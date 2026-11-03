"""
master_cracking
  One row per 10 m per lane. Source (config.CRACKING_SOURCES, first that has data):
    1) D4 defect report 'Cracking Lane Area (%) / (sq. m.) @ 10 mtr | <lane>'
    2) D4 standalone NSV cracks sheet 'Crack Area | Total' (%), sq m = % × 35 / 100
  metric = cracking %; parent_* = its 1 km block (DLM notebook).
  Defect report: every D4 lane-row is kept (a blank % stays NULL, breach_flag 0) once D4 has any cracking value;
  road_condition D4 label → CA Good/Bad from breach, and NULL when the % is blank or D2 has no threshold.
  image_name / image_urls: from the D4 crack image list (d4_images_crack_images): each image → one 10 m row of its
  lane: chainage range → lat / long (≤ CRACK_IMAGE_TOLERANCE_M) → OCR chainage → OCR lat / long; 100 m / 1 km rows list every
  image of their 10 m rows. image_name = file names (comma separated), image_urls = image paths.
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_cracking.py [--no-excel]
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.utils import standardize_nh
from common.defects import crack_condition, cracking_rows, map_crack_images
from common.segments import common_cols, defect_ids, parent_cols
from common.project import breach
from common.output import run_table, standalone

TABLE = 'master_cracking'
LEVEL = 'child_10m'


def _aggregate(s, bin_m):
    """10 m cracking rows → one row per lane per block of bin_m metres (DLM notebook build_cracking_agg_pdf):
       cracking % = total crack sq m of the block ÷ (lane width 3.5 m × covered length) × 100, covered length =
       last end − first start of the block's rows. Not the mean of the 10 m %: a short first / last row or a missing
       10 m row would weigh the same as a full one. A row with a % but no sq m counts % × 3.5 × its length ÷ 100;
       a blank row counts 0 inside the covered length; no value at all in the block → blank.
       sq m = total (all blank → blank), length = sum of the 10 m lengths, total_children = number of 10 m rows."""
    a = s.copy()
    a['_b'] = a['start'].astype('int64') // bin_m * bin_m
    a['_len'] = (a['end'] - a['start']).astype(float)
    width = config.LANE_AREA_SQM / config.CHILD_10M                    # 3.5 m (35 sq m per 10 m)
    a['_area'] = a['crack_sqm'].where(a['crack_sqm'].notna(), a['crack_pct'] * width * a['_len'] / 100.0)
    g = a.groupby(['lane', '_b'], dropna=False).agg(
        start=('start', 'min'), end=('end', 'max'), _area=('_area', lambda x: x.sum(min_count=1)),
        crack_sqm=('crack_sqm', lambda x: x.sum(min_count=1)), length=('_len', 'sum'), children=('start', 'size'),
        start_lat=('start_lat', 'first'), start_long=('start_long', 'first'),
        end_lat=('end_lat', 'last'), end_long=('end_long', 'last'),
        nh_number=('nh_number', 'first'), datetime=('datetime', 'first'), source=('source', 'first')).reset_index()
    covered = (g['end'] - g['start']).astype(float)
    g['crack_pct'] = (g['_area'] * 100.0 / (width * covered)).where(covered > 0)
    g['image_name'] = None                     # image names belong to the 10 m rows only
    return g.drop(columns=['_b', '_area'])


def level_rows(prj, level):
    """10 m = the cracking source rows; 100 m = average of the 10 m rows of the block;
       1 km = average of all 10 m rows of the km (PARENT_AGGREGATION 'from_10m', as D2)
              or average of the 100 m averages ('from_100m')."""
    s = cracking_rows(prj)
    if s.empty or level == 'child_10m':
        return s
    if level == 'child_100m':
        return _aggregate(s, config.CHILD_100M)
    if config.PARENT_AGGREGATION == 'from_10m':
        return _aggregate(s, config.PARENT_LENGTH_M)
    g100 = _aggregate(s, config.CHILD_100M)                     # 1 km = average of the 100 m averages
    k = g100.assign(_b=g100['start'].astype('int64') // config.PARENT_LENGTH_M * config.PARENT_LENGTH_M)
    g = k.groupby(['lane', '_b'], dropna=False).agg(
        start=('start', 'min'), end=('end', 'max'), crack_pct=('crack_pct', 'mean'),
        crack_sqm=('crack_sqm', lambda x: x.sum(min_count=1)), length=('length', 'sum'), children=('children', 'sum'),
        start_lat=('start_lat', 'first'), start_long=('start_long', 'first'),
        end_lat=('end_lat', 'last'), end_long=('end_long', 'last'),
        nh_number=('nh_number', 'first'), datetime=('datetime', 'first'), source=('source', 'first')).reset_index()
    g['image_name'] = None
    g = g.drop(columns=['_b'])
    return g


def _images_10m(prj):
    """Images of every 10 m cracking row, keyed by (lane, start): ([file names], [image paths]). Built once per UPC."""
    if getattr(prj, '_crack_images_10m', None) is None:
        s10 = cracking_rows(prj).reset_index(drop=True)
        names, paths, st = map_crack_images(prj, s10)
        keys = list(zip(s10['lane'].astype(str).str.upper(), s10['start'].astype('int64')))
        prj._crack_images_10m = {k: (nm, pt) for k, nm, pt in zip(keys, names, paths) if nm}
        tol = f"{config.CRACK_IMAGE_TOLERANCE_M:g} m"
        print(f"  cracking images (D4 crack image list): {st['images']:,} images → "
              f"1) chainage {st['by_chainage']:,}, 2) lat/long ≤ {tol} {st['by_latlon']:,}, "
              f"3) OCR chainage {st['by_ocr_chainage']:,}, 4) OCR lat/long ≤ {tol} {st['by_ocr_latlon']:,}, "
              f"not mapped {st['not_mapped']:,} (log only); {len(prj._crack_images_10m):,} of {len(s10):,} 10 m rows have an image")
    return prj._crack_images_10m


def _images_for_level(prj, s, level):
    """Per row of this level: (file names, image paths). 10 m: the row's own images; 100 m / 1 km: every image of
       the 10 m rows inside the block, in chainage order."""
    m = _images_10m(prj)
    lane = s['lane'].astype(str).str.upper().values
    start = s['start'].astype('int64').values
    if level == 'child_10m':
        got = [m.get((l, int(a)), ([], [])) for l, a in zip(lane, start)]
        return [g[0] for g in got], [g[1] for g in got]
    size = config.CHILD_100M if level == 'child_100m' else config.PARENT_LENGTH_M
    blocks = {}
    for (l, a) in sorted(m, key=lambda k: (k[0], k[1])):
        nm, pt = m[(l, a)]
        b = blocks.setdefault((l, a // size * size), ([], []))
        for x, y in zip(nm, pt):
            if x not in b[0]:
                b[0].append(x)
                b[1].append(y)
    got = [blocks.get((l, int(a) // size * size), ([], [])) for l, a in zip(lane, start)]
    return [g[0] for g in got], [g[1] for g in got]


def build(prj):
    return build_level(prj, TABLE, LEVEL)


def build_level(prj, table, level):
    """master_cracking (10 m), master_cracking_child_100m, master_cracking_parent (1 km)."""
    s = level_rows(prj, level)
    if s.empty:
        print(f"  cracking: no cracking data in the defect report or the NSV cracks sheet → {table} empty")
        return pd.DataFrame()
    s = s.reset_index(drop=True)
    print(f"  cracking source: {s['source'].iloc[0]}" + ("" if level == 'child_10m' else f" → aggregated to {level}"))
    st, en = s['start'].astype('int64'), s['end'].astype('int64')
    seq = np.arange(1, len(s) + 1)
    ctx = prj.road.lookup(st.values, s['lane'].values)
    v = s['crack_pct']
    cond = crack_condition(v, prj.thr['ca']['cracking'])
    seg = (en - st).astype(float) if level == 'child_10m' else s['length'].astype(float)
    std_def = prj.tracker_ctx['defect_standards']['cracking']['standard']

    tag = '' if level == 'child_10m' else f"{level}_"
    o = pd.DataFrame({'source_defect_id': [f"cracking_{tag}{q:06d}" for q in seq]})
    o['record_id'] = o['source_defect_id']
    parent_cols(o, prj, s['lane'], st, level)
    o['nh_number'] = [standardize_nh(a) or b for a, b in zip(s['nh_number'], ctx['nh_number'])]
    o['defect_type'] = o['type_of_defect'] = o['defect_type_filter'] = 'Cracking'
    o['category'] = 'LENGTH'
    o['severity'] = cond.values             # placeholder; finalize sets it per row's standard (apply_severity)
    o['lane'] = s['lane'].values
    o['metric_name'] = 'cracking_pct'
    o['metric_value'] = v.values
    o['cracking_pct'] = v.values
    o['total_children'] = 1 if level == 'child_10m' else s['children'].astype(int).values
    o['segment_count'] = o['total_children']
    o['length_m'] = seg.values
    o['area_sqm'] = s['crack_sqm'].values
    o['start_chainage'] = st.values
    o['end_chainage'] = en.values
    o['defect_chainage'] = ((st + en) / 2).values
    for c in ['start_lat', 'start_long', 'end_lat', 'end_long']:
        o[c] = s[c].values
    o['defect_lat'] = s['start_lat'].values
    o['defect_long'] = s['start_long'].values
    o['datetime'] = pd.to_datetime(s['datetime']).values
    o['observed_at'] = o['datetime']
    names, paths = _images_for_level(prj, s, level)
    o['image_name'] = [config.IMAGE_SEPARATOR.join(x) if x else None for x in names]   # file names, comma separated
    o['image_urls'] = [list(x) if x else None for x in paths]                         # image paths
    o['nsv'] = [json.dumps({"condition": c, "cracking": x}) for c, x in zip(cond, v.where(v.notna(), None))]
    o['direction'] = ctx['direction'].values
    o['reporting_interval_m'] = prj.reporting_interval('cracking')
    o['rating_ca'] = cond.values if std_def == 'CA' else None
    o['rating_irc'] = cond.values if std_def == 'IRC' else None
    o['measures'] = [json.dumps({"cracking_pct": x, "crack_area_sqm": a, "lane_area_sqm": config.LANE_AREA_SQM,
                                 "crack_count": None, "avg_corrected_width_mm": None})
                     for x, a in zip(v.where(v.notna(), None), s['crack_sqm'].where(s['crack_sqm'].notna(), None))]
    o['total_length'] = seg.values
    common_cols(o, prj, table, 'cracking', level, ctx)

    frames = []
    for std in ['ca', 'irc']:
        r = o.copy()
        # D2 threshold unit can be % or sq. m. ('Inputs (Thresholds)': "units for cracking (% or sq. m.)")
        unit = (prj.thr_unit(std, 'cracking') or '%').lower()
        probe = s['crack_sqm'] if 'sq' in unit else v
        bf = breach(probe, prj.thr[std]['cracking'], prj.thr_op(std, 'cracking')).values
        r['id'] = [f"{prj.upc}_{table}_{'' if std == 'ca' else 'irc_'}{q:06d}" for q in seq]
        if std == 'irc':
            r['record_id'] += '_irc'
            r['source_defect_id'] += '_irc'
        r['threshold_name'] = 'As per CA' if std == 'ca' else 'As per IRC'
        r['threshold_value'] = prj.thr[std]['cracking']
        r['breach_flag'] = bf
        # defect_id: 10 m breach rows only; 100 m / 1 km cracking rows never get one (DLM notebook)
        did = (defect_ids(prj, r['start_chainage'], 'Cracking', seq, bf == 1) if level == 'child_10m'
               else [None] * len(r))
        r['defect_id'] = did
        r['display_defect_id'] = did
        aff = np.where(bf == 1, seg.round(4).values, 0.0)
        r['affected_length'] = aff
        r['affected_length_ca'] = aff if std == 'ca' else 0.0
        r['affected_length_irc'] = aff if std == 'irc' else 0.0
        frames.append(r)
    return pd.concat(frames, ignore_index=True)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
