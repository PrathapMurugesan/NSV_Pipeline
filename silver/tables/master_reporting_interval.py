"""
master_reporting_interval
  Reporting interval (m) of the project, from Deliverable 2 — CURRENT CYCLE only (Previous Cycle columns never used).
  One row per D2 comparison sheet that exists:
      'Comparison as per CA'  → standard = 'CA'
      'Comparison as per IRC' → standard = 'IRC'
  (both sheets → 2 rows; a missing sheet still gets its row when D2 'Inputs (Thresholds)' has its intervals;
   nothing for a standard → no row).

  overall_reporting_interval   ← 'Overall Reporting Interval'
  roughness_reporting_interval ← 'Current Cycle | Roughness BI | Reporting Interval'
  rutting_reporting_interval   ← 'Current Cycle | Rut Depth | Reporting Interval'
  cracking_reporting_interval  ← 'Current Cycle | Cracking Lane Area | Reporting Interval'
  ravelling_reporting_interval ← 'Current Cycle | Ravel… | Reporting Interval' (NULL when D2 has no ravelling column)

  D2 writes the interval on every row of its block; the last block of a road is usually shorter (60 m, 43 m …).
  So the value written here is the MOST COMMON value of that column; a column with no value → NULL.
  The value comes from Project.d2_interval — the same function every silver table uses for reporting_interval_m.
Run alone:  python tables/master_reporting_interval.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from common.output import run_table, standalone

TABLE = 'master_reporting_interval'

# output column → words that must ALL be in the (normalised) D2 column name, current cycle only
DEFECT_INTERVAL_COLS = {
    'roughness_reporting_interval': ('roughness',),
    'rutting_reporting_interval': ('rut',),
    'cracking_reporting_interval': ('crack',),
    'ravelling_reporting_interval': ('ravel',),
}


def build(prj):
    rows = []
    for std, d2 in [('CA', prj.d2_ca), ('IRC', prj.d2_irc)]:
        source = f'Comparison as per {std}'
        if d2 is None or d2.empty:
            # no comparison sheet: the D2 'Inputs (Thresholds)' intervals still give this standard a row
            if not any((std.lower(), d) in prj._thr_info['intervals'] for d in ('roughness', 'rutting', 'cracking', 'ravelling')):
                print(f"  D2 'Comparison as per {std}': not found, no {std} interval in Inputs (Thresholds) → no {std} row")
                continue
            source = 'Inputs (Thresholds)'
            print(f"  D2 'Comparison as per {std}': not found → {std} intervals from D2 Inputs (Thresholds)")
        r = {'upc': prj.upc_slash, 'upc_code': prj.upc, 'zone': prj.zone, 'phase': prj.phase, 'cycle': int(prj.cycle),
             'standard': std, 'source_sheet': source,
             'overall_reporting_interval': prj.d2_interval(std, 'overall')}
        for col in DEFECT_INTERVAL_COLS:                      # same function every table uses (Project.d2_interval)
            r[col] = prj.d2_interval(std, col.replace('_reporting_interval', ''))
        rows.append(r)
        print(f"  {std}: overall {r['overall_reporting_interval']}, roughness {r['roughness_reporting_interval']}, "
              f"rutting {r['rutting_reporting_interval']}, cracking {r['cracking_reporting_interval']}, "
              f"ravelling {r['ravelling_reporting_interval']}")
    cols = ['upc', 'upc_code', 'zone', 'phase', 'cycle', 'standard', 'source_sheet', 'overall_reporting_interval',
            *DEFECT_INTERVAL_COLS]
    out = pd.DataFrame(rows, columns=cols)
    for c in ['overall_reporting_interval', *DEFECT_INTERVAL_COLS]:      # always numeric (blank → NULL)
        out[c] = pd.to_numeric(out[c], errors='coerce').astype('float64')
    return out


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, breach_copy=False)


if __name__ == '__main__':
    standalone(run)
