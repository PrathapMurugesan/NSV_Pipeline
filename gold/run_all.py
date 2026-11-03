"""Driver: runs the KPI pipelines in order, each as its own process from its own folder
(every folder has its own utils package and .env).

    python run_all.py                              # ris_trends -> intermediate -> ris_view
    python run_all.py --only intermediate ris_view
    python run_all.py --keep-going                 # continue after a failure (dependents still skipped)

Stops at the first failure, prints the error and a summary, and exits with code 1.
"""
import argparse
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# name -> (folder, steps it depends on); run in this order
PIPELINES = {
    "ris_trends": ("code_ris_trends", []),
    "intermediate": ("code_intermediate_logic", []),
    "ris_view": ("code_ris_view", ["intermediate"]),  # reads final_intermediate.csv
}


def run_step(name, folder):
    """Run <folder>/main.py, streaming its output; returns (ok, seconds, last error line)."""
    print(f"\n{'=' * 25} {name} ({folder}) {'=' * 25}", flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    start = time.time()
    tail = deque(maxlen=40)
    proc = subprocess.Popen(
        [sys.executable, "main.py"], cwd=ROOT / folder, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        print(line, end="", flush=True)
        tail.append(line.rstrip())
    proc.wait()
    seconds = time.time() - start

    if proc.returncode == 0:
        return True, seconds, ""
    # Last non-empty line of a traceback is the exception, e.g. "KeyError: 'UPC_code'"
    error = next((l for l in reversed(tail) if l.strip()), f"exit code {proc.returncode}")
    print(f"\n!!! {name} FAILED (exit code {proc.returncode}): {error}", flush=True)
    return False, seconds, error


def main():
    # Pipeline logs contain characters like '—' / '→' that the Windows console codepage cannot print
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser =argparse.ArgumentParser(description="Run the RIS KPI pipelines in order.")
    parser.add_argument("--only", nargs="+", choices=list(PIPELINES), help="run only these pipelines")
    parser.add_argument("--keep-going", action="store_true",
                        help="continue with independent pipelines after a failure")
    args = parser.parse_args()

    selected = [n for n in PIPELINES if not args.only or n in args.only]
    results = {}  # name -> (status, seconds, error)

    for name in selected:
        folder, deps = PIPELINES[name]
        failed_deps = [d for d in deps if results.get(d, ("OK",))[0] != "OK"]
        stopped = any(r[0] == "FAILED" for r in results.values()) and not args.keep_going
        if stopped or failed_deps:
            reason = f"{', '.join(failed_deps)} did not succeed" if failed_deps else "stopped after failure"
            results[name] = ("SKIPPED", 0.0, reason)
            continue
        ok, seconds, error = run_step(name, folder)
        results[name] = ("OK" if ok else "FAILED", seconds, error)

    print(f"\n{'=' * 25} summary {'=' * 25}")
    for name, (status, seconds, error) in results.items():
        detail = f"  -> {error}" if error else ""
        print(f"{name:<14} {status:<8} {seconds:6.1f}s{detail}")

    sys.exit(0 if all(r[0] == "OK" for r in results.values()) else 1)


if __name__ == "__main__":
    main()
