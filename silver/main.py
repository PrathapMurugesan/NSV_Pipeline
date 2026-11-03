"""
Run the NSV silver layer, table by table, in the correct order, then the DQ checks against D2.

ONE project (paths from config.py, or --project):
    python main.py
    python main.py --no-excel
    python main.py --tables master_rutting_child_10m,master_pothole

MANY projects (every UPC folder under --root that has d1..d4 sub-folders):
    python main.py --root "D:\\NSV\\New_Structure_Data" --no-excel
    python main.py --root "D:\\...\\bronze\\nsv" --upc RJ --no-excel        (only UPCs containing 'RJ')

Every UPC gets  <output>\\...\\<UPC>\\run.log  (everything printed, including errors)
A batch gets    <OUTPUT_ROOT>\\_batches\\batch=<time>.csv  (one row per UPC × table + DQ result)  ← start here
"""
import contextlib
import os
import sys
import time
import traceback
from datetime import datetime

import pandas as pd

import config
from common.output import parse_args, make_project
from tables import (master_rutting_child_10m, master_rutting_child_100m, master_rutting_parent,
                    master_roughness_child_10m, master_roughness_child_100m, master_roughness_parent,
                    master_ravelling_child_10m, master_ravelling_child_100m, master_ravelling_parent,
                    master_cracking, master_cracking_child_100m, master_cracking_parent,
                    master_pothole, master_chainage, master_reporting_interval, dlm_threshold_mapping)
from validation import dq_checks, rule_checks

# Order: 10 m → 100 m → 1 km per defect, then cracking, potholes, chainage spine.
PIPELINE = [
    master_rutting_child_10m, master_rutting_child_100m, master_rutting_parent,
    master_roughness_child_10m, master_roughness_child_100m, master_roughness_parent,
    master_ravelling_child_10m, master_ravelling_child_100m, master_ravelling_parent,
    master_cracking, master_cracking_child_100m, master_cracking_parent,
    master_pothole,
    master_chainage,
    master_reporting_interval,
    dlm_threshold_mapping,
]


class _Tee:
    """Print to the console AND the UPC's run.log."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def find_projects(root, upc_filter=None):
    """Every folder under root that has d1..d4 sub-folders (= one UPC)."""
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if os.path.basename(dirpath).lower() in ('silver', 'dlm_kpi', 'dlm_postgres', '_batches'):
            dirnames[:] = []
            continue
        if any(d.lower() in ('d1', 'd2', 'd4') for d in dirnames):          # D3 is not used
            if not upc_filter or upc_filter.lower() in os.path.basename(dirpath).lower():
                found.append(dirpath)
            dirnames[:] = []                      # do not go inside d1..d4
    return sorted(found)


def run_project(args, steps, project_dir=None):
    """Run every table + DQ for one UPC. Returns rows for the batch report."""
    if project_dir:
        args.project = project_dir
    prj = make_project(args)
    os.makedirs(prj.out_dir, exist_ok=True)
    report, t0 = [], time.time()
    with open(os.path.join(prj.out_dir, 'run.log'), 'w', encoding='utf-8') as log, \
            contextlib.redirect_stdout(_Tee(sys.__stdout__, log)):
        print(f"Run {datetime.now():%Y-%m-%d %H:%M:%S}")
        prj.describe()
        try:
            print("Loading deliverables:")
            prj.d4_long
            prj.tracker_ctx
            prj.print_thresholds()
            print(f"  Survey date: {prj.survey_ts} (defect_id date {prj.survey_date_str})")
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            report.append({'upc': prj.upc, 'table': '(load)', 'status': 'FAILED', 'rows': 0, 'breach': 0,
                           'error': f"{type(e).__name__}: {e}"})
            return report

        results = {}
        for mod in steps:
            try:
                df = mod.run(prj)
                results[mod.TABLE] = df
                report.append({'upc': prj.upc, 'table': mod.TABLE, 'status': 'ok' if len(df) else 'empty',
                               'rows': len(df), 'breach': int(df['breach_flag'].sum()) if 'breach_flag' in df else None,
                               'error': ''})
            except Exception as e:
                print(f"  ✖ {mod.TABLE} FAILED")
                traceback.print_exc(file=sys.stdout)
                report.append({'upc': prj.upc, 'table': mod.TABLE, 'status': 'FAILED', 'rows': 0, 'breach': None,
                               'error': f"{type(e).__name__}: {e}"})

        # display_defect_id: hierarchical ids over all tables of the UPC (DLM notebook post-process)
        try:
            from common.display_ids import assign_display_ids
            from common.output import write_table
            for t, df in assign_display_ids(prj, results).items():
                write_table(df, t, prj)
                if t in results:
                    results[t] = df
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            report.append({'upc': prj.upc, 'table': '(display_defect_id)', 'status': 'FAILED', 'rows': 0,
                           'breach': None, 'error': f"{type(e).__name__}: {e}"})

        print("\nSources used:")
        for k, v in prj.sources_used.items():
            print(f"  {k}: {v}")

        try:
            dq = dq_checks.run_dq(prj, results)
            judged = dq[dq['status'].isin(['PASS', 'FAIL'])] if len(dq) else dq
            if len(judged):
                n_pass = int((judged['status'] == 'PASS').sum())
                report.append({'upc': prj.upc, 'table': '(DQ vs D2)',
                               'status': 'PASS' if n_pass == len(judged) else 'FAIL',
                               'rows': len(judged), 'breach': None, 'error': f"{n_pass}/{len(judged)} checks pass"})
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            report.append({'upc': prj.upc, 'table': '(DQ vs D2)', 'status': 'FAILED', 'rows': 0, 'breach': None,
                           'error': f"{type(e).__name__}: {e}"})
        try:
            rc = rule_checks.run_rule_checks(prj, results)
            if len(rc):
                n_ok = int((rc['status'] == 'PASS').sum())
                report.append({'upc': prj.upc, 'table': '(rule checks)', 'status': 'PASS' if n_ok == len(rc) else 'FAIL',
                               'rows': len(rc), 'breach': None, 'error': f"{n_ok}/{len(rc)} checks pass"})
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            report.append({'upc': prj.upc, 'table': '(rule checks)', 'status': 'FAILED', 'rows': 0, 'breach': None,
                           'error': f"{type(e).__name__}: {e}"})
        print(f"\nDone {prj.upc} in {time.time() - t0:.0f}s → {prj.out_dir}")
    return report


def main():
    def extra(ap):
        ap.add_argument('--tables', help='Comma-separated table names to run (default: all)')
        ap.add_argument('--root', help='Run every UPC folder found under this folder')
        ap.add_argument('--upc', help='With --root: only UPC folders whose name contains this text')
    args = parse_args(extra)

    wanted = {t.strip() for t in args.tables.split(',')} if args.tables else None
    steps = [m for m in PIPELINE if not wanted or m.TABLE in wanted]
    if wanted and len(steps) != len(wanted):
        sys.exit(f"Unknown table(s): {', '.join(sorted(wanted - {m.TABLE for m in PIPELINE}))}")

    projects = find_projects(args.root, args.upc) if args.root else [None]
    if args.root:
        print(f"Found {len(projects)} UPC folder(s) under {args.root}")
        if not projects:
            sys.exit(2)

    report = []
    for i, pdir in enumerate(projects, 1):
        if args.root:
            print(f"\n{'═' * 80}\n[{i}/{len(projects)}] {pdir}\n{'═' * 80}")
        try:
            report += run_project(args, steps, pdir)
        except Exception as e:                       # never stop the batch
            traceback.print_exc()
            report.append({'upc': os.path.basename(pdir or config.PROJECT_DIR), 'table': '(run)',
                           'status': 'FAILED', 'rows': 0, 'breach': None, 'error': f"{type(e).__name__}: {e}"})

    rep = pd.DataFrame(report)
    out_root = args.out or config.OUTPUT_ROOT
    os.makedirs(os.path.join(out_root, '_batches'), exist_ok=True)
    path = os.path.join(out_root, '_batches', f"batch={datetime.now():%Y%m%d_%H%M%S}.csv")
    rep.to_csv(path, index=False)

    bad = rep[rep['status'].isin(['FAILED', 'FAIL'])]
    print(f"\n{'═' * 80}\nBatch report: {path}")
    print(f"UPCs: {rep['upc'].nunique()} | table runs FAILED: {int((rep['status'] == 'FAILED').sum())} | "
          f"DQ FAIL: {int(((rep['table'] == '(DQ vs D2)') & (rep['status'] == 'FAIL')).sum())}")
    if len(bad):
        print(bad.to_string(index=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
