"""
run_status_csv.py - run run_download.py for every UPC in the download status CSV that has
New Structure = 1, up to 3 UPCs at the same time.

The CSV has the columns  Zone, Phase, Cycle, UPC code ..., Status, New Structure, ...
("Phase  3", "Cycle 1", "N/02007/11001/HR" are fine - only the number / code is used).

    python run_status_csv.py --csv "Downloading_status(UPC Download Status).csv" --root "$SRC"
    python run_status_csv.py --csv s3://bucket/path/status.csv --root "$SRC"      CSV on S3
    python run_status_csv.py --csv status.csv --root "$SRC" --parallel 2         2 at a time
    python run_status_csv.py --csv status.csv --root "$SRC" --list               only show the UPCs
    python run_status_csv.py --csv status.csv --root "$SRC" --status Downloaded  only Status = Downloaded too
    python run_status_csv.py --csv status.csv --root "$SRC" d1 d2                any run_download option
    python run_status_csv.py --csv status.csv --root "$SRC" --resume logs/status_20261027_073821
                                        skip the UPCs that finished in that earlier run (stopped ones run again)

One log per UPC:   logs/status_<timestamp>/<zone>_<phase>_<cycle>_<UPC>.log
Summary:           logs/status_<timestamp>/summary.csv   (UPC, status, exit code, log file)
                   status = extracted / extracted with problems / UPC not found in S3 / error
With NSV_OUTPUT=s3://bucket/prefix the folder is also copied to s3://bucket/prefix/logs/status_<timestamp>/
At the end the UPCs are added to the master table  master/reporting_intervals.parquet / .csv  (nsv/intervals.py)
"""
import argparse
import csv
import io
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def read_csv_text(path):
    if path.startswith("s3://"):
        import boto3
        bucket, _, key = path[len("s3://"):].partition("/")
        data = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    else:
        data = Path(path).read_bytes()
    return data.decode("utf-8-sig", errors="replace")


def surveys_from_csv(path, status=None):
    """[(zone, phase, cycle, upc)] of the rows with New Structure = 1, duplicates removed.
    status (e.g. 'Downloaded'): only rows whose Status column is that value too (case / spaces ignored)."""
    reader = csv.DictReader(io.StringIO(read_csv_text(path)))
    columns = {c.strip().lower(): c for c in reader.fieldnames or []}
    upc_col = next((c for k, c in columns.items() if k.startswith("upc")), None)
    new_col = columns.get("new structure")
    status_col = columns.get("status")
    missing = [n for n, c in (("Zone", columns.get("zone")), ("Phase", columns.get("phase")),
                              ("Cycle", columns.get("cycle")), ("UPC ...", upc_col),
                              ("New Structure", new_col),
                              ("Status", status_col if status else "")) if c is None]
    if missing:
        sys.exit(f"column(s) {missing} not found in {path} - columns are {reader.fieldnames}")
    out = []
    for row in reader:
        if (row.get(new_col) or "").strip() not in ("1", "1.0"):
            continue
        if status and (row.get(status_col) or "").strip().lower() != status.strip().lower():
            continue
        zone = (row[columns["zone"]] or "").strip().upper()
        phase = re.findall(r"\d+", row[columns["phase"]] or "")
        cycle = re.findall(r"\d+", row[columns["cycle"]] or "")
        upc = (row[upc_col] or "").strip()
        if not (zone and phase and cycle and upc):
            print(f"skipped (incomplete row): {row}")
            continue
        survey = (zone, phase[0], cycle[0], upc)
        if survey not in out:
            out.append(survey)
    return out


def log_name(survey):
    zone, phase, cycle, upc = survey
    return f"{zone}_{phase}_{cycle}_{upc.replace('/', '_')}.log"


def finished(log_path):
    """True when an earlier run of this UPC ran to its end (not stopped / killed half way)."""
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    return "done, exit code" in text or "nothing to run" in text


def run_one(survey, root, extra, log_dir, image_workers):
    zone, phase, cycle, upc = survey
    log_path = log_dir / log_name(survey)
    command = [sys.executable, str(PROJECT_ROOT / "run_download.py"), *extra, "--root", root,
               "--upc", upc, "--zone", zone, "--phase", phase, "--cycle", cycle]
    env = {**os.environ, "NSV_IMAGE_WORKERS": os.environ.get("NSV_IMAGE_WORKERS", str(image_workers)),
           "NSV_MASTER_INTERVALS": "0"}        # parallel runs don't write the master table, main() does at the end
    with open(log_path, "w", encoding="utf-8") as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=PROJECT_ROOT, env=env).returncode
    return code, log_path


def status_of(code, log_path):
    """run_download exit code (+ its log) -> words for the summary."""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace") if log_path else ""
    moved = " - found under another zone/phase/cycle than in the CSV" if "--use-found: extracting" in text else ""
    if code == 0:
        return "extracted" + moved
    if code == 2:
        return "extracted with problems (see log: REJECTED / missing / crashed)" + moved
    if "is in the download only under" in text:
        return "UPC in S3 under another zone/phase/cycle than in the CSV (see log)"
    if "not found in" in text or "nothing to run" in text:
        return "UPC not found in S3"
    return "error (see log)"


def upload_logs(log_dir):
    """Copy the log folder to NSV_OUTPUT/logs/<folder>/ when NSV_OUTPUT is s3://bucket/prefix."""
    output = os.environ.get("NSV_OUTPUT", "")
    if not output.startswith("s3://"):
        return None
    import boto3
    bucket, _, prefix = output[len("s3://"):].partition("/")
    target = f"{prefix.strip('/')}/logs/{log_dir.name}".lstrip("/")
    s3 = boto3.client("s3")
    for f in sorted(log_dir.iterdir()):
        s3.upload_file(str(f), bucket, f"{target}/{f.name}")
    return f"s3://{bucket}/{target}/"


def update_intervals(surveys):
    """Once, after all UPCs: add their rows to master/reporting_intervals (nsv/intervals.py).
    Matched by UPC only, so a UPC extracted under another zone/phase/cycle (--use-found) is found too."""
    if os.environ.get("NSV_MASTER_INTERVALS", "").lower() in ("0", "false", "no"):
        return
    try:
        import settings
        from nsv.intervals import extracted_surveys, update_master
        upcs = {upc.replace("/", "-").upper() for _, _, _, upc in surveys}
        update_master([s for s in extracted_surveys(settings.OUTPUT) if s[3].upper() in upcs])
    except Exception as e:
        print(f"master table of reporting intervals not updated: {e}")


def main(args):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="download status CSV, local path or s3://bucket/key")
    parser.add_argument("--root", required=True, help="download root, local folder or s3://bucket/prefix/")
    parser.add_argument("--parallel", type=int, default=3, help="UPCs at the same time (default 3)")
    parser.add_argument("--list", action="store_true", help="only show the UPCs, run nothing")
    parser.add_argument("--status", help="only rows with this Status too, e.g. --status Downloaded")
    parser.add_argument("--resume", help="earlier log folder (logs/status_...): skip the UPCs that finished there")
    parser.add_argument("--strict-location", action="store_true",
                        help="a UPC in S3 under another zone/phase/cycle than in the CSV is NOT extracted "
                             "(default: it is extracted where it is, and the summary says so)")
    o, extra = parser.parse_known_args(args)
    if not o.strict_location:
        extra = [*extra, "--use-found"]

    surveys = all_surveys = surveys_from_csv(o.csv, o.status)
    print(f"{len(surveys)} UPC(s) with New Structure = 1{f' and Status = {o.status}' if o.status else ''} in {o.csv}")
    if o.resume:
        done = [s for s in surveys if finished(Path(o.resume) / log_name(s))]
        surveys = [s for s in surveys if s not in done]
        print(f"--resume {o.resume}: {len(done)} already finished there, skipped - {len(surveys)} to run")
    for zone, phase, cycle, upc in surveys:
        print(f"  zone {zone}  phase {phase}  cycle {cycle}  {upc}")
    if o.list or not surveys:
        return 0

    log_dir = PROJECT_ROOT / "logs" / f"status_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    results = []
    parallel = max(1, o.parallel)
    image_workers = max(1, (os.cpu_count() or 1) // parallel)       # the cores are shared by the parallel UPCs
    image_workers = int(os.environ.get("NSV_IMAGE_WORKERS") or image_workers)   # export NSV_IMAGE_WORKERS wins
    print(f"{parallel} UPC(s) at a time, {image_workers} image worker(s) each")
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        jobs = {pool.submit(run_one, s, o.root, extra, log_dir, image_workers): s for s in surveys}
        for number, job in enumerate(as_completed(jobs), start=1):
            zone, phase, cycle, upc = jobs[job]
            try:
                code, log_path = job.result()
            except Exception as e:
                code, log_path = f"crashed: {e}", ""
            status = status_of(code, log_path)
            print(f"[{number}/{len(surveys)}] {zone}/{phase}/{cycle} {upc:20} {status}  {log_path}")
            results.append([zone, phase, cycle, upc, status, code, Path(log_path).name if log_path else ""])

    summary = log_dir / "summary.csv"
    with open(summary, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zone", "phase", "cycle", "upc", "status", "exit_code", "log"])
        w.writerows(sorted(results, key=lambda r: r[:4]))

    counts = {}
    for r in results:
        counts[r[4]] = counts.get(r[4], 0) + 1
    print(f"################ {len(results)} UPC(s) ################")
    for status, n in sorted(counts.items()):
        print(f"  {n:4}  {status}")
    for r in sorted(results, key=lambda r: r[:4]):
        if r[4] != "extracted":
            print(f"  {r[0]}/{r[1]}/{r[2]} {r[3]:20} {r[4]}")
    print(f"summary: {summary}")
    update_intervals(all_surveys)        # with --resume too: the UPCs of the stopped run are added as well
    try:
        where = upload_logs(log_dir)
        if where:
            print(f"logs + summary copied to {where}")
    except Exception as e:
        print(f"could not copy the logs to S3: {e}")
    return 0 if counts.get("extracted", 0) == len(results) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
