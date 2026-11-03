"""
DQ checks: does the silver output reproduce D2?   (per UPC, Prathap: "498 of 500 rows match")

For every check:  D2 rows (numeric) · matched · match % · PASS when match % ≥ config.DQ_PASS_PCT (98)
  threshold  every defect + standard must have its threshold in D2 (no defaults): missing → FAIL
  value      rutting / roughness BI / cracking % per lane, at D2's interval (100 m → child_100m, 1 km → parent)
             |D2 − silver| ≤ DQ_TOLERANCE (roughness BI: DQ_TOLERANCE_BY_DEFECT, 1 mm/km)
             cracking (D2 100 m crack %), three checks:
               master_cracking            10 m rows averaged per 100 m block  vs D2 100 m
               master_cracking_child_100m 100 m value                         vs D2 100 m
               master_cracking_parent     1 km value                          vs average of the D2 100 m values of the km
  breach     per lane vs silver breach_flag:
               CA  — D2 breach flag per lane ('Current Cycle | L1 Rut Depth' … 0/1)
               IRC — the D2 IRC sheet has lane VALUES but no per-lane flags, so the D2 breach is derived:
                     D2 IRC lane value vs the D2 IRC threshold with its operator
  failing_lanes  CA AND IRC: D2 'Current Cycle | Roughness / Rutting / Cracking | Failing Lanes' (Yes / No per block)
             vs silver: 'Yes' when ANY lane of that block has breach_flag = 1 in the 100 m (or 1 km) table of the
             same standard. Only D2 rows that carry the defect's current-cycle reporting interval are used.
  d2_sheet   a missing 'Comparison as per CA' / 'as per IRC' sheet is listed as NOT RUN: that standard's silver rows
             are not compared with D2 (the other sheet is never used in its place)
  pothole    D2 'Potholes' sheet area per lane per 100 m vs master_pothole area   |diff| ≤ DQ_AREA_TOLERANCE
             (D2 rows with severity in DQ_POTHOLE_EXCLUDE_SEVERITY, e.g. Delamination, are not potholes)

Writes  <UPC output>/dq/dq_summary.csv     one row per check
        <UPC output>/dq/dq_mismatches.csv  every D2 row that did not match (bin, D2 value, silver value, reason)
Run alone (after the tables were written):  python validation/dq_checks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.utils import pick, num, parse_chainage, norm_name
from common.project import breach

NEEDED = ['master_rutting_child_100m', 'master_rutting_parent', 'master_roughness_child_100m',
          'master_roughness_parent', 'master_cracking', 'master_cracking_child_100m', 'master_cracking_parent',
          'master_pothole']

# cracking: (silver table, block size, level label) — every one compared with the D2 100 m crack %
CRACK_CHECKS = [('master_cracking', 100, '100 m (10 m avg)'),
                ('master_cracking_child_100m', 100, '100 m'),
                ('master_cracking_parent', 1000, '1 km (vs D2 100 m avg)')]

# defect → (D2 value key, D2 CA flag key, silver column)
VALUE_CHECKS = {'rutting': ('rut depth', 'rut depth', 'metric_value'),
                'roughness': ('roughness bi', 'lane roughness bi', 'roughness_bi'),
                'cracking': ('cracking lane area', 'crack area', 'metric_value')}


def _load_outputs(prj, tables):
    out = {}
    for t in NEEDED:
        f = os.path.join(prj.out_dir, 'dlm_kpi', f"{t}.parquet")
        if os.path.exists(f):
            out[t] = pd.read_parquet(f)
    out.update({k: v for k, v in (tables or {}).items() if v is not None and len(v)})
    return out


def _silver_bins(prj, tables, defect, std, lane, g, table=None):
    """Silver value + breach per bin of size g for one lane and standard."""
    if defect == 'cracking' and table in ('master_cracking_child_100m', 'master_cracking_parent'):
        s = tables.get(table, pd.DataFrame())
        if s.empty:
            return pd.DataFrame(columns=['silver', 'silver_breach'])
        s = s[(s['threshold_name'] == f'As per {std}') & (s['lane'] == lane)]
        return (s.assign(ch=s['start_chainage'].astype('int64') // g * g)
                .groupby('ch').agg(silver=('metric_value', 'first'), silver_breach=('breach_flag', 'first')))
    if defect == 'cracking':
        s = tables.get('master_cracking', pd.DataFrame())
        if s.empty:
            return pd.DataFrame(columns=['silver', 'silver_breach'])
        s = s[(s['threshold_name'] == f'As per {std}') & (s['lane'] == lane)]
        b = (s.assign(ch=s['start_chainage'].astype('int64') // g * g)
             .groupby('ch').agg(silver=('metric_value', 'mean')))
        k = std.lower()
        b['silver_breach'] = breach(b['silver'], prj.thr[k]['cracking'], prj.thr_op(k, 'cracking'))
        return b
    tbl = f"master_{defect}_{'parent' if g >= 1000 else 'child_100m'}"
    s = tables.get(tbl, pd.DataFrame())
    if s.empty:
        return pd.DataFrame(columns=['silver', 'silver_breach'])
    col = VALUE_CHECKS[defect][2]
    s = s[(s['threshold_name'] == f'As per {std}') & (s['lane'] == lane)]
    return (s.assign(ch=s['start_chainage'].astype('int64') // g * g)
            .groupby('ch').agg(silver=(col, 'first'), silver_breach=('breach_flag', 'first')))


def _d2_interval(ri):
    """D2 reporting interval (m) of a block of rows: most common value of its interval column, 100 when none."""
    return int(round(float(ri.mode().iloc[0]))) if len(ri) else 100


def _decimals(values):
    """Decimals D2 shows for a column (0-4): the fewest k with every value == round(value, k)."""
    v = num(pd.Series(values)).dropna().to_numpy(dtype=float)
    for k in range(5):
        if np.allclose(v, np.round(v, k), rtol=0, atol=1e-9):
            return k
    return 4


def _tolerance(defect, d2_values):
    """|D2 − silver| allowed: the configured tolerance, or one unit of D2's last shown decimal when that is larger
    (config.DQ_TOLERANCE_D2_PRECISION). D2 rounds its 100 m values (rut depth 3.2 for silver 3.15); one full unit,
    not half, because D2 rounds values it averaged from already rounded 10 m values."""
    tol = config.DQ_TOLERANCE_BY_DEFECT.get(defect, config.DQ_TOLERANCE)
    if getattr(config, 'DQ_TOLERANCE_D2_PRECISION', False):
        tol = max(tol, 10.0 ** -_decimals(d2_values))
    return tol + 1e-9                         # 3.2 − 3.1 is 0.10000000000000009 in floating point


def _bi_to_iri(bi):
    """Inverse of BI = 630 × IRI^1.12 (common/segments.py)."""
    return (num(bi).clip(lower=0) / 630.0) ** (1 / 1.12)


def _roughness_iri_ok(d2_bi, silver_bi):
    """D2 builds BI from an IRI it rounded first (D2 BI 881.6869 = 630 × 1.35^1.12). Its BI values then turn back
    into IRI values with few decimals, and the right comparison is in IRI, within one unit of that decimal
    (silver IRI 1.348 vs D2 1.35). When D2's BI does not come from a rounded IRI (≥ 4 decimals back), nothing
    extra passes here and the BI tolerance alone decides."""
    d2_iri, sv_iri = _bi_to_iri(d2_bi), _bi_to_iri(silver_bi)
    k = _decimals(d2_iri)
    if k > 3:
        return pd.Series(False, index=d2_bi.index)
    return ((d2_iri - sv_iri).abs() <= 10.0 ** -k + 1e-9).fillna(False)


def _row(check, std, defect, lane, level, total, matched, skipped, note=''):
    pct = round(100.0 * matched / total, 1) if total else None
    return {'upc': None, 'check': check, 'std': std, 'defect': defect, 'lane': lane, 'level': level,
            'd2_rows': total, 'matched': matched, 'mismatched': total - matched, 'not_numeric_in_d2': skipped,
            'match_%': pct, 'status': ('PASS' if pct is not None and pct >= config.DQ_PASS_PCT else
                                       'FAIL' if pct is not None else 'NO DATA'), 'note': note}


def _crack_checks(prj, tables, summary, mism, std, lane, ref, skipped, gi=100):
    """D2 cracking % (blocks of gi m) vs the three cracking tables (see CRACK_CHECKS).
    gi = D2's interval: the 10 m rows are averaged per gi m block; a 100 m / 1 km table is compared with the
    average of the D2 blocks inside it (only when the D2 blocks fit in it exactly)."""
    tol = _tolerance('cracking', ref['d2'])
    for table, g, level in CRACK_CHECKS:
        if table == 'master_cracking':        # 10 m rows can be averaged to any D2 interval
            g, level = gi, f'{gi} m (10 m avg)'
        if table not in tables:
            summary.append({**_row('value', std, 'cracking', lane, level, 0, 0, skipped),
                            'd2_rows': len(ref), 'status': 'NOT RUN', 'note': f'{table} not in this run'})
            continue
        if g % gi:                            # e.g. D2 200 m blocks vs the 100 m table: not comparable
            summary.append({**_row('value', std, 'cracking', lane, level, 0, 0, skipped),
                            'd2_rows': len(ref), 'status': 'NOT RUN',
                            'note': f'D2 interval {gi} m does not fit in {table} blocks of {g} m'})
            continue
        r = ref.assign(bin=(ref['ch'] // g * g).astype('int64'))
        if g > gi:                            # D2 has no block this size: average of its blocks inside it
            r = r.groupby('bin', as_index=False)['d2'].mean()
        j = r.join(_silver_bins(prj, tables, 'cracking', std, lane, g, table), on='bin', how='left')
        ok = (j['d2'] - num(j['silver'])).abs() <= tol
        summary.append(_row('value', std, 'cracking', lane, level, len(j), int(ok.sum()), skipped, note=table))
        for _, q in j[~ok].iterrows():
            mism.append({'check': 'value', 'std': std, 'defect': 'cracking', 'lane': lane, 'level': level,
                         'bin_start': int(q['bin']), 'd2_value': q['d2'], 'silver_value': q['silver'],
                         'diff': None if pd.isna(q['silver']) else abs(q['d2'] - q['silver']),
                         'reason': (f'no {table} row for this bin' if pd.isna(q['silver']) else f'{table} value differs')})


# defect → (D2 group name in 'Current Cycle | <group> | Failing Lanes', D2 value group for its reporting interval,
#           silver table prefix)
FAILING_LANES = {'roughness': ('roughness', 'roughness bi', 'master_roughness'),
                 'rutting': ('rutting', 'rut depth', 'master_rutting'),
                 'cracking': ('cracking', 'cracking lane area', 'master_cracking')}


def _failing_lanes_checks(prj, tables, summary, mism):
    """D2 'Failing Lanes' (Yes / No per block) vs silver: Yes when any lane of the block breaches (CA and IRC)."""
    for std, d2 in [('CA', prj.d2_ca), ('IRC', prj.d2_irc)]:
        if d2.empty:
            continue
        c = list(d2.columns)
        names = {norm_name(x): x for x in c}
        st = parse_chainage(d2[pick(c, 'chainage', 'start')])
        for defect, (grp, val_grp, prefix) in FAILING_LANES.items():
            flc = names.get(f'current cycle | {grp} | failing lanes')
            if not flc:
                continue
            ric = names.get(f'current cycle | {val_grp} | reporting interval')
            iv = num(d2[ric]) if ric else pd.Series(100.0, index=d2.index)
            rows = iv.notna()
            if not rows.any():
                summary.append({**_row('failing_lanes', std, defect, '-', '-', 0, 0, 0),
                                'note': 'no current-cycle reporting interval in D2 (no current data)'})
                continue
            gi = _d2_interval(iv[rows])
            g = 1000 if gi >= 1000 else 100
            # cracking at another D2 interval (e.g. 50 m): 10 m rows averaged per D2 block, breach per lane
            from_10m = defect == 'cracking' and gi not in (100, 1000) and gi < 1000 and gi % 10 == 0
            if from_10m:
                g = gi
            level = '1 km' if g == 1000 else f'{g} m'
            table = 'master_cracking' if from_10m else f"{prefix}_{'parent' if g == 1000 else 'child_100m'}"
            flag = d2.loc[rows, flc].astype(str).str.strip().str.title()
            ok_val = flag.isin(['Yes', 'No'])
            skipped = int((~ok_val).sum())
            ref = pd.DataFrame({'bin': (st[rows][ok_val] // g * g), 'd2': flag[ok_val]}).dropna(subset=['bin'])
            ref['bin'] = ref['bin'].astype('int64')
            if gi not in (100, 1000) and not from_10m:
                summary.append({**_row('failing_lanes', std, defect, '-', f'{gi} m', 0, 0, skipped),
                                'd2_rows': len(ref), 'status': 'NOT RUN',
                                'note': f'D2 interval {gi} m: silver has {defect} only at 100 m and 1 km'})
                continue
            if from_10m and table in tables:
                lanes = sorted(prj.d4_long['lane'].unique())
                per_lane = [_silver_bins(prj, tables, 'cracking', std, ln, g)['silver_breach'] for ln in lanes]
                sil = (pd.concat(per_lane).groupby(level=0).max().map({1: 'Yes', 0: 'No', 1.0: 'Yes', 0.0: 'No'})
                       if per_lane else pd.Series(dtype=object))
                j = ref.join(sil.rename('silver'), on='bin')
                ok = j['d2'] == j['silver']
                summary.append(_row('failing_lanes', std, defect, '-', level, len(j), int(ok.sum()), skipped,
                                    note=f"master_cracking 10 m rows averaged per {g} m: Yes when any lane breaches"))
                for _, q in j[~ok].iterrows():
                    mism.append({'check': 'failing_lanes', 'std': std, 'defect': defect, 'lane': '-', 'level': level,
                                 'bin_start': int(q['bin']), 'd2_value': q['d2'], 'silver_value': q['silver'],
                                 'diff': None, 'reason': 'no silver row for this block' if pd.isna(q['silver'])
                                 else 'Failing Lanes differs'})
                continue
            if table not in tables:
                summary.append({**_row('failing_lanes', std, defect, '-', level, 0, 0, skipped),
                                'd2_rows': len(ref), 'status': 'NOT RUN', 'note': f'{table} not in this run'})
                continue
            s = tables[table]
            s = s[s['threshold_name'] == f'As per {std}']
            bf = pd.to_numeric(s['breach_flag'], errors='coerce')
            if bf.isna().all():
                summary.append({**_row('failing_lanes', std, defect, '-', level, 0, 0, skipped),
                                'd2_rows': len(ref), 'status': 'NO DATA',
                                'note': f'{table}: breach_flag NULL (no D2 threshold)'})
                continue
            sil = (s.assign(_b=s['start_chainage'].astype('int64') // g * g, _bf=bf)
                   .groupby('_b')['_bf'].max().map({1.0: 'Yes', 0.0: 'No'}))
            j = ref.join(sil.rename('silver'), on='bin')
            ok = j['d2'] == j['silver']
            summary.append(_row('failing_lanes', std, defect, '-', level, len(j), int(ok.sum()), skipped,
                                note=f"{table}: Yes when any lane has breach_flag = 1 "
                                     f"(D2 Yes {int((j['d2'] == 'Yes').sum()):,}, silver Yes {int((j['silver'] == 'Yes').sum()):,})"))
            for _, q in j[~ok].iterrows():
                mism.append({'check': 'failing_lanes', 'std': std, 'defect': defect, 'lane': '-', 'level': level,
                             'bin_start': int(q['bin']), 'd2_value': q['d2'], 'silver_value': q['silver'],
                             'diff': None, 'reason': (f'no {table} row for this block' if pd.isna(q['silver'])
                                                      else 'Failing Lanes differs')})


def _value_and_breach_checks(prj, tables, summary, mism):
    lanes = sorted(prj.d4_long['lane'].unique())
    for std, d2 in [('CA', prj.d2_ca), ('IRC', prj.d2_irc)]:
        if d2.empty:
            continue
        c = list(d2.columns)
        st = parse_chainage(d2[pick(c, 'chainage', 'start')])
        for defect, (key, flag_kw, _) in VALUE_CHECKS.items():
            ric = pick(c, 'current', key, 'reporting interval')
            for lane in lanes:
                vc = next((x for x in c if norm_name(x).startswith('current') and key in norm_name(x)
                           and norm_name(x).endswith(f'| {lane.lower()}')), None)
                if not vc:
                    continue
                raw = d2[vc]
                val = num(raw)
                skipped = int((raw.notna() & val.isna()).sum())          # e.g. 'Under Const.'
                ref = pd.DataFrame({'ch': st, 'd2': val}).dropna()
                if ref.empty:
                    continue
                ri = num(d2[ric]).dropna() if ric else pd.Series([100])
                gi = _d2_interval(ri)           # D2's own interval (50, 100, 1000 …)
                g = 1000 if gi >= 1000 else 100
                crack100 = defect == 'cracking' and gi < 1000 and gi % 10 == 0
                if crack100:                    # cracking at D2's interval from the 10 m rows (any multiple of 10 m)
                    g = gi
                    _crack_checks(prj, tables, summary, mism, std, lane, ref, skipped, gi)
                level = '1 km' if g == 1000 else f'{g} m'
                need = 'master_cracking' if defect == 'cracking' else f"master_{defect}_{'parent' if g >= 1000 else 'child_100m'}"
                if not crack100 and gi not in (100, 1000):
                    # rutting / roughness are only built at 100 m and 1 km: a D2 block of another size has no
                    # silver row to compare with → NOT RUN (comparing it with a 100 m row would be a false FAIL)
                    summary.append({**_row('value', std, defect, lane, f'{gi} m', 0, 0, skipped),
                                    'd2_rows': len(ref), 'status': 'NOT RUN',
                                    'note': f'D2 interval {gi} m: silver has {defect} only at 100 m and 1 km'})
                    continue
                if crack100:                    # breach flags: 100 m table at 100 m, else the 10 m rows averaged
                    t100 = ('master_cracking_child_100m' if gi == 100 and 'master_cracking_child_100m' in tables
                            else None)
                    if not t100 and 'master_cracking' not in tables:
                        continue
                    sb = _silver_bins(prj, tables, defect, std, lane, g, t100)
                elif need not in tables:                       # table not built in this run → do not judge
                    summary.append({**_row('value', std, defect, lane, level, 0, 0, skipped),
                                    'd2_rows': len(ref), 'status': 'NOT RUN', 'note': f'{need} not in this run'})
                    continue
                ref['bin'] = (ref['ch'] // g * g).astype('int64')
                if not crack100:                # cracking values were checked in _crack_checks
                    sb = _silver_bins(prj, tables, defect, std, lane, g)
                    j = ref.join(sb, on='bin', how='left')
                    diff = (j['d2'] - num(j['silver'])).abs()
                    ok = diff <= _tolerance(defect, ref['d2'])
                    if defect == 'roughness':
                        ok = ok | _roughness_iri_ok(j['d2'], num(j['silver']))
                    summary.append(_row('value', std, defect, lane, level, len(j), int(ok.sum()), skipped))
                    bad = j[~ok]
                    for _, r in bad.iterrows():
                        mism.append({'check': 'value', 'std': std, 'defect': defect, 'lane': lane, 'level': level,
                                     'bin_start': int(r['bin']), 'd2_value': r['d2'],
                                     'silver_value': r['silver'], 'diff': None if pd.isna(r['silver']) else abs(r['d2'] - r['silver']),
                                     'reason': 'no silver row for this bin' if pd.isna(r['silver']) else 'value differs'})
                # CA breach flags
                fc = next((x for x in c if norm_name(x) == f'current cycle | {lane.lower()} {flag_kw}'), None)
                if std == 'CA' and fc:
                    # same D2 rows as the value check (rows with a value); flags on blank rows are not compared
                    fl = pd.DataFrame({'ch': st, 'flag': num(d2[fc]), 'v': val}).dropna()
                    if len(fl):
                        fl['bin'] = (fl['ch'] // g * g).astype('int64')
                        jf = fl.join(sb, on='bin', how='left')
                        okf = jf['flag'].astype(int) == num(jf['silver_breach'])
                        summary.append(_row('breach', std, defect, lane, level, len(jf), int(okf.sum()), 0))
                        for _, r in jf[~okf].iterrows():
                            mism.append({'check': 'breach', 'std': std, 'defect': defect, 'lane': lane, 'level': level,
                                         'bin_start': int(r['bin']), 'd2_value': r['flag'],
                                         'silver_value': r['silver_breach'], 'diff': None,
                                         'reason': 'no silver row for this bin' if pd.isna(r['silver_breach']) else 'breach flag differs'})

                # IRC breach per lane: the D2 IRC sheet has the lane VALUES but no per-lane 0/1 flags, so the D2
                # breach is derived: D2 IRC lane value vs the D2 IRC threshold with its operator
                if std == 'IRC':
                    t, op = prj.thr['irc'][defect], prj.thr_op('irc', defect)
                    unit = (prj.thr_unit('irc', defect) or '').lower()
                    if t is None:
                        summary.append({**_row('breach', std, defect, lane, level, 0, 0, 0), 'status': 'NO DATA',
                                        'note': 'no D2 IRC threshold → breach cannot be derived'})
                    elif defect == 'cracking' and 'sq' in unit:
                        summary.append({**_row('breach', std, defect, lane, level, 0, 0, 0), 'status': 'NOT RUN',
                                        'note': 'IRC cracking threshold in sq m; D2 lane values are %'})
                    else:
                        fl = pd.DataFrame({'ch': st, 'v': val}).dropna()
                        if len(fl):
                            fl['flag'] = breach(fl['v'], t, op).values
                            fl['bin'] = (fl['ch'] // g * g).astype('int64')
                            jf = fl.join(sb, on='bin', how='left')
                            okf = jf['flag'].astype(int) == num(jf['silver_breach'])
                            summary.append(_row('breach', std, defect, lane, level, len(jf), int(okf.sum()), 0,
                                                note=f'D2 IRC lane value {op} {t:g} (derived; D2 IRC has no flag columns)'))
                            for _, r in jf[~okf].iterrows():
                                mism.append({'check': 'breach', 'std': std, 'defect': defect, 'lane': lane,
                                             'level': level, 'bin_start': int(r['bin']), 'd2_value': r['flag'],
                                             'silver_value': r['silver_breach'], 'diff': None,
                                             'reason': ('no silver row for this bin' if pd.isna(r['silver_breach'])
                                                        else f"breach differs (D2 value {r['v']:g})")})


def _pothole_check(prj, tables, summary, mism):
    d2p = prj.read_first('d2', 'pothole') if prj.find_files('d2', 'pothole') else pd.DataFrame()
    if d2p.empty or 'master_pothole' not in tables:
        return
    pot = tables['master_pothole']
    c = list(d2p.columns)
    sev_c = pick(c, 'severity')
    d = pd.DataFrame({'lane': d2p[pick(c, 'lane')].astype(str).str.strip().str.upper(),
                      'ch': parse_chainage(d2p[pick(c, 'chainage')]), 'area': num(d2p[pick(c, 'area')]),
                      'sev': d2p[sev_c].astype(str).str.lower() if sev_c else ''})
    excl = d['sev'].apply(lambda v: any(x in v for x in config.DQ_POTHOLE_EXCLUDE_SEVERITY))
    d = d[~excl].dropna(subset=['ch', 'area'])
    # If silver severities are D2 severities (NHAI One: Moderate / High), compare only those D2 rows
    sil_sev = set(pot['severity'].dropna().astype(str).str.lower()) if 'severity' in pot else set()
    common = sil_sev & set(d['sev'])
    sev_note = ''
    if common:
        d = d[d['sev'].isin(common)]
        sev_note = f"; D2 rows with severity {'/'.join(sorted(common))} only"
    d['bin'] = (d['ch'] // 100 * 100).astype('int64')
    ref = d.groupby(['lane', 'bin'])['area'].sum()
    if pot.empty:
        sil = pd.Series(dtype=float)
    else:
        # same exclusion on the silver side (Delamination rows are kept in master_pothole with breach_flag 0)
        ps = pot['severity'].fillna('').astype(str).str.lower() if 'severity' in pot else pd.Series('', index=pot.index)
        pot = pot[~ps.apply(lambda v: any(x in v for x in config.DQ_POTHOLE_EXCLUDE_SEVERITY))]
        sil = (pot.assign(bin=pot['start_chainage'].astype('int64') // 100 * 100)
               .groupby(['lane', 'bin'])['area_sqm'].sum())
    src = prj.sources_used.get('pothole rows', '')
    for lane in sorted(ref.index.get_level_values(0).unique()):
        r = ref.loc[lane]
        s = sil.loc[lane] if lane in sil.index.get_level_values(0) else pd.Series(dtype=float)
        j = pd.DataFrame({'d2': r}).join(s.rename('silver'), how='left')
        diff = (j['d2'] - j['silver'].fillna(0)).abs()
        ok = diff <= config.DQ_AREA_TOLERANCE
        summary.append(_row('pothole_area', '-', 'pothole', lane, '100 m', len(j), int(ok.sum()), 0,
                            note=f"D2 {r.sum():.3f} sq m vs silver {s.sum():.3f} sq m; silver source: {src}{sev_note}"))
        for b, row in j[~ok].iterrows():
            mism.append({'check': 'pothole_area', 'std': '-', 'defect': 'pothole', 'lane': lane, 'level': '100 m',
                         'bin_start': int(b), 'd2_value': row['d2'], 'silver_value': row['silver'],
                         'diff': diff.loc[b], 'reason': 'no silver pothole in this 100 m' if pd.isna(row['silver'])
                         else 'area differs'})


def _threshold_check(prj, summary, mism):
    """One row per standard + defect: PASS when D2 gives the threshold, FAIL when it does not
       (there are no default thresholds, so that defect's threshold_value / breach_flag / road_condition are NULL)."""
    from common.project import DEFECTS
    for std in ['ca', 'irc']:
        for d in DEFECTS:
            v = prj.thr[std][d]
            found = v is not None
            note = (f"{prj.thr_op(std, d)} {v:g} from {prj.thr_src.get((std, d), 'D2')}" if found else
                    "threshold NOT in D2 → threshold_value, breach_flag, road_condition and calculated "
                    "severity / ratings are NULL for this defect + standard")
            summary.append({**_row('threshold', std.upper(), d, '-', '-', 1, int(found), 0, note=note)})
            if not found:
                mism.append({'check': 'threshold', 'std': std.upper(), 'defect': d, 'lane': '-', 'level': '-',
                             'bin_start': None, 'd2_value': None, 'silver_value': None, 'diff': None,
                             'reason': 'threshold not in D2 (no default used)'})


def run_dq(prj, tables=None):
    tables = _load_outputs(prj, tables)
    summary, mism = [], []
    _threshold_check(prj, summary, mism)
    for std, d2 in [('CA', prj.d2_ca), ('IRC', prj.d2_irc)]:      # a missing D2 sheet is shown, never replaced
        if d2.empty:
            summary.append({**_row('d2_sheet', std, '-', '-', '-', 0, 0, 0), 'status': 'NOT RUN',
                            'note': f"D2 'Comparison as per {std}' not found → {std} rows are not compared with D2 "
                                    f"(the other sheet is not used in its place)"})
    _value_and_breach_checks(prj, tables, summary, mism)
    _failing_lanes_checks(prj, tables, summary, mism)
    _pothole_check(prj, tables, summary, mism)
    res = pd.DataFrame(summary)
    if res.empty:
        print("\nDQ: no D2 data to compare against")
        return res
    res['upc'] = prj.upc
    out = os.path.join(prj.out_dir, 'dq')
    os.makedirs(out, exist_ok=True)
    res.to_csv(os.path.join(out, 'dq_summary.csv'), index=False)
    m = pd.DataFrame(mism, columns=['check', 'std', 'defect', 'lane', 'level', 'bin_start', 'd2_value',
                                    'silver_value', 'diff', 'reason'])
    m.insert(0, 'upc', prj.upc)
    m.to_csv(os.path.join(out, 'dq_mismatches.csv'), index=False)

    judged = res[res['status'].isin(['PASS', 'FAIL'])]
    n_pass = int((judged['status'] == 'PASS').sum())
    print(f"\nDQ vs D2 (pass ≥ {config.DQ_PASS_PCT:.0f}% of D2 rows): {n_pass}/{len(judged)} checks PASS"
          + (f" ({len(res) - len(judged)} not run)" if len(res) > len(judged) else ""))
    show = res[['check', 'std', 'defect', 'lane', 'level', 'd2_rows', 'matched', 'match_%', 'status']]
    print(show.to_string(index=False))
    print(f"  details: {os.path.join(out, 'dq_summary.csv')} + dq_mismatches.csv ({len(m):,} rows)")
    return res


if __name__ == '__main__':
    from common.output import parse_args, make_project
    run_dq(make_project(parse_args()))
