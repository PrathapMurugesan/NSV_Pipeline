"""
master_pothole
  Source: ONLY the D4 NHAI One pothole sheet → one row per pothole (chainage = FROM_CH_KM × 1000 + FROM_CH_M).
  No defect-report fallback. threshold_value NULL (no pothole threshold in D2; breach comes from severity).
  rating_ca / rating_irc: D4 defect report 'Visual Condition - Pothole (CA)' / '(IRC)', same lane + 10 m segment.
  No CA/IRC twin. parent_* = its 1 km block (DLM notebook).
  breach_flag = 0 when SEVERITY is in config.POTHOLE_NO_BREACH_SEVERITY (Low, Delamination, Very Low, None), else 1.
  road_condition = Bad / Good from breach_flag only (set in finalize).
  affected_length(_ca/_irc) and total_length = the pothole area as read (not tied to breach_flag).
Run alone:  python tables/master_pothole.py [--no-excel]
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.utils import standardize_nh
from common.defects import pothole_rows
from common.segments import common_cols, parent_cols
from common.output import run_table, standalone

TABLE = 'master_pothole'
LEVEL = 'child_10m'


def build(prj):
    p = pothole_rows(prj)
    if p.empty:
        print("  pothole: no D4 NHAI One pothole sheet / rows → master_pothole empty")
        return pd.DataFrame()
    print(f"  pothole source: {p['source'].iloc[0]}")
    seq = np.arange(1, len(p) + 1)
    start, end, lane, area = p['start'], p['end'], p['lane'], p['area']
    ctx = prj.road.lookup(start.values, lane.values)
    std_def = prj.tracker_ctx['defect_standards']['pothole']['standard']
    raw_start = prj.tracker_ctx['survey_start_date']
    fallback_ts = pd.Timestamp(raw_start) if raw_start else None
    a0 = area.fillna(0.0)
    # breach: from SEVERITY only — 0 for the 'no breach' severities (Low, Delamination …), else 1
    words = [w.lower() for w in config.POTHOLE_NO_BREACH_SEVERITY]
    sev_t = pd.Series(p['severity'], dtype=object).fillna('').astype(str).str.strip().str.lower()
    bf = np.where(sev_t.apply(lambda v: any(v.startswith(w) for w in words)).values, 0, 1)

    o = pd.DataFrame({'id': [f"{prj.upc}_{TABLE}_{q:06d}" for q in seq]})
    o['defect_id'] = [f"D/{prj.survey_date_str}/N/{prj.upc}/{int(s)}/Pothole/{q:04d}" for s, q in zip(start, seq)]
    o['display_defect_id'] = o['defect_id']
    o['source_defect_id'] = o['record_id'] = [f"pothole_{q:06d}" for q in seq]
    parent_cols(o, prj, lane, start, LEVEL)
    o['nh_number'] = ctx['nh_number'].map(standardize_nh).values
    o['defect_type'] = 'Pothole'
    o['type_of_defect'] = p['type'].values
    o['defect_type_filter'] = 'Pothole'
    o['category'] = 'POINT'
    o['severity'] = p['severity'].values
    o['lane'] = lane.values
    o['breach_flag'] = bf
    o['metric_name'] = 'area_sq_m'
    o['metric_value'] = area.values
    o['area_sqm'] = area.values
    o['threshold_name'] = prj.threshold_name_for('pothole')
    o['threshold_value'] = None              # no pothole threshold in D2 (breach_flag comes from severity)
    o['total_children'] = 1
    o['segment_count'] = 1
    o['length_m'] = np.maximum(end - start, 0).values
    o['start_chainage'] = start.values
    o['end_chainage'] = end.values
    o['defect_chainage'] = start.values
    for k in ['start', 'defect']:
        o[f'{k}_lat'] = p['lat'].values
        o[f'{k}_long'] = p['lon'].values
    o['end_lat'] = p['end_lat'].values
    o['end_long'] = p['end_lon'].values
    sdt = p['datetime']
    o['datetime'] = sdt.where(sdt.notna(), fallback_ts).values
    o['observed_at'] = o['datetime']
    # all distinct images of the potholes on the same lane + chainage (metre), comma separated
    key = pd.Series(list(zip(p['lane'], p['start'].astype('int64'))))
    imgs = pd.Series(p['image'].values, dtype=object)
    joined = {}
    for k, im in zip(key, imgs):
        if im is not None and im == im:
            lst = joined.setdefault(k, [])
            if im not in lst:
                lst.append(im)
    o['image_name'] = [config.IMAGE_SEPARATOR.join(joined[k]) if k in joined else None for k in key]
    o['image_urls'] = [list(joined[k]) if k in joined else None for k in key]
    md = p['max_depth']
    o['nsv'] = [json.dumps({"area_sqm": a, "max_depth_mm": m})
                for a, m in zip(area.where(area.notna(), None), md.where(md.notna(), None))]
    o['side'] = p['side'].values
    o['direction'] = ctx['direction'].values
    o['reporting_interval_m'] = prj.reporting_interval('pothole')
    o['rating_ca'] = None                    # filled in finalize from D4 'Visual Condition - Pothole (CA)'
    o['rating_irc'] = None                   # filled in finalize from D4 'Visual Condition - Pothole (IRC)'
    o['measures'] = [json.dumps({"area_sqm": a}) for a in area.where(area.notna(), None)]
    for k in ['affected_length', 'total_length', 'affected_length_ca', 'affected_length_irc']:
        o[k] = a0.values                                   # pothole area as read, whatever breach_flag is
    common_cols(o, prj, TABLE, 'pothole', LEVEL, ctx)
    # as in the old notebook: tracker dates, else the pothole's own survey date
    own = pd.to_datetime(p['datetime'], errors='coerce').dt.date
    trk = prj._tracker_raw
    o['survey_start_date'] = trk['survey_start_date'] if trk['survey_start_date'] else own.values
    o['survey_end_date'] = trk['survey_end_date'] if trk['survey_end_date'] else own.values
    o['detected_on'] = o['survey_end_date']
    return o


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
