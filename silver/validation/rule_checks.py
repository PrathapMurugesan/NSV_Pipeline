"""
Rule checks on the GENERATED rutting / roughness / ravelling tables (10 m, 100 m, 1 km), CA and IRC separately.

Expected values are recomputed here INDEPENDENTLY from D4 and the old-notebook formulas (not with the
pipeline's own functions), then compared row by row with the written tables:
  source_value      10 m metric = D4 value (rutting mm, BI = 630 × IRI^1.12 or D4 BI when IRI is blank,
                    ravelling area × 100 / 35); 100 m / 1 km = mean of the 10 m / 100 m values, except
                    ravelling % = total sq m × 100 ÷ 350 (100 m) / 3,500 (1 km). Blank stays blank.
  threshold_value   = D2 threshold of the row's standard (no defaults; not in D2 → NULL)
  breach_flag       = metric_value vs threshold_value with the standard's operator (default >=); no D2 threshold → NULL
  road_condition    = Poor if breach_flag = 1 else Good; NULL when metric_value or breach_flag is blank
  severity          10 m: D4 Visual Condition of the row's standard; 100 m / 1 km: old-notebook style
                    classification of the aggregated value
  rating_ca/_irc    old-notebook formulas with the CA / IRC threshold (IRC roughness Good < 2000), same on twins
  no_dropped_rows   every D4 lane-block has a row, also when its value is blank; no 0 replaces a missing value
  boundary          exact threshold values pushed through the pipeline's classifier and breach function
Writes <UPC output>/dq/rule_checks.csv. Run alone: python validation/rule_checks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

LEVELS = {'child_10m': 10, 'child_100m': 100, 'parent': 1000}
DEFECTS = ['rutting', 'roughness', 'ravelling']
VC = {'rutting': 'vc_rut', 'roughness': 'vc_rough', 'cracking': 'vc_crack', 'ravelling': 'vc_rav'}


def _f(x):
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:.2f}".rstrip('0').rstrip('.')


# ── old-notebook formulas, written out (independent of defect_rules.json parsing) ──
def exp_rating(defect, std, v, t):
    if pd.isna(v) or t is None:              # no D2 threshold → no calculated label
        return None
    if defect == 'rutting':
        fl = round(t * 0.5)
        return f'Good (<{_f(fl)}mm)' if v < fl else f'Fair ({_f(fl)}-{_f(t)}mm)' if v <= t else f'Poor (>{_f(t)}mm)'
    if defect == 'roughness':
        fl = 2000.0 if std == 'irc' else round(t * 0.8)
        return f'Good (<{_f(fl)}mm/km)' if v < fl else f'Fair ({_f(fl)}-{_f(t)}mm/km)' if v <= t else f'Poor (>{_f(t)}mm/km)'
    if defect == 'cracking':                       # old notebook crack_condition_from_pct
        b2, b3, b4 = round(t * 2), round(t * 4), round(t * 6)
        return (f'Very Good (<{_f(t)}%)' if v < t else f'Good ({_f(t)}-{_f(b2)}%)' if v <= b2 else
                f'Fair ({_f(b2)}-{_f(b3)}%)' if v <= b3 else f'Poor ({_f(b3)}-{_f(b4)}%)' if v <= b4 else f'Very Poor (>{_f(b4)}%)')
    h = round(t * 0.5)
    if v == 0:
        return 'Very Good (0)'
    return f'Good (1-{_f(h)}%)' if v <= h else f'Fair ({_f(h)}-{_f(t)}%)' if v < t else f'Poor (>={_f(t)}%)'


def exp_severity_calc(defect, std, v, t):
    if pd.isna(v) or t is None:
        return None
    if defect in ('rutting', 'roughness') and std == 'ca':
        u = 'mm' if defect == 'rutting' else 'mm/km'
        return f'Good (<={_f(t)}{u})' if v <= t else f'Poor (>{_f(t)}{u})'
    return exp_rating(defect, std, v, t)


def _expected_metric(prj, defect, level):
    """Independent recomputation of metric_value (and ravelling sq m) per (lane, block start)."""
    d4 = prj.d4_long
    x = d4[['lane', 'start_chainage', 'rut', 'iri', 'bi', 'rav_area']].copy()     # every row, blanks included
    if defect == 'rutting':
        x['m'] = x['rut']
    elif defect == 'roughness':
        x['m'] = (630.0 * x['iri'] ** 1.12).fillna(x['bi'])          # D4 BI only when IRI is blank
    else:
        x['m'] = x['rav_area'] * 100.0 / 35.0
    g = LEVELS[level]
    x['blk'] = x['start_chainage'].astype('int64') // g * g
    if level == 'child_10m':
        return x.set_index(['lane', 'blk'])[['m', 'rav_area']].rename(columns={'rav_area': 'sqm'}), x
    import config
    # 100 m = mean of the 10 m values; 1 km = mean of the 100 m values (sq m: sums)
    x['b100'] = x['start_chainage'].astype('int64') // 100 * 100
    a100 = x.groupby(['lane', 'b100']).agg(m=('m', 'mean'), iri=('iri', 'mean'),
                                           sqm=('rav_area', lambda s: s.sum(min_count=1))).reset_index()
    if defect == 'roughness' and config.ROUGHNESS_AGGREGATION == 'iri_formula':
        a100['m'] = (630.0 * a100['iri'] ** 1.12).fillna(a100['m'])      # no IRI in block → mean of D4 BI
    if defect == 'ravelling':
        a100['m'] = a100['sqm'] * 100.0 / 350.0
    if level == 'child_100m':
        return a100.rename(columns={'b100': 'blk'}).set_index(['lane', 'blk'])[['m', 'sqm']], x
    if config.PARENT_AGGREGATION == 'from_10m':          # 1 km = average of all 10 m values in the km
        x['blk'] = x['start_chainage'].astype('int64') // 1000 * 1000
        a1k = x.groupby(['lane', 'blk']).agg(m=('m', 'mean'), iri=('iri', 'mean'),
                                             sqm=('rav_area', lambda s: s.sum(min_count=1)))
    else:                                                 # 1 km = average of the 100 m averages
        a100['blk'] = a100['b100'] // 1000 * 1000
        a1k = a100.groupby(['lane', 'blk']).agg(m=('m', 'mean'), iri=('iri', 'mean'), sqm=('sqm', lambda s: s.sum(min_count=1)))
    if defect == 'roughness' and config.ROUGHNESS_AGGREGATION == 'iri_formula':
        a1k['m'] = (630.0 * a1k['iri'] ** 1.12).fillna(a1k['m'])
    if defect == 'ravelling':
        a1k['m'] = a1k['sqm'] * 100.0 / 3500.0
    return a1k[['m', 'sqm']], x


def _row(table, std, check, n, bad, note=''):
    return {'table': table, 'std': std, 'check': check, 'rows_checked': int(n), 'failures': int(bad),
            'status': 'PASS' if bad == 0 else 'FAIL', 'note': note}


def check_table(prj, table, df):
    d = next(x for x in DEFECTS if x in table)
    level = next(l for l in LEVELS if table.endswith(l))
    out = []
    exp, raw = _expected_metric(prj, d, level)
    g = LEVELS[level]
    key = pd.MultiIndex.from_arrays([df['lane'], df['start_chainage'].astype('int64') // g * g])
    e = exp.reindex(key)
    for k, name in [('ca', 'As per CA'), ('irc', 'As per IRC')]:
        m = (df['threshold_name'] == name).values
        s = df[m]
        es = e[m]
        t, op = prj.thr[k][d], prj.thr_op(k, d)
        mv = pd.to_numeric(s['metric_value'], errors='coerce')
        # source value
        bad = int((~np.isclose(mv.values, es['m'].values, rtol=1e-9, atol=1e-6, equal_nan=True)).sum())
        out.append(_row(table, k.upper(), 'source_value', len(s), bad))
        if d == 'ravelling':
            sq = pd.to_numeric(s['ravelling_area_sqm'], errors='coerce').values
            bad = int((~np.isclose(sq, es['sqm'].values, atol=1e-9, equal_nan=True)).sum())
            out.append(_row(table, k.upper(), 'ravelling_area_sqm', len(s), bad))
        # threshold / breach / road_condition
        tv = pd.to_numeric(s['threshold_value'], errors='coerce')
        bf = pd.to_numeric(s['breach_flag'], errors='coerce')
        if t is None:                        # not in D2 → threshold / breach / road_condition all NULL
            out.append(_row(table, k.upper(), 'threshold_value', len(s), int(tv.notna().sum()), 'NOT IN D2 → NULL'))
            out.append(_row(table, k.upper(), 'breach_flag', len(s), int(bf.notna().sum()), 'no D2 threshold → NULL'))
        else:
            out.append(_row(table, k.upper(), 'threshold_value', len(s), int((tv != t).sum()),
                            f"{op} {t} ({prj.thr_src.get((k, d), '')})"))
            exp_b = ((mv > t) if op == '>' else (mv >= t)).astype(int)
            out.append(_row(table, k.upper(), 'breach_flag', len(s), int((bf.values != exp_b.values).sum()), f"operator {op}"))
        exp_rc = pd.Series(np.where(mv.isna().values | bf.isna().values, None, np.where(bf == 1, 'Poor', 'Good')))
        got_rc = pd.Series(s['road_condition'].values)
        out.append(_row(table, k.upper(), 'road_condition', len(s),
                        int((got_rc.fillna('∅').values != exp_rc.fillna('∅').values).sum()),
                        'NULL when metric is blank or there is no D2 threshold'))
        # severity
        if level == 'child_10m' and d in VC:
            lab = prj.d4_long.set_index(['lane', 'start_chainage'])[f'{VC[d]}_{k}']
            lab = lab[~lab.index.duplicated()]
            ev = lab.reindex(pd.MultiIndex.from_arrays([s['lane'], s['start_chainage']])).values
            ev = np.where(pd.isna(ev), [exp_severity_calc(d, k, v, t) for v in mv], ev)
            note = 'D4 Visual Condition (calculated where D4 is blank)'
        else:
            ev = np.array([exp_severity_calc(d, k, v, t) for v in mv], dtype=object)
            note = 'calculated from the ' + ('10 m' if level == 'child_10m' else 'aggregated') + ' value'
        bad = int((pd.Series(s['severity'].values).fillna('∅').values != pd.Series(ev).fillna('∅').values).sum())
        out.append(_row(table, k.upper(), 'severity', len(s), bad, note))
    out += _rating_checks(prj, table, df, d, level)
    # no row dropped for a blank value, blank only where D4 is blank, no missing value turned into 0
    mv = pd.to_numeric(df['metric_value'], errors='coerce')
    n_rows = len(df[df['threshold_name'] == 'As per CA'])
    n_exp = len(exp)
    out.append(_row(table, 'CA', 'no_dropped_rows', n_rows, abs(n_rows - n_exp),
                    f"{n_rows} rows vs {n_exp} D4 lane-blocks (blank values included)"))
    out.append(_row(table, '-', 'blank_metric_only_where_d4_blank', len(df),
                    int((mv.isna() & pd.Series(e['m'].values, index=df.index).notna()).sum()),
                    f"{int(mv.isna().sum()):,} rows kept with a blank metric"))
    if level == 'child_10m':
        zero_bad = int(((mv == 0) & es_isnan(e['m'])).sum())
    else:
        zero_bad = int(((mv == 0) & ~np.isclose(e['m'].values, 0)).sum())
    out.append(_row(table, '-', 'no_zero_for_missing', len(df), zero_bad))
    return out


def _rating_checks(prj, table, df, d, level):
    """rating_ca / rating_irc: 10 m → D4 label of that standard where present, else formula; 100 m / 1 km → formula."""
    out = []
    mv = pd.to_numeric(df['metric_value'], errors='coerce')
    for k, col in [('ca', 'rating_ca'), ('irc', 'rating_irc')]:
        t = prj.thr[k][d]
        ev = pd.Series([exp_rating(d, k, v, t) for v in mv], index=df.index, dtype=object)
        n_d4 = 0
        if level == 'child_10m' and d in VC:
            d4 = prj.d4_long
            lab = pd.Series(d4[f'{VC[d]}_{k}'].values, index=pd.MultiIndex.from_arrays(
                [d4['lane'], d4['start_chainage'].astype('int64') // 10 * 10]))    # same 10 m key on both sides
            lab = lab[lab.notna()]
            lab = lab[~lab.index.duplicated()]
            l4 = pd.Series(lab.reindex(pd.MultiIndex.from_arrays(
                [df['lane'], df['start_chainage'].astype('int64') // 10 * 10])).values, index=df.index)
            n_d4 = int(l4.notna().sum())
            ev = l4.where(l4.notna(), ev)
        bad = int((df[col].fillna('∅').values != ev.fillna('∅').values).sum())
        out.append(_row(table, k.upper(), col, len(df), bad,
                        (f'D4 label {n_d4:,} rows, formula {len(df) - n_d4:,} rows' if level == 'child_10m'
                         else 'formula on the aggregated value')))
    ca, irc = df[df['threshold_name'] == 'As per CA'], df[df['threshold_name'] == 'As per IRC']
    for col in ['rating_ca', 'rating_irc']:
        out.append(_row(table, 'CA/IRC', f'{col}_same_on_twins', len(ca),
                        int((ca[col].fillna('∅').values != irc[col].fillna('∅').values).sum())))
    return out


def es_isnan(s):
    return pd.Series(s).isna().values


def boundary_tests(prj):
    """Exact threshold values through the pipeline's own classifier + breach function."""
    from common.defects import classify, ranges_for
    from common.project import breach
    out = []
    for d in DEFECTS + ['cracking']:
        for k in ['ca', 'irc']:
            t, op = prj.thr[k][d], prj.thr_op(k, d)
            if t is None:                    # no D2 threshold → nothing to test at the limits
                out.append(_row(f'boundary:{d}', k.upper(), 'no_d2_threshold', 0, 0, 'skipped: threshold not in D2'))
                continue
            eps = 1e-6
            vals = [0.0, t - eps, t, t + eps]
            if d == 'rutting':
                vals += [round(t * 0.5) - eps, round(t * 0.5)]
            if d == 'roughness':
                fl = 2000.0 if k == 'irc' else round(t * 0.8)
                vals += [fl - eps, fl]
            if d == 'ravelling':
                vals += [round(t * 0.5), round(t * 0.5) + eps]
            if d == 'cracking':
                vals += [round(2 * t), round(2 * t) + eps, round(4 * t), round(6 * t), round(6 * t) + eps]
            got = classify(pd.Series(vals), ranges_for(prj, 'rating_ranges', d, k), t).values
            want = [exp_rating(d, k, v, t) for v in vals]
            bad = sum(a != b for a, b in zip(got, want))
            out.append(_row(f'boundary:{d}', k.upper(), 'rating_at_exact_limits', len(vals), bad,
                            '; '.join(f"{_f(v) if v == round(v, 6) else round(v, 6)}→{g}" for v, g in zip(vals, got))))
            b = breach(pd.Series([t - eps, t, t + eps]), t, op).tolist()
            want_b = [0, 1 if op == '>=' else 0, 1]
            out.append(_row(f'boundary:{d}', k.upper(), 'breach_at_threshold', 3, int(b != want_b),
                            f"operator {op}: T-ε→{b[0]}, T→{b[1]}, T+ε→{b[2]}"))
    return out


def cross_level_check(prj, tables_by_name):
    """1 km metric_value of the GENERATED parent table vs the GENERATED lower-level table in that km:
       PARENT_AGGREGATION 'from_10m' → the 10 m table, 'from_100m' → the 100 m table.
       Averages (rut, BI); roughness with 'iri_formula' = 630 × (average IRI)^1.12; ravelling = total sq m ÷ 3,500."""
    import config
    src_level = 'child_10m' if config.PARENT_AGGREGATION == 'from_10m' else 'child_100m'
    out = []
    for d in DEFECTS:
        p, c = tables_by_name.get(f'master_{d}_parent'), tables_by_name.get(f'master_{d}_{src_level}')
        if p is None or c is None or p.empty or c.empty:
            continue
        for name in ['As per CA', 'As per IRC']:
            cc = c[c['threshold_name'] == name].assign(km=lambda z: z['start_chainage'].astype('int64') // 1000 * 1000)
            grp = cc.groupby(['lane', 'km'])
            if d == 'ravelling':          # 1 km % = total sq m × 100 ÷ 3,500
                exp = grp['ravelling_area_sqm'].sum(min_count=1) * 100.0 / 3500.0
            elif d == 'roughness' and config.ROUGHNESS_AGGREGATION == 'iri_formula':
                exp = (630.0 * grp['iri_value'].mean() ** 1.12).fillna(grp['metric_value'].mean())
            else:
                exp = grp['metric_value'].mean()
            pp = p[p['threshold_name'] == name]
            got = pd.Series(pp['metric_value'].values, index=pd.MultiIndex.from_arrays(
                [pp['lane'], pp['start_chainage'].astype('int64') // 1000 * 1000]))
            e = exp.reindex(got.index).values
            bad = int((~np.isclose(got.values.astype(float), e.astype(float), rtol=1e-9, atol=1e-6, equal_nan=True)).sum())
            out.append(_row(f'master_{d}_parent', name[7:], f'1km_from_generated_{src_level[6:]}', len(pp), bad))
    return out


def run_rule_checks(prj, tables=None):
    rows = []
    loaded = {}
    for d in DEFECTS:
        for l in LEVELS:
            t = f'master_{d}_{l}'
            df = (tables or {}).get(t)
            if df is None:
                f = os.path.join(prj.out_dir, 'dlm_kpi', f'{t}.parquet')
                df = pd.read_parquet(f) if os.path.exists(f) else None
            if df is None or df.empty:
                continue
            loaded[t] = df
            rows += check_table(prj, t, df)
    rows += cross_level_check(prj, loaded)
    crk = (tables or {}).get('master_cracking')
    if crk is None:
        f = os.path.join(prj.out_dir, 'dlm_kpi', 'master_cracking.parquet')
        crk = pd.read_parquet(f) if os.path.exists(f) else None
    if crk is not None and len(crk):
        rows += _rating_checks(prj, 'master_cracking', crk, 'cracking', 'child_10m')
    rows += boundary_tests(prj)
    res = pd.DataFrame(rows)
    res.insert(0, 'upc', prj.upc)
    os.makedirs(os.path.join(prj.out_dir, 'dq'), exist_ok=True)
    res.to_csv(os.path.join(prj.out_dir, 'dq', 'rule_checks.csv'), index=False)
    n_fail = int((res['status'] == 'FAIL').sum())
    print(f"\nRule checks on generated tables: {len(res) - n_fail}/{len(res)} PASS")
    if n_fail:
        print(res[res['status'] == 'FAIL'].to_string(index=False))
    return res


if __name__ == '__main__':
    from common.output import parse_args, make_project
    run_rule_checks(make_project(parse_args()))
