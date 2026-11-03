"""
dlm_threshold_mapping
  One row per defect (roughness, cracking, rutting, ravelling, pothole) of the UPC: the thresholds, IRC bands,
  visual-condition labels, reporting intervals and road facts the silver tables were built with.

  ca_threshold / irc_threshold     D2 only (Inputs (Thresholds) json → header json → limit column); no defaults,
                                   no IRC → CA fallback. Pothole: NULL (D2 has no pothole threshold).
  ca_source / irc_threshold_source 'D2 Comparison as per CA (…)' / 'D2 Comparison as per IRC (…)' with the place in D2
                                   the value was read from; 'NOT IN D2' when missing.
  irc_good_max … irc_bad           IRC bands exactly as the code classifies: defect_rules.json rating_ranges (IRC)
                                   × the D2 IRC threshold. Good = Very Good + Good, Bad = Poor + Very Poor.
  visual_condition_labels_ca/_irc  distinct D4 Defect Report 'Visual Condition - <defect> (CA)' / '(IRC)' labels
                                   (numbers 1–5 already turned into labels). D3 is not used.
  reporting_interval_m             D2 'Comparison as per CA', current cycle '<defect> | Reporting Interval'
  irc_reporting_interval_m         D2 'Comparison as per IRC', same (Project.d2_interval; never the tracker)
  number_of_lanes / total_length_km / lane_length_km
                                   1) tracker 'Number of lanes' / 'Total Length (km)' / 'Lane Length (km)'
                                   2) D1 LRMS SectionMaster 'Lanes' ('4 Lane …' → 4) / 'SectionLength' (m → km)
                                   3) D4: lanes used / chainage range / 10 m rows with a measured value (lane-km)
  pavement_type                    tracker, else the most common D1 PavementType
  deliverable_path                 bronze UPC path: config.BRONZE_S3_ROOT + /zone=…/phase=…/cycle=…/<UPC> when set,
                                   else the project folder the run read
Run alone:  python tables/dlm_threshold_mapping.py [--no-excel]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.utils import num, pick, clean_text
from common.defects import band_limits, ranges_for, _fmt
from common.output import run_table, standalone

TABLE = 'dlm_threshold_mapping'
DEFECTS = ['roughness', 'cracking', 'rutting', 'ravelling', 'pothole']
UNIT = {'roughness': 'mm/km', 'cracking': '% area', 'rutting': 'mm', 'ravelling': '% area', 'pothole': 'sq m'}
VC_FIELD = {'roughness': 'vc_rough', 'rutting': 'vc_rut', 'cracking': 'vc_crack', 'ravelling': 'vc_rav',
            'pothole': 'vc_pot'}

COLUMNS = ['zone', 'upc_code', 'upc', 'phase', 'cycle', 'defect', 'unit', 'ca_threshold', 'irc_threshold',
           'irc_good_max', 'irc_fair_min', 'irc_fair_max', 'irc_bad_min', 'irc_good', 'irc_fair', 'irc_bad',
           'visual_condition_labels_ca', 'visual_condition_labels_irc', 'pavement_type', 'reporting_standard_tracker',
           'reporting_interval_m', 'irc_reporting_interval_m', 'd2_report_standard', 'has_d2_ca_file',
           'has_d2_irc_file', 'number_of_lanes', 'total_length_km', 'lane_length_km', 'ca_source',
           'irc_threshold_source', 'irc_band_source', 'pavement_type_source', 'lanes_length_source',
           'tracker_matched', 'deliverable_path', 'updated_at']


def _irc_bands(prj, defect, t):
    """IRC bands as the code classifies (rating_ranges IRC × D2 IRC threshold) → the 8 irc_* fields."""
    out = dict.fromkeys(['irc_good_max', 'irc_fair_min', 'irc_fair_max', 'irc_bad_min', 'irc_good', 'irc_fair',
                         'irc_bad', 'irc_band_source'])
    ranges = ranges_for(prj, 'rating_ranges', defect, 'irc')
    if not ranges or t is None:
        return out
    lims = band_limits(ranges, t)
    if lims is None:
        return out
    groups = {'good': [], 'fair': [], 'bad': []}
    prev = None
    for b, lim in zip(ranges, lims):
        lab = b['label'].lower()
        g = 'fair' if 'fair' in lab else 'good' if 'good' in lab else 'bad'
        text = b.get('text', b['label']).replace('{from}', _fmt(prev)).replace('{to}', _fmt(lim))
        groups[g].append((prev, lim, text))
        prev = lim if lim is not None else prev
    if groups['good']:
        out['irc_good_max'] = groups['good'][-1][1]
        out['irc_good'] = ', '.join(x[2] for x in groups['good'])
    if groups['fair']:
        out['irc_fair_min'], out['irc_fair_max'] = groups['fair'][0][0], groups['fair'][-1][1]
        out['irc_fair'] = ', '.join(x[2] for x in groups['fair'])
    if groups['bad']:
        out['irc_bad_min'] = groups['bad'][0][0]
        out['irc_bad'] = ', '.join(x[2] for x in groups['bad'])
    out['irc_band_source'] = f'defect_rules.json rating_ranges (IRC) × D2 IRC threshold {t:g}'
    return out


def _vc_labels(prj, defect, std):
    """Distinct D4 Visual Condition labels of the defect for one standard (CA / IRC), sorted, comma separated."""
    col = f"{VC_FIELD[defect]}_{std}"
    d4 = prj.d4_long
    if d4.empty or col not in d4:
        return None
    v = sorted({str(x).strip() for x in d4[col].dropna() if str(x).strip()})
    return ', '.join(v) if v else None


def _lanes_length(prj):
    """number_of_lanes, total_length_km, lane_length_km, source text.
       1) NSV Activity Tracker 'Number of lanes' / 'Total Length (km)' / 'Lane Length (km)'
       2) D1 LRMS SectionMaster 'Lanes' / 'SectionLength'   3) D4 (lanes used, chainage range, surveyed 10 m rows)"""
    d4 = prj.d4_long
    trk = prj.tracker_ctx
    lanes, total, lane_km = trk.get('number_of_lanes'), trk.get('total_length_km'), trk.get('lane_length_km')
    src = []
    if lanes is not None or total is not None or lane_km is not None:
        src.append('tracker (' + ', '.join(n for n, v in [('Number of lanes', lanes), ('Total Length (km)', total),
                                                          ('Lane Length (km)', lane_km)] if v is not None) + ')')
    sm = prj.read_first('d1', 'sectionmaster') if (lanes is None or total is None) else pd.DataFrame()
    if not sm.empty:
        c = list(sm.columns)
        lc, slc = pick(c, 'lanes'), pick(c, 'sectionlength') or pick(c, 'section', 'length')
        got = []
        if lc and lanes is None:
            m = sm[lc].astype(str).str.extract(r'(\d+)')[0].dropna()
            lanes = int(m.iloc[0]) if len(m) else None
            got.append('Lanes')
        if slc and total is None:
            v = num(sm[slc]).dropna()
            total = round(float(v.sum()) / 1000.0, 3) if len(v) else None
            got.append('SectionLength')
        if got:
            src.append(f"D1 LRMS SectionMaster ({', '.join(got)})")
    if not d4.empty:
        if lanes is None:
            lanes = int(d4['lane'].nunique())
            src.append('lanes: D4 lanes')
        if total is None:
            total = round((float(d4['end_chainage'].max()) - float(d4['start_chainage'].min())) / 1000.0, 3)
            src.append('length: D4 chainage range')
    if lane_km is None and not d4.empty:
        vals = [f for f in ['rut', 'iri', 'bi', 'rav_area', 'crack_pct', 'crack_sqm', 'pothole_area', 'patch_area']
                if f in d4]
        has = d4[vals].notna().any(axis=1) if vals else pd.Series(False, index=d4.index)
        seg = (d4['end_chainage'] - d4['start_chainage']).clip(lower=0)
        lane_km = round(float(seg[has].sum()) / 1000.0, 3)
        src.append('lane_length_km: D4 10 m rows with a measured value, summed over lanes')
    return lanes, total, lane_km, '; '.join(src) or None


def _pavement(prj):
    pt = prj.tracker_ctx.get('pavement_type')
    if pt:
        return pt, 'tracker'
    pav = prj.d1_pavement
    if not pav.empty:
        c = pick(list(pav.columns), 'pavement')
        if c:
            v = clean_text(pav[c]).dropna()
            if len(v):
                return v.mode().iloc[0], 'D1 PavementType (mode)'
    return None, None


def _deliverable_path(prj):
    root = getattr(config, 'BRONZE_S3_ROOT', None)
    if root:
        return f"{root.rstrip('/')}/zone={prj.zone}/phase={prj.phase}/cycle={prj.cycle}/{os.path.basename(prj.project_dir.rstrip('/'))}"
    return prj.project_dir


def build(prj):
    has_ca, has_irc = not prj.d2_ca.empty, not prj.d2_irc.empty
    lanes, total, lane_km, ll_src = _lanes_length(prj)
    pavement, pav_src = _pavement(prj)
    common = {
        'zone': prj.zone, 'upc_code': prj.upc, 'upc': prj.upc_slash, 'phase': str(prj.phase), 'cycle': str(prj.cycle),
        'pavement_type': pavement, 'pavement_type_source': pav_src,
        'd2_report_standard': ', '.join(s for s, h in [('CA', has_ca), ('IRC', has_irc)] if h) or None,
        'has_d2_ca_file': has_ca, 'has_d2_irc_file': has_irc,
        'number_of_lanes': lanes, 'total_length_km': total, 'lane_length_km': lane_km, 'lanes_length_source': ll_src,
        'tracker_matched': bool(prj.tracker_ctx.get('found')),
        'deliverable_path': _deliverable_path(prj),
        'updated_at': pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
    }
    rows = []
    for d in DEFECTS:
        has_thr = d in prj.thr['ca']
        ca_t = prj.thr['ca'].get(d) if has_thr else None
        irc_t = prj.thr['irc'].get(d) if has_thr else None
        r = {**common, 'defect': d, 'unit': UNIT[d], 'ca_threshold': ca_t, 'irc_threshold': irc_t,
             'reporting_standard_tracker': prj.tracker_ctx['defect_standards'].get(d, {}).get('standard'),
             'reporting_interval_m': prj.d2_interval('CA', d),
             'irc_reporting_interval_m': prj.d2_interval('IRC', d),
             'visual_condition_labels_ca': _vc_labels(prj, d, 'ca'),
             'visual_condition_labels_irc': _vc_labels(prj, d, 'irc'),
             'ca_source': None, 'irc_threshold_source': None}
        if has_thr:
            r['ca_source'] = (f"D2 Comparison as per CA ({prj.thr_src.get(('ca', d), 'D2')})" if ca_t is not None
                              else 'NOT IN D2')
            r['irc_threshold_source'] = (f"D2 Comparison as per IRC ({prj.thr_src.get(('irc', d), 'D2')})"
                                         if irc_t is not None else 'NOT IN D2')
        r.update(_irc_bands(prj, d, irc_t))
        rows.append(r)
        print(f"  {d}: CA {ca_t} / IRC {irc_t} | interval CA {r['reporting_interval_m']} / IRC "
              f"{r['irc_reporting_interval_m']} | VC CA [{r['visual_condition_labels_ca']}] IRC "
              f"[{r['visual_condition_labels_irc']}]")
    out = pd.DataFrame(rows, columns=COLUMNS)
    for c in ['ca_threshold', 'irc_threshold', 'irc_good_max', 'irc_fair_min', 'irc_fair_max', 'irc_bad_min',
              'reporting_interval_m', 'irc_reporting_interval_m', 'total_length_km', 'lane_length_km']:
        out[c] = pd.to_numeric(out[c], errors='coerce').astype('float64')
    out['number_of_lanes'] = pd.to_numeric(out['number_of_lanes'], errors='coerce').astype('Int64')
    return out


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, breach_copy=False)


if __name__ == '__main__':
    standalone(run)
