"""
master_chainage
  10 m reference spine per lane with road info (no defect values, no breach).
  Range per lane = D4 chainage range, stretched to the D1 pavement extent of that direction.
Run alone:  python tables/master_chainage.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.utils import lane_dir
from common.road import dir_mask
from common.output import run_table, standalone

TABLE = 'master_chainage'


def build(prj):
    d4 = prj.d4_long
    lb = d4.groupby('lane')['start_chainage'].agg(['min', 'max'])
    pav = prj.road.pavement
    if not pav.empty:
        for direction, prefix in [('Increasing', 'L'), ('Decreasing', 'R')]:
            pv = pav[dir_mask(pav, direction)]
            for ln in [l for l in lb.index if l.startswith(prefix)]:
                lb.loc[ln, 'min'] = min(lb.loc[ln, 'min'], pv['start'].min())
                lb.loc[ln, 'max'] = max(lb.loc[ln, 'max'], pv['end'].max() - 10)
    sp = pd.concat([pd.DataFrame({'lane': l, 'start_chainage': np.arange(int(r['min']) // 10 * 10,
                                                                         int(r['max']) // 10 * 10 + 10, 10)})
                    for l, r in lb.iterrows()], ignore_index=True)
    ctx = prj.road.lookup(sp['start_chainage'].values, sp['lane'].values)
    return pd.DataFrame({
        'upc': prj.upc, 'upc_code': prj.upc, 'source_system': config.SOURCE_SYSTEM,
        'defect_detected_on': prj.tracker_ctx['survey_end_date'],
        'start_chainage': sp['start_chainage'].astype(float), 'end_chainage': (sp['start_chainage'] + 10).astype(float),
        'phase': prj.phase, 'cycle': int(prj.cycle), 'zone': prj.zone, 'lane': sp['lane'],
        'direction': [lane_dir(l) for l in sp['lane']], 'nh_number': ctx['nh_number'].values,
        'section_code': ctx['section_code'].values, 'pavement_type': ctx['pavement_type'].values,
        'carriageway': ctx['carriageway'].values})


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, breach_copy=False)


if __name__ == '__main__':
    standalone(run)
