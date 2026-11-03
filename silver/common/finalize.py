"""
Steps applied to every table after it is built:
  1) PCI  → weighted defect score per 10 m slot that has data (blanks not counted), 100 m / 1 km = mean
  2) rutting / roughness / ravelling (apply_linear_conditions):
       severity       10 m D4 Visual Condition of the row's standard, else calculated; 100 m / 1 km calculated
       rating_ca/irc  10 m D4 Visual Condition, else calculated; 100 m / 1 km calculated (same on both twins)
       road_condition breach_flag only (Poor / Good; NULL when metric_value is blank or there is no D2 threshold)
     pothole: breach_flag 0 for severity Low / Delamination (config), road_condition Bad / Good from breach_flag only,
              rating_ca / rating_irc from D4 'Visual Condition - Pothole (CA)' / '(IRC)' (apply_pothole_ratings)
     cracking: severity per row's standard (apply_severity, as rutting), road_condition D4 → breach
               (apply_road_condition), then ratings
  3) schema alignment (only when SCHEMA_WORKBOOK is set) + column order
"""
import os

import numpy as np
import pandas as pd

import config
from common.utils import num, coalesce
from common.defects import (rutting_condition, roughness_condition, ravelling_condition, crack_condition,
                            linear_rows, cracking_rows, pothole_rows, classify, ranges_for)
from common.segments import split_10m


# ═════════════════════════════════════════════════════════════════════════════
# 1. PCI  (each defect ÷ highest value in project × 100, weighted by config.PCI_WEIGHTS;
#          PCI = 100 − score, clipped 0–100, 4 decimals). Roughness input = IRI (m/km).
#          BLANKS ARE NOT COUNTED: a 10 m lane slot gets a PCI only when at least one defect input has a
#          real value there. Slots with no data (chainage gaps, all-blank D4 rows) get no PCI and are left
#          out of the 100 m / 1 km averages (= mean of the 10 m PCI values that exist in the block).
#          Inside a slot that has data, a defect with no value adds 0 to the score.
#          Inputs are rebuilt from the sources, so every table can run on its own.
# ═════════════════════════════════════════════════════════════════════════════
def _part(lane, start, values, alias, how):
    """One PCI input per (lane, 10 m slot). Blank values stay blank; slots with only blanks are dropped."""
    m = pd.DataFrame({'lane': np.asarray(lane), 'ch': (np.asarray(start).astype('int64') // 10 * 10),
                      alias: num(pd.Series(np.asarray(values, dtype=float))).values})
    agg = (lambda x: x.sum(min_count=1)) if how == 'sum' else how
    out = m.groupby(['lane', 'ch'], as_index=False).agg({alias: agg})
    return out.dropna(subset=[alias])


def _d4_sum(d4, field, alias):
    m = d4[['lane', 'start_chainage', field]].dropna()
    m = pd.DataFrame({'lane': m['lane'], 'ch': m['start_chainage'].astype('int64') // 10 * 10, alias: m[field]})
    return m.groupby(['lane', 'ch'], as_index=False)[alias].sum()


def build_pci(prj):
    d4 = prj.d4_long
    parts = []
    # cracking: cracking % (max per 10 m) — same rows as master_cracking (defect report → NSV cracks)
    cr = cracking_rows(prj)
    parts.append(_part(cr['lane'], cr['start'], cr['crack_pct'], 'cracking_pct', 'max')
                 if len(cr) else pd.DataFrame(columns=['lane', 'ch', 'cracking_pct']))
    # ravelling: D4 ravelling area sq m (sum per 10 m)
    parts.append(_d4_sum(d4, 'rav_area', 'ravelling_area'))
    # potholes: pothole area (sum per 10 m) — same rows as master_pothole (NHAI One sheet only)
    pp = pothole_rows(prj)
    parts.append(_part(pp['lane'], pp['start'], pp['area'], 'pothole_area', 'sum')
                 if len(pp) else pd.DataFrame(columns=['lane', 'ch', 'pothole_area']))
    # rutting / roughness: same 10 m rows as the 10 m tables
    for defect, col, alias in [('rutting', 'rutting_avg', 'rutting_mm'), ('roughness', 'iri_value', 'roughness_iri')]:
        n = linear_rows(prj, defect)
        n = split_10m(n) if len(n) else n
        parts.append(_part(n['lane'], n['start_chainage'], n[col], alias, 'max')
                     if len(n) else pd.DataFrame(columns=['lane', 'ch', alias]))
    # patching: D4 patching area (sum per 10 m)
    parts.append(_d4_sum(d4, 'patch_area', 'patching_area'))

    parts = [p for p in parts if len(p)]
    if not parts:
        return {g: pd.Series(dtype=float) for g in (10, 100, 1000)}
    # only the 10 m slots that have at least one real defect value (no full chainage grid → gaps get no PCI)
    df = pd.concat([p[['lane', 'ch']] for p in parts]).drop_duplicates().reset_index(drop=True)
    for p in parts:
        df = df.merge(p, on=['lane', 'ch'], how='left')
    df['weighted_defect_score'] = 0.0
    for col, w in config.PCI_WEIGHTS.items():
        if col not in df:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0).astype(float)
        mx = df[col].max()
        df['weighted_defect_score'] += ((df[col] / mx * 100).clip(0, 100) if mx > 0 else 0.0) * w
    df['weighted_defect_score'] = df['weighted_defect_score'].round(4)          # as in the notebook
    df['pci_10m'] = (100 - df['weighted_defect_score']).clip(0, 100).round(4)

    lookup = {10: df.groupby(['lane', 'ch'])['pci_10m'].mean().round(4)}
    for g in (100, 1000):
        lookup[g] = df.assign(ch=df['ch'] // g * g).groupby(['lane', 'ch'])['pci_10m'].mean().round(4)
    return lookup


# ═════════════════════════════════════════════════════════════════════════════
# 2–4. road_condition, ratings, schema, column order
# ═════════════════════════════════════════════════════════════════════════════
DEFECTS = ['pothole', 'cracking', 'roughness', 'rutting', 'ravelling']


def defect_of(table):
    return next(d for d in DEFECTS if d in table)


def apply_pci(df, table, prj):
    g = 1000 if table.endswith('_parent') else 100 if table.endswith('_100m') else 10
    key = pd.MultiIndex.from_arrays([df['lane'], df['start_chainage'].astype('int64') // g * g])
    df['pci'] = prj.pci_lookup[g].reindex(key).values
    return df


_D4_CONDITION_COL = {'roughness': 'vc_rough', 'rutting': 'vc_rut', 'pothole': 'vc_pot', 'cracking': 'vc_crack',
                     'ravelling': 'vc_rav'}


def _severity_rank(label):
    """0 = worst … higher = better; unknown labels rank after all known ones."""
    t = str(label).strip().lower()
    for i, k in enumerate(config.CONDITION_SEVERITY_ORDER):
        if t.startswith(k):
            return i
    return len(config.CONDITION_SEVERITY_ORDER)


def d4_condition(prj, defect, std, block):
    """D4 visual condition per (lane, block start); worst 10 m label when block > 10 m."""
    col = _D4_CONDITION_COL.get(defect)
    d4 = prj.d4_long
    if not col or f'{col}_{std}' not in d4:
        return pd.Series(dtype=object)
    d = d4[['lane', 'start_chainage', f'{col}_{std}']].dropna()
    if d.empty:
        return pd.Series(dtype=object)
    d = d.assign(bin=d['start_chainage'].astype('int64') // block * block,
                 rank=d[f'{col}_{std}'].map(_severity_rank))
    d = d.sort_values('rank', kind='mergesort').drop_duplicates(['lane', 'bin'])
    return d.set_index(['lane', 'bin'])[f'{col}_{std}']


def apply_road_condition(df, table, prj):
    """CRACKING only (rutting / roughness / ravelling / pothole use breach_flag).
       road_condition: 1) D4 Visual Condition - Cracks of the row's standard (CA label on CA rows, IRC label on IRC
                          rows; 100 m / 1 km = the worst 10 m label of the block)
                       2) otherwise CA rows Bad / Good from breach_flag, IRC rows blank;
                          a blank metric or no D2 threshold → NULL. (D3 is not used.)"""
    d = defect_of(table)
    block = 1000 if table.endswith('_parent') else 100 if table.endswith('_100m') else 10
    std = np.where(df['threshold_name'].fillna('').str.lower().str.contains('irc'), 'irc', 'ca')
    lane, start = df['lane'], df['start_chainage'].astype('int64')
    result = pd.Series(None, index=df.index, dtype=object)
    n_d4 = 0
    for k in ['ca', 'irc']:
        m = std == k
        if not m.any():
            continue
        look = d4_condition(prj, d, k, block)
        if len(look):
            v = look.reindex(pd.MultiIndex.from_arrays([lane[m], start[m] // block * block])).values
            result[m] = v
    n_d4 = int(result.notna().sum())
    # breach fallback only when there is a value: a blank metric stays NULL (never 'Good')
    has_value = (pd.to_numeric(df['metric_value'], errors='coerce').notna()
                 & pd.to_numeric(df['breach_flag'], errors='coerce').notna()).values      # no D2 threshold → NULL
    miss = result.isna().values & (std == 'ca') & has_value
    result[miss] = np.where(df.loc[miss, 'breach_flag'] == 1, 'Bad', 'Good')
    df['road_condition'] = result.replace({np.nan: None}).values
    print(f"  road_condition source: D4 {n_d4:,}, breach Good/Bad {int(miss.sum()):,}, "
          f"blank {int(df['road_condition'].isna().sum()):,}")
    return df


def _ca_rating(df, d, thr):
    v = num(df['metric_value'])
    if d == 'cracking':
        return crack_condition(v, thr['cracking'])
    if d == 'rutting':
        return rutting_condition(v, thr['rutting'])
    if d == 'roughness':
        return roughness_condition(num(df['roughness_bi']), num(df['iri_value']), thr['roughness'])
    return ravelling_condition(v, thr['ravelling'])


def apply_pothole_ratings(df, prj):
    """rating_ca / rating_irc of a pothole = D4 defect report 'Visual Condition - Pothole (CA)' / '(IRC)' of the
       same lane + 10 m segment (numbers 1–5 already turned into labels). Not in D4 → NULL (never the severity)."""
    lane = df['lane'].astype(str).str.upper().values
    seg = (pd.to_numeric(df['start_chainage'], errors='coerce') // 10 * 10).astype('Int64').values
    for k in ['ca', 'irc']:
        lab = d4_condition(prj, 'pothole', k, 10)
        vals = [lab.get((l, int(s))) if s is not pd.NA and (l, int(s)) in lab.index else None
                for l, s in zip(lane, seg)] if len(lab) else [None] * len(df)
        df[f'rating_{k}'] = vals
        df[f'rating_{k}_source'] = [f'D4 Visual Condition - Pothole ({k.upper()})' if v is not None else None for v in vals]
        print(f"  rating_{k} from D4 Visual Condition - Pothole ({k.upper()}): "
              f"{sum(v is not None for v in vals):,} of {len(vals):,} potholes")
    return df


def apply_ratings(df, table, prj):
    d = defect_of(table)
    if d == 'pothole':
        df['rating_ca'] = coalesce(df['rating_ca'], df['severity'])
        df['rating_irc'] = coalesce(df['rating_irc'], df['severity'])
        return df
    std = df['threshold_name'].fillna('').str.lower()
    has = num(df['metric_value']).notna()
    is_ca, is_irc = std.str.contains('ca') & ~std.str.contains('irc'), std.str.contains('irc')
    ca_own = coalesce(_ca_rating(df, d, prj.thr['ca']), df['rating_ca'], df['severity'],
                      pd.Series(np.where(has, np.where(df['breach_flag'] == 1, 'Bad', 'Good'), None), index=df.index))
    irc_own = coalesce(df['road_condition'],
                       pd.Series(np.where(has, np.where(df['breach_flag'] == 1, 'Poor', 'Good'), None), index=df.index))
    pair = df['id'].str.replace('_irc_', '_', regex=False)
    df['rating_ca'] = pair.map(pd.Series(ca_own[is_ca].values, index=pair[is_ca].values))
    df['rating_irc'] = pair.map(pd.Series(irc_own[is_irc].values, index=pair[is_irc].values))
    return df


# ── column order (used when no schema workbook) ─────────────────────────────
COLUMN_ORDER = [
    'id', 'collection_name', 'defect_base', 'level', 'defect_id', 'display_defect_id', 'source_defect_id', 'record_id',
    'parent_id', 'upc', 'upc_code', 'zone', 'phase', 'cycle', 'nh_number', 'section_code', 'lane', 'side', 'direction',
    'start_chainage', 'end_chainage', 'defect_chainage', 'length_m', 'total_length',
    'parent_start_chainage', 'parent_end_chainage',
    'start_lat', 'start_long', 'end_lat', 'end_long', 'defect_lat', 'defect_long',
    'metric_name', 'metric_value', 'rutting_avg', 'roughness_bi', 'iri_value', 'ravelling_area', 'ravelling_area_sqm', 'cracking_pct',
    'area_sqm', 'iri_condition', 'measures', 'nsv',
    'severity', 'severity_source', 'threshold_name', 'threshold_value', 'breach_flag',
    'affected_length', 'affected_length_ca', 'affected_length_irc',
    'road_condition', 'rating_ca', 'rating_ca_source', 'rating_irc', 'rating_irc_source', 'pci',
    'total_children', 'segment_count', 'reporting_interval_m',
    'defect_type', 'type_of_defect', 'defect_type_filter', 'category', 'pavement_type', 'carriageway',
    'image_name', 'image_urls',
    'datetime', 'observed_at', 'survey_start_date', 'survey_end_date', 'detected_on',
    'source_system', 'sources', 'project_details', 'is_duplicate', 'has_standard_defect', 'standard_flags',
    'created_at', 'updated_at', 'run_date']


def order_columns(df):
    first = [c for c in COLUMN_ORDER if c in df.columns]
    return df[first + [c for c in df.columns if c not in first]]


# ── schema workbook alignment (optional) ────────────────────────────────────
SHEET_FOR_TABLE = {'master_pothole': 'pothole', 'master_cracking': 'cracking',
                   **{f"master_{d}_{l}": f"{d}_{l}" for d in ['roughness', 'rutting', 'ravelling']
                      for l in ['parent', 'child_10m', 'child_100m']}}
EXTRA_COLS = {'affected_length': 'double', 'total_length': 'double', 'affected_length_ca': 'double',
              'affected_length_irc': 'double', 'road_condition': 'text', 'severity_source': 'text',
              'ravelling_area_sqm': 'double', 'rating_ca_source': 'text', 'rating_irc_source': 'text'}


def _schema_cols(path, sheet):
    df = pd.read_excel(path, sheet_name=sheet, header=None, skiprows=2).iloc[:, :3]
    df.columns = ['num', 'column', 'type']
    df = df[num(df['num']).notna()]
    cols = {str(c).strip().lower(): str(t).strip().lower() for c, t in zip(df['column'], df['type'])}
    for c, t in EXTRA_COLS.items():
        cols.setdefault(c, t)
    return cols


def _cast(s, pg):
    if 'text[]' in pg:
        return s.map(lambda v: v if isinstance(v, list) or v is None else [v])
    if 'timestamp' in pg:
        return pd.to_datetime(s, errors='coerce')
    if pg == 'date':
        return pd.to_datetime(s, errors='coerce').dt.date
    if 'boolean' in pg:
        return s.astype('boolean')
    if 'smallint' in pg or 'integer' in pg or 'bigint' in pg:
        return num(s).round().astype('Int64')
    if any(k in pg for k in ['numeric', 'double', 'real', 'float']):
        return num(s).astype(float)
    return s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))


def align_schema(df, table, prj):
    path = prj.schema_workbook
    sheet = SHEET_FOR_TABLE.get(table)
    if not (path and os.path.exists(path) and sheet):
        return order_columns(df)
    cols = _schema_cols(path, sheet)
    missing = [c for c in cols if c not in df.columns]
    dropped = [c for c in df.columns if c not in cols]
    for c in missing:
        df[c] = None
    print(f"  schema {table}: {len(cols)} cols (filled-null {len(missing)}, dropped {len(dropped)})")
    return pd.DataFrame({c: _cast(df[c], t) for c, t in cols.items()})


LINEAR_DEFECTS = ('rutting', 'roughness', 'ravelling')


def apply_rating_columns(df, table, prj, level):
    """rating_ca / rating_irc (rutting, roughness, ravelling, cracking):
       10 m          D4 Visual Condition of that standard (CA → rating_ca, IRC → rating_irc), as it is;
                     if D4 has none → calculated
       100 m / 1 km  calculated (the metric_value here is already the aggregated value)
       calculated  = metric_value vs that standard's D2 threshold with rating_ranges → label like
                     'Fair (10-20mm)'; no D2 threshold → NULL. Same value on the CA row and its IRC twin."""
    d = defect_of(table)
    metric = num(df['metric_value'])
    lane, start = df['lane'], df['start_chainage'].astype('int64')
    counts = {}
    for k, col in [('ca', 'rating_ca'), ('irc', 'rating_irc')]:
        calc = classify(metric, ranges_for(prj, 'rating_ranges', d, k), prj.thr[k][d])
        val = pd.Series(None, index=df.index, dtype=object)
        src = pd.Series(None, index=df.index, dtype=object)
        if level == 'child_10m':
            look = d4_condition(prj, d, k, 10)
            if len(look):
                v = pd.Series(look.reindex(pd.MultiIndex.from_arrays([lane, start // 10 * 10])).values, index=df.index)
                val[v.notna()] = v[v.notna()]
                src[v.notna()] = f'D4 Visual Condition ({k.upper()})'
        miss = val.isna() & calc.notna()
        val[miss] = calc[miss]
        if miss.any():
            src[miss] = (f"calculated ({k.upper()} threshold {prj.thr[k][d]:g}, "
                         f"{'10 m value' if level == 'child_10m' else 'aggregated value'})")
        df[col] = val.values
        df[f'{col}_source'] = src.values
        counts[k] = (int(src.fillna('').str.startswith('D4').sum()), int(miss.sum()))
    print(f"  rating_ca: D4 {counts['ca'][0]:,}, calculated {counts['ca'][1]:,} | "
          f"rating_irc: D4 {counts['irc'][0]:,}, calculated {counts['irc'][1]:,}")
    return df


def apply_linear_conditions(df, table, prj):
    """Rutting / roughness / ravelling, every level. SEPARATE fields — none is copied from another:
       severity        10 m: D4 Visual Condition of the row's standard (CA row → CA, IRC row → IRC), as it is;
                       missing, or 100 m / 1 km: calculated from this row's (aggregated) metric_value with
                       severity_ranges of the row's standard × that standard's threshold
       rating_ca       calculated from this row's (aggregated) metric_value with rating_ranges['ca'] × CA threshold
       rating_irc      calculated from this row's (aggregated) metric_value with rating_ranges['irc'] × IRC threshold
       breach_flag     set when the rows were built (metric_value vs threshold_value, operator from D2, default >=);
                       NULL when D2 has no threshold
       road_condition  'Poor' if breach_flag = 1 else 'Good'; NULL when metric_value or breach_flag is missing"""
    d = defect_of(table)
    level = 'parent' if table.endswith('_parent') else 'child_100m' if table.endswith('_100m') else 'child_10m'
    df = apply_severity(df, d, level, prj)
    if d == 'roughness':
        df['iri_condition'] = df['severity']          # existing rule: iri_condition = severity

    df = apply_rating_columns(df, table, prj, level)
    return _linear_road_condition(df)


def apply_severity(df, d, level, prj):
    """severity per row's standard (rutting, roughness, ravelling, cracking):
       10 m          D4 Visual Condition of the row's standard (CA row → CA label, IRC row → IRC label), as it is;
                     if D4 has none → calculated
       100 m / 1 km  calculated from the aggregated metric_value
       calculated  = severity_ranges of the row's standard × that standard's D2 threshold (no threshold → NULL)"""
    std = pd.Series(np.where(df['threshold_name'].fillna('').str.lower().str.contains('irc'), 'irc', 'ca'), index=df.index)
    metric = num(df['metric_value'])
    lane, start = df['lane'], df['start_chainage'].astype('int64')
    sev = pd.Series(None, index=df.index, dtype=object)
    src = pd.Series(None, index=df.index, dtype=object)
    for k in ['ca', 'irc']:
        m = std == k
        if not m.any():
            continue
        if level == 'child_10m':
            look = d4_condition(prj, d, k, 10)
            if len(look):
                v = pd.Series(look.reindex(pd.MultiIndex.from_arrays([lane[m], start[m] // 10 * 10])).values,
                              index=df.index[m])
                sev[m] = v
                src[m & sev.notna()] = f'D4 Visual Condition ({k.upper()})'
        miss = m & sev.isna()
        if miss.any():
            sev[miss] = classify(metric[miss], ranges_for(prj, 'severity_ranges', d, k), prj.thr[k][d])
            src[miss & sev.notna()] = f'calculated (severity_ranges {k.upper()}, {"aggregated value" if level != "child_10m" else "10 m value"})'
    df['severity'] = sev.values
    df['severity_source'] = src.values
    n_d4 = int(src.fillna('').str.startswith('D4').sum())
    print(f"  severity: D4 {n_d4:,}, calculated {int(src.fillna('').str.startswith('calc').sum()):,}, "
          f"blank {int(sev.isna().sum()):,}")
    return df


def _linear_road_condition(df):
    """Rutting / roughness / ravelling: road_condition ONLY from breach_flag (Poor / Good).
       Missing metric or no D2 threshold (breach_flag NULL) → NULL, never 'Good'."""
    metric = num(df['metric_value'])
    bf = pd.to_numeric(df['breach_flag'], errors='coerce')
    df['road_condition'] = np.where(metric.isna() | bf.isna(), None, np.where(bf == 1, 'Poor', 'Good'))
    print("  road_condition from breach_flag")
    return df


def finalize(df, table, prj):
    if df.empty:
        return df
    df = apply_pci(df, table, prj)
    # reporting_interval_m per row's standard, from D2 only (CA rows: 'Comparison as per CA', IRC rows: 'as per IRC')
    d = defect_of(table)
    is_irc = df['threshold_name'].fillna('').str.lower().str.contains('irc').values
    iv = {s: prj.d2_interval(s, d) for s in ('CA', 'IRC')}
    df['reporting_interval_m'] = np.where(is_irc, iv['IRC'], iv['CA'])
    df['reporting_interval_m'] = pd.to_numeric(df['reporting_interval_m'], errors='coerce')
    if defect_of(table) in LINEAR_DEFECTS:
        df = apply_linear_conditions(df, table, prj)
    elif defect_of(table) == 'pothole':     # road_condition ONLY from breach_flag; ratings from D4 Visual Condition
        df['road_condition'] = df['breach_flag'].map(config.POTHOLE_ROAD_CONDITION).values
        print(f"  road_condition from breach_flag: " +
              ", ".join(f"{k} {v:,}" for k, v in df['road_condition'].value_counts().items()))
        df = apply_pothole_ratings(df, prj)
    else:                                   # cracking: road_condition D4 → breach; severity per standard
        df = apply_road_condition(df, table, prj)
        df = apply_ratings(df, table, prj)
        if defect_of(table) == 'cracking':  # 10 m: D4 Visual Condition else calculated; 100 m / 1 km: calculated
            level = 'parent' if table.endswith('_parent') else 'child_100m' if table.endswith('_100m') else 'child_10m'
            df = apply_severity(df, 'cracking', level, prj)   # per row's standard, like rutting / roughness
            df = apply_rating_columns(df, table, prj, level)
    return align_schema(df, table, prj)
