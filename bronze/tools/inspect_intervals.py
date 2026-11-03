"""
inspect_intervals.py - everything about ONE UPC's reporting intervals in one text file, to find why the master
table does not map onto the silver data. Reads only, writes nothing to S3.

    python tools/inspect_intervals.py --upc N/02001/19004/DL --phase 2 --cycle 2
    python tools/inspect_intervals.py --upc N/02001/19004/DL --silver s3://dhms-new-stgae/nsv-silver

Output: inspect_<UPC>.txt in the current folder (also printed). Sections:
    1. bronze d1 / d2 runs found (all zones / phases / cycles of this UPC)
    2. d2 manifest: workbook file, every sheet and its status
    3. 'Inputs (Thresholds)' sheet as a grid + the intervals read from it
    4. comparison_ca / comparison_irc tables: columns, 'Reporting Interval' values
    5. the UPC's row(s) in master/reporting_intervals
    6. silver: files under <silver>/zone=*/phase=*/cycle=*/<UPC>/, columns + first rows
"""
import argparse
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # allows: python tools/inspect_intervals.py

import pandas as pd

import settings
from nsv.intervals import from_tables, from_thresholds, read_master
from nsv.storage import LocalStorage, S3Storage

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)
pd.set_option("display.max_colwidth", 60)
OUT = []


def say(text=""):
    print(text)
    OUT.append(str(text))


def section(title):
    say()
    say("=" * 100)
    say(title)
    say("=" * 100)


def storage_for(location):
    if location.startswith("s3://"):
        bucket, _, prefix = location[len("s3://"):].partition("/")
        return S3Storage(bucket=bucket, prefix=prefix)
    return LocalStorage(Path(location))


def read_table(storage, path):
    data = storage.read_bytes(path)
    if path.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(data))
    if path.endswith(".csv"):
        return pd.read_csv(io.BytesIO(data), dtype=str, encoding="utf-8-sig")
    return None


def show_table(storage, path, rows=5):
    try:
        df = read_table(storage, path)
    except Exception as e:
        say(f"  could not read: {e}")
        return None
    if df is None:
        return None
    say(f"  {len(df)} rows x {len(df.columns)} columns")
    say(f"  columns: {list(df.columns)}")
    say(df.head(rows).to_string())
    return df


def main(args):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upc", required=True)
    parser.add_argument("--zone")
    parser.add_argument("--phase")
    parser.add_argument("--cycle")
    parser.add_argument("--silver", default="s3://dhms-new-stgae/nsv-silver", help="silver root (s3:// or folder)")
    o = parser.parse_args(args)
    upc_folder = re.sub(r"[/_\s]+", "-", o.upc.strip()).upper()
    out = settings.OUTPUT
    say(f"UPC {o.upc} (folder {upc_folder})   bronze: {out.describe()}   silver: {o.silver}")

    # ---- 1. bronze runs of this UPC ----
    section("1. BRONZE: d1 / d2 of this UPC (any zone / phase / cycle)")
    files = [f for f in out.list_files("bronze/nsv") if f"/{upc_folder}/" in f]
    pattern = re.compile(rf"zone=([^/]+)/phase=([^/]+)/cycle=([^/]+)/{re.escape(upc_folder)}/(d\d)/(d\d)_latest\.json$")
    latest = sorted({m.groups()[:4] for f in files if (m := pattern.search(f))})
    if not latest:
        say("  NO d1 / d2 extraction of this UPC in the bronze output")
    for zone, phase, cycle, d in latest:
        mark = "" if (not o.zone or zone == o.zone) and (not o.phase or phase == o.phase) \
            and (not o.cycle or cycle == o.cycle) else "   (other zone/phase/cycle than asked)"
        say(f"  zone={zone} phase={phase} cycle={cycle} {d}{mark}")
    runs = sorted({f.split("/run=")[0] + "/run=" + f.split("/run=")[1].split("/")[0] for f in files if "/run=" in f})
    say(f"  run folders ({len(runs)}):")
    for r in runs:
        say(f"    {r}")

    for zone, phase, cycle, d in latest:
        if d != "d2":
            continue
        base = f"bronze/nsv/zone={zone}/phase={phase}/cycle={cycle}/{upc_folder}/d2"
        pointer = json.loads(out.read_bytes(f"{base}/d2_latest.json"))
        run_files = out.list_files(pointer["folder"])
        section(f"2. D2 MANIFEST  zone={zone} phase={phase} cycle={cycle}  run {pointer['run_id']} ({pointer['status']})")
        for f in run_files:
            say(f"  {f.rsplit('/', 1)[-1]}")
        manifest = next((f for f in run_files if f.endswith("d2_manifest.json")), None)
        if manifest:
            m = json.loads(out.read_bytes(manifest))
            say(f"  settings: {m.get('settings')}")
            for wb_key, wb in m.get("workbooks", {}).items():
                say(f"  workbook '{wb_key}': status={wb.get('status')} file={wb.get('file')} "
                    f"identified_by={wb.get('identified_by')} variants={wb.get('variants')}")
                for sheet_key, s in wb.get("sheets", {}).items():
                    say(f"      {sheet_key:28} {str(s.get('status')):22} sheet='{s.get('sheet_name')}' "
                        f"rows={s.get('rows')} interval_m={s.get('interval_m')} "
                        f"missing={(s.get('header_check') or {}).get('missing') if isinstance(s.get('header_check'), dict) else ''}")
            say(f"  unmatched / unexpected files: {m.get('unexpected_files')}")

        section(f"3. 'INPUTS (THRESHOLDS)' SHEET  zone={zone} phase={phase} cycle={cycle}")
        cells_files = [f for f in run_files if "thresholds" in f.lower() and f.endswith("_cells.json")]
        if not cells_files:
            say("  NOT SAVED - no *inputs_thresholds*_cells.json in this run (sheet missing in the workbook?)")
        for path in cells_files:
            say(f"  file: {path.rsplit('/', 1)[-1]}")
            cells = json.loads(out.read_bytes(path))
            grid = {}
            for c in cells.get("cells", []):
                mm = re.match(r"([A-Z]+)(\d+)$", str(c.get("cell", "")))
                if mm:
                    grid.setdefault(int(mm[2]), []).append(f"{mm[1]}={c.get('value')}")
            for row in sorted(grid):
                say(f"    row {row:3}: {' | '.join(grid[row])}")
            say(f"  intervals read from it: {from_thresholds(cells)}")

        section(f"4. COMPARISON TABLES  zone={zone} phase={phase} cycle={cycle}")
        tables = [f for f in run_files if f.endswith(".parquet") and "comparison" in f.rsplit("/", 1)[-1]]
        if not tables:
            say("  no comparison table saved")
        for path in tables:
            say(f"  --- {path.rsplit('/', 1)[-1]}")
            try:
                df = read_table(out, path)
            except Exception as e:
                say(f"  could not read: {e}")
                continue
            say(f"  {len(df)} rows; columns: {list(df.columns)}")
            for col in [c for c in df.columns if "interval" in c.lower()]:
                say(f"    '{col}': {df[col].value_counts(dropna=False).head(6).to_dict()}")
        say(f"  intervals read from the tables: {from_tables(out, run_files)}")

    # ---- 5. master table ----
    section("5. MASTER TABLE master/reporting_intervals")
    master = read_master(out)
    say(f"  {len(master)} rows in total; UPC_code examples: {master['UPC_code'].head(3).tolist() if len(master) else []}")
    rows = master[master["UPC_code"].astype(str).str.replace("/", "-").str.upper() == upc_folder] if len(master) else master
    say(rows.T.to_string() if len(rows) else "  this UPC is NOT in the master table")
    say(f"  column types: {dict(master.dtypes.astype(str))}")

    # ---- 6. silver ----
    section(f"6. SILVER {o.silver}")
    places = {(z, p, c) for z, p, c, _ in latest}
    if o.zone and o.phase and o.cycle:
        places.add((o.zone, o.phase, o.cycle))
    try:
        silver = storage_for(o.silver)
        found = [f for z, p, c in sorted(places) for f in silver.list_files(f"zone={z}/phase={p}/cycle={c}/{upc_folder}")]
        if not found:                                         # not where bronze is: look everywhere
            say("  not under the bronze zone/phase/cycle - searching all of silver")
            found = [f for f in silver.list_files("") if f"/{upc_folder}/" in f]
    except Exception as e:
        found = []
        say(f"  could not list silver: {e}")
    say(f"  {len(found)} file(s) for this UPC")
    for f in found[:60]:
        say(f"    {f}")
    shown = set()
    for f in found:
        folder = f.rsplit("/", 1)[0]
        if folder in shown or not f.endswith((".parquet", ".csv")):
            continue
        shown.add(folder)
        say(f"  --- {f}")
        show_table(silver, f)
        if len(shown) >= 8:
            break

    name = f"inspect_{upc_folder}.txt"
    Path(name).write_text("\n".join(OUT), encoding="utf-8")
    print(f"\nsaved: {Path(name).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
