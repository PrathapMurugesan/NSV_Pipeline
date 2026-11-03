"""
Building the rows of every table:
  • columns every table has (upc, zone, dates, project_details …), defect_id, parent_* block
  • 10 m split, 100 m and 1 km aggregation
  • CA row + IRC twin row with threshold, breach_flag, affected lengths
"""
import json

import numpy as np
import pandas as pd

import config
from common.utils import standardize_nh
from common.project import breach
from common.defects import METRIC_NAME, linear_rows


# ── Columns every table has + ids ─────────────────────────────────────────────
def common_cols(df, prj, collection, defect_base, level, ctx):
    df['collection_name'] = collection
    df['defect_base'] = defect_base
    df['level'] = level
    df['upc'] = prj.upc_slash
    df['upc_code'] = prj.upc
    df['zone'] = prj.zone
    df['phase'] = prj.phase
    df['cycle'] = int(prj.cycle)
    df['source_system'] = config.SOURCE_SYSTEM
    df['sources'] = [[config.SOURCE_SYSTEM]] * len(df)
    df['section_code'] = ctx['section_code'].values
    df['pavement_type'] = ctx['pavement_type'].values
    df['carriageway'] = ctx['carriageway'].values
    df['pci'] = prj.tracker_ctx.get('pci')          # replaced later by calculated PCI
    df['survey_start_date'] = prj.tracker_ctx['survey_start_date']
    df['survey_end_date'] = prj.tracker_ctx['survey_end_date']
    df['is_duplicate'] = False
    df['project_details'] = json.dumps({"project_name": prj.upc, "zone": prj.zone,
                                        "phase": prj.phase, "cycle": prj.cycle})
    df['created_at'] = prj.now_ts
    df['updated_at'] = prj.now_ts
    df['has_standard_defect'] = True
    df['standard_flags'] = '{}'
    df['run_date'] = prj.run_date
    df['detected_on'] = prj.tracker_ctx['survey_end_date']
    return df


def defect_ids(prj, starts, name, seq, mask):
    """D/<ddmmyy>/N/<UPC>/<start>/<Defect>/<nnnn> for breach rows, else None."""
    return [f"D/{prj.survey_date_str}/N/{prj.upc}/{int(s)}/{name}/{q:04d}" if m else None
            for s, q, m in zip(starts, seq, mask)]


def parent_block(level):
    return config.PARENT_BLOCK_BY_LEVEL.get(level, config.PARENT_LENGTH_M)


def parent_cols(df, prj, lanes, starts, level):
    """parent_start_chainage / parent_end_chainage / parent_id for the level's parent block."""
    b = parent_block(level)
    ps = starts // b * b
    df['parent_start_chainage'] = ps.values
    df['parent_end_chainage'] = (ps + b).values
    df['parent_id'] = [f"{prj.upc}_{l}_{int(p)}_{int(p) + b}" if l is not None and p == p else None
                       for l, p in zip(lanes, ps)]
    return df


# ── 10 m / 100 m / 1 km ───────────────────────────────────────────────────────
def split_10m(n):
    """Rows wider than 10 m → one row per 10 m (D4 is already 10 m, so normally 1:1)."""
    s, e = n['start_chainage'].values, n['end_chainage'].values
    valid = e > s
    s_al = np.round(s / 10).astype('int64') * 10
    e_al = np.round(e / 10).astype('int64') * 10
    e_al = np.where(e_al <= s_al, s_al + 10, e_al)
    nseg = np.where(valid, (e_al - s_al) // 10, 1)
    out = n.loc[n.index.repeat(nseg)].copy()
    k = out.groupby(level=0).cumcount().values
    seg_start = np.repeat(s_al, nseg) + 10 * k
    v = np.repeat(valid, nseg)
    out['start_chainage'] = np.where(v, np.where(k == 0, np.repeat(np.round(s).astype('int64'), nseg), seg_start),
                                     out['start_chainage'])
    out['end_chainage'] = np.where(v, seg_start + 10, out['end_chainage'])
    out['length_m'] = out['end_chainage'] - out['start_chainage']
    return out.reset_index(drop=True)


_AGG = {'survey_date': 'first', 'start_lat': 'first', 'start_long': 'first', 'end_lat': 'last', 'end_long': 'last',
        'metric_value': 'mean', 'severity': 'first', 'length_m': 'sum', 'row_id': 'count',
        'start_chainage': 'min', 'end_chainage': 'max', 'roughness_bi': 'mean', 'iri_value': 'mean',
        'rutting_avg': 'mean', 'ravelling_area': 'mean', 'lwp_iri': 'mean', 'rwp_iri': 'mean',
        'ravelling_area_sqm': lambda s: s.sum(min_count=1)}      # total sq m of the block; all-NaN stays NaN


def aggregate(n, bin_m, defect):
    a = n.copy()
    a['_b'] = a['start_chainage'] // bin_m * bin_m
    g = a.groupby(['lane', '_b'], dropna=False).agg(_AGG).reset_index().rename(columns={'row_id': 'segment_count'})
    if defect == 'roughness':
        # new structure: one IRI per lane (D4 'Roughness IRI @ 10 mtr'), no left / right wheel path.
        # iri_value = average of the 10 m IRI of the block (groupby above)
        if config.ROUGHNESS_AGGREGATION == 'iri_formula':
            # average IRI, then BI = 630 × IRI^1.12 (how D2 is built)
            v = g['iri_value'].notna()
            g.loc[v, 'roughness_bi'] = 630.0 * g.loc[v, 'iri_value'] ** 1.12
        # 'mean_bi': roughness_bi is already the average of the 10 m BI (from the groupby above)
        g['metric_value'] = g['roughness_bi']
    if defect == 'ravelling':
        g = _ravelling_pct_from_sqm(g, bin_m)
    return g.drop(columns=['_b'])


def _ravelling_pct_from_sqm(g, block_m):
    """Ravelling % of a block = total affected sq m × 100 ÷ lane area of the block (3.5 m × its surveyed length:
    full blocks 100 m 350, 1 km 3,500; the road's last, shorter block its real length, as the DLM notebook).
    Not the mean of the 10 m percentages. All-blank sq m → blank %."""
    length = pd.to_numeric(g['length_m'], errors='coerce') if 'length_m' in g else pd.Series(np.nan, index=g.index)
    denom = (config.LANE_AREA_SQM / config.CHILD_10M) * length.where(length > 0, block_m)
    g['ravelling_area'] = g['ravelling_area_sqm'] * 100.0 / denom
    g['metric_value'] = g['ravelling_area']
    return g


# Used when config.PARENT_AGGREGATION = 'from_100m': 1 km rows built FROM THE 100 m ROWS:
#   averages (metric, rut, BI, IRI) = mean of the 100 m values in the km
#   ravelling sq m = sum of the 100 m sums (all blank → blank, never 0); ravelling % = that sum × 100 ÷ 3,500
#   length / segment_count = sums
_AGG_FROM_100M = {'survey_date': 'first', 'start_lat': 'first', 'start_long': 'first', 'end_lat': 'last', 'end_long': 'last',
                  'metric_value': 'mean', 'severity': 'first', 'length_m': 'sum', 'segment_count': 'sum',
                  'start_chainage': 'min', 'end_chainage': 'max', 'roughness_bi': 'mean', 'iri_value': 'mean',
                  'rutting_avg': 'mean', 'ravelling_area': 'mean', 'lwp_iri': 'mean', 'rwp_iri': 'mean',
                  'ravelling_area_sqm': lambda s: s.sum(min_count=1)}


def aggregate_from_100m(g100, defect):
    a = g100.copy()
    a['_b'] = a['start_chainage'] // config.PARENT_LENGTH_M * config.PARENT_LENGTH_M
    g = a.groupby(['lane', '_b'], dropna=False).agg(_AGG_FROM_100M).reset_index()
    if defect == 'roughness':
        if config.ROUGHNESS_AGGREGATION == 'iri_formula':
            v = g['iri_value'].notna()
            g.loc[v, 'roughness_bi'] = 630.0 * g.loc[v, 'iri_value'] ** 1.12
        # 'mean_bi': roughness_bi is already the average of the 100 m BI values
        g['metric_value'] = g['roughness_bi']
    if defect == 'ravelling':
        g = _ravelling_pct_from_sqm(g, config.PARENT_LENGTH_M)
    return g.drop(columns=['_b'])


def level_input(prj, defect, level):
    """Rows for the requested level (before building records):
       10 m = D4 rows;  100 m = aggregate of the 10 m rows;
       1 km = aggregate of the 10 m rows (PARENT_AGGREGATION 'from_10m', as D2) or of the 100 m rows ('from_100m')."""
    n10 = linear_rows(prj, defect)
    if n10.empty:
        return n10
    if level == 'child_10m':
        return split_10m(n10)
    if level == 'parent' and config.PARENT_AGGREGATION == 'from_10m':
        return aggregate(n10, config.PARENT_LENGTH_M, defect)      # 1 km = average of all 10 m values (as D2)
    g100 = aggregate(n10, config.CHILD_100M, defect)
    if level == 'child_100m':
        return g100
    return aggregate_from_100m(g100, defect)


def _breach(prj, defect, metric, bi, std):
    """metric_value vs this standard's threshold_value; operator from D2 Inputs (Thresholds) column B, default >=."""
    return breach(metric, prj.thr[std][defect], prj.thr_op(std, defect))


def build_linear_table(prj, defect, level):
    """Full CA + IRC record set for master_<defect>_<level> (before PCI / ratings)."""
    tbl = f"master_{defect}_{level}"
    p = level_input(prj, defect, level)
    if p.empty:
        print(f"  {tbl}: no D4 data")
        return pd.DataFrame()
    p = p.reset_index(drop=True)
    seq = np.arange(1, len(p) + 1)
    ctx = prj.road.lookup(p['start_chainage'].values, p['lane'].values)
    bi, iri = p['roughness_bi'], p['iri_value']
    metric = (bi if defect == 'roughness'                     # BI only; blank BI → NULL (no IRI fallback)
              else p['rutting_avg'] if defect == 'rutting' else p['ravelling_area'])
    seg = p['length_m'].fillna(0).astype(float)
    std_def = prj.tracker_ctx['defect_standards'].get(defect, {}).get('standard')
    survey = p['survey_date'].where(p['survey_date'].notna(), prj.run_ts) if 'survey_date' in p else prj.run_ts
    nh_row = p['nh_number'] if 'nh_number' in p else pd.Series([None] * len(p))

    out = pd.DataFrame({'_seq': seq})
    rec_id = [f"{defect}_{level}_{q:06d}" for q in seq]
    out['source_defect_id'] = rec_id
    out['record_id'] = rec_id
    parent_cols(out, prj, p['lane'], p['start_chainage'].astype('int64'), level)
    out['nh_number'] = [standardize_nh(a if a is not None and not (isinstance(a, float) and np.isnan(a)) else b)
                        for a, b in zip(nh_row, ctx['nh_number'])]
    out['defect_type'] = defect.title()
    out['type_of_defect'] = defect.title()
    out['defect_type_filter'] = defect.title()
    out['category'] = 'LENGTH'
    out['severity'] = p['severity'].values
    out['lane'] = p['lane'].values
    out['metric_name'] = METRIC_NAME[defect]
    out['metric_value'] = metric.values
    out['total_children'] = p.get('segment_count', pd.Series(1, index=p.index)).fillna(1).astype(int).values
    out['segment_count'] = out['total_children']
    out['length_m'] = seg.values
    out['start_chainage'] = p['start_chainage'].astype('int64').values
    out['end_chainage'] = p['end_chainage'].astype('int64').values
    out['defect_chainage'] = out['start_chainage']
    for c in ['start_lat', 'start_long', 'end_lat', 'end_long']:
        out[c] = p[c].values
    out['defect_lat'] = p['start_lat'].values
    out['defect_long'] = p['start_long'].values
    out['datetime'] = pd.to_datetime(survey).values if not np.isscalar(survey) else survey
    out['observed_at'] = out['datetime']
    out['nsv'] = json.dumps({"metric_source": defect})
    out['direction'] = ctx['direction'].values
    out['reporting_interval_m'] = prj.reporting_interval(defect)
    out['roughness_bi'] = bi.values if defect == 'roughness' else None
    out['iri_value'] = iri.values if defect == 'roughness' else None
    out['rutting_avg'] = p['rutting_avg'].values if defect == 'rutting' else None
    out['ravelling_area'] = p['ravelling_area'].values if defect == 'ravelling' else None
    out['ravelling_area_sqm'] = p['ravelling_area_sqm'].values if defect == 'ravelling' and 'ravelling_area_sqm' in p else None
    out['iri_condition'] = p['severity'].values if defect == 'roughness' else None
    out['rating_ca'] = p['severity'].values if std_def == 'CA' else None
    out['rating_irc'] = p['severity'].values if std_def == 'IRC' else None
    if defect == 'roughness':
        out['measures'] = [json.dumps({'roughness_bi': m, 'lwp_iri': None, 'rwp_iri': None, 'lane_iri': i})
                           for m, i in zip(metric.where(metric.notna(), None), iri.where(iri.notna(), None))]
    else:
        out['measures'] = [json.dumps({METRIC_NAME[defect]: m}) for m in metric.where(metric.notna(), None)]
    out['total_length'] = seg.values
    common_cols(out, prj, tbl, defect, level, ctx)

    frames = []
    for std in ['ca', 'irc']:
        r = out.copy()
        bf = _breach(prj, defect, metric, bi, std).values
        sfx = '' if std == 'ca' else 'irc_'
        r['id'] = [f"{prj.upc}_{tbl}_{sfx}{q:06d}" for q in seq]
        if std == 'irc':
            r['record_id'] = r['record_id'] + '_irc'
            r['source_defect_id'] = r['source_defect_id'] + '_irc'
        r['threshold_name'] = 'As per CA' if std == 'ca' else 'As per IRC'
        r['threshold_value'] = prj.thr[std][defect]
        r['breach_flag'] = bf
        did = defect_ids(prj, r['start_chainage'], defect.title(), seq, bf == 1)
        r['defect_id'] = did
        r['display_defect_id'] = did
        aff = np.where(bf == 1, seg.round(4).values, 0.0)
        r['affected_length'] = aff
        r['affected_length_ca'] = aff if std == 'ca' else 0.0
        r['affected_length_irc'] = aff if std == 'irc' else 0.0
        frames.append(r)
    return pd.concat(frames, ignore_index=True).drop(columns=['_seq'])
