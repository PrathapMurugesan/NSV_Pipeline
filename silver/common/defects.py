"""
Where each defect's rows come from (one place per defect, same pattern for all):

  rutting / roughness / ravelling → D4 defect report 10 m columns          (linear_rows)
  cracking  → 1) D4 defect report 'Cracking Lane Area (%)'                 (cracking_rows)
              2) if empty: D4 standalone NSV cracks sheet 'Crack Area | Total' (%)
  potholes  → D4 NHAI One pothole sheet only (one row per pothole)      (pothole_rows; no fallback)
  cracking images → only the D4 crack image list (crack_image_points / map_crack_images):
                    chainage → lat / long (≤ CRACK_IMAGE_TOLERANCE_M) → OCR chainage → OCR lat / long
  Cracking source order: config.CRACKING_SOURCES.

Rutting / roughness / ravelling: every D4 lane-row is kept; a blank value stays blank (NULL).
Also: old condition-label helpers (the tables' labels come from defect_rules.json via classify()).
"""
import re
import numpy as np
import pandas as pd

import config
from common.utils import pick, num, clean_text, standardize_nh, parse_chainage


# ── Condition labels (same breakpoints as the old notebook). t = CA threshold from D2; None → no label ──
def _sel(conds, labels, default, index):
    return pd.Series(np.select(conds, labels, default), index=index).replace({'None': None})


def rutting_condition(v, t):
    if t is None or t != t:                  # no D2 threshold → no calculated label
        return pd.Series(None, index=v.index, dtype=object)
    fl = round(t * 0.5)
    return _sel([v.isna(), v < fl, v <= t], [None, f'Good (<{fl}mm)', f'Fair ({fl}-{t:.0f}mm)'],
                f'Poor (>{t:.0f}mm)', v.index)


def roughness_condition(bi, iri, t):
    if t is None or t != t:                  # no D2 threshold → no calculated label
        return pd.Series(None, index=bi.index, dtype=object)
    p = bi                                   # BI only (mm/km); blank BI → no label, never IRI × 1000
    fl = round(t * 0.8)
    return _sel([p.isna(), p < fl, p <= t], [None, f'Good (<{fl}mm/km)', f'Fair ({fl}-{t:.0f}mm/km)'],
                f'Poor (>{t:.0f}mm/km)', bi.index)


def ravelling_condition(v, t):
    if t is None or t != t:                  # no D2 threshold → no calculated label
        return pd.Series(None, index=v.index, dtype=object)
    h = round(t * 0.5)
    return _sel([v.isna(), v == 0, v <= h, v < t], [None, 'Very Good (0)', f'Good (1-{h}%)', f'Fair ({h}-{t:.0f}%)'],
                f'Poor (>={t:.0f}%)', v.index)


def crack_condition(v, t):
    if t is None or t != t:                  # no D2 threshold → no calculated label
        return pd.Series(None, index=v.index, dtype=object)
    b2, b3, b4 = round(t * 2), round(t * 4), round(t * 6)
    return _sel([v.isna(), v < t, v <= b2, v <= b3, v <= b4],
                [None, f'Very Good (<{t:.0f}%)', f'Good ({t:.0f}-{b2}%)', f'Fair ({b2}-{b3}%)', f'Poor ({b3}-{b4}%)'],
                f'Very Poor (>{b4}%)', v.index)


# ── Rutting / roughness / ravelling ────────────────────────────────────────────
METRIC_NAME = {'roughness': 'roughness_bi', 'rutting': 'rutting_avg', 'ravelling': 'ravelling_area'}
D4_FIELD = {'roughness': 'iri', 'rutting': 'rut', 'ravelling': 'rav_area'}


def linear_rows(prj, defect):
    """ALL D4 lane-rows → common 'normalised' frame for this defect.
    A row is never dropped because its value is blank: a missing value stays NaN (metric_value NULL,
    breach_flag 0, road_condition NULL) and the D4 Visual Condition of that row is still used."""
    d4 = prj.d4_long
    s = d4.copy()
    if s.empty:
        return pd.DataFrame()
    st, en = s['start_chainage'].astype(float), s['end_chainage'].astype(float)
    n = pd.DataFrame({
        'row_id': np.arange(1, len(s) + 1), 'lane': s['lane'].values,
        'nh_number': s['nh_number'].map(standardize_nh).values,
        'survey_date': prj.survey_ts,
        'start_chainage': np.minimum(st, en).astype('int64').values,
        'end_chainage': np.maximum(st, en).astype('int64').values,
        'start_lat': s['start_lat'].values, 'start_long': s['start_long'].values,
        'end_lat': s['end_lat'].values, 'end_long': s['end_long'].values,
        'roughness_bi': np.nan, 'iri_value': np.nan, 'rutting_avg': np.nan, 'ravelling_area': np.nan,
        'ravelling_area_sqm': np.nan,
        'lwp_iri': np.nan, 'rwp_iri': np.nan})
    n['length_m'] = n['end_chainage'] - n['start_chainage']
    thr = prj.thr['ca']
    if defect == 'roughness':
        n['iri_value'] = s['iri'].values
        # BI at 10 m: 630 × IRI^1.12; IRI blank → D4 'Roughness BI' as it is; both blank → NULL.
        # Never IRI (m/km) in place of BI (mm/km).
        n['roughness_bi'] = (630.0 * pd.Series(s['iri'].values) ** 1.12).fillna(pd.Series(s['bi'].values)).values
        n['metric_value'] = n['roughness_bi']
        n['severity'] = pd.Series(s['vc_rough_irc'].values).fillna(
            roughness_condition(n['roughness_bi'], n['iri_value'], thr['roughness'])).values
    elif defect == 'rutting':
        n['rutting_avg'] = n['metric_value'] = s['rut'].values
        n['severity'] = rutting_condition(n['metric_value'], thr['rutting']).values
    else:  # ravelling: area (sq m) → % of 35 sq m lane area
        n['ravelling_area_sqm'] = s['rav_area'].values                                   # original area, sq m
        n['ravelling_area'] = n['metric_value'] = s['rav_area'].values * 100.0 / config.LANE_AREA_SQM
        n['severity'] = ravelling_condition(n['metric_value'], thr['ravelling']).values
    return n




# ── Cracking ───────────────────────────────────────────────────────────────────
CRACK_COLS = ['lane', 'start', 'end', 'crack_pct', 'crack_sqm', 'start_lat', 'start_long', 'end_lat', 'end_long',
              'nh_number', 'image_name', 'datetime', 'source']


def _cracking_from_defect_report(prj):
    """Every D4 lane-row once D4 has any cracking value; a blank % stays blank (NULL), the row is kept.
       D4 with no cracking at all → empty, so the next source (NSV cracks sheet) is used."""
    d4 = prj.d4_long
    if d4.empty or not d4['crack_pct'].notna().any():
        return pd.DataFrame(columns=CRACK_COLS)
    s = d4.reset_index(drop=True)
    return pd.DataFrame({
        'lane': s['lane'],
        'start': np.minimum(s['start_chainage'], s['end_chainage']).astype('int64'),
        'end': np.maximum(s['start_chainage'], s['end_chainage']).astype('int64'),
        'crack_pct': s['crack_pct'], 'crack_sqm': s['crack_sqm'],
        'start_lat': s['start_lat'], 'start_long': s['start_long'], 'end_lat': s['end_lat'], 'end_long': s['end_long'],
        'nh_number': s['nh_number'], 'image_name': None, 'datetime': prj.run_ts, 'source': 'defect_report'})


def _cracking_from_nsv_cracks(prj):
    d = prj.nsv_cracks_src
    if d.empty:
        return pd.DataFrame(columns=CRACK_COLS)
    c = list(d.columns)
    st, en = parse_chainage(d[pick(c, 'start', 'chainage')]), parse_chainage(d[pick(c, 'end', 'chainage')])
    en = en.where(en.notna(), st + 10)        # blank end → start + 10 (else min/max would blank the start too)
    pct = num(d[pick(c, 'crack area')])
    lat0, lon0 = pick(c, 'start', 'lat'), pick(c, 'start', 'lon')
    lat1, lon1 = pick(c, 'end', 'lat'), pick(c, 'end', 'lon')
    img, dt, nh = pick(c, 'image'), pick(c, 'datetime'), pick(c, 'nh')
    out = pd.DataFrame({
        'lane': clean_text(d[pick(c, 'lane')]).str.upper(),
        'start': np.minimum(st, en), 'end': np.maximum(st, en),
        'crack_pct': pct,                                            # 'Crack Area | Total' is % of lane (max 100)
        'crack_sqm': pct * config.LANE_AREA_SQM / 100.0,             # % → sq m of the 35 sq m lane
        'start_lat': num(d[lat0]) if lat0 else np.nan, 'start_long': num(d[lon0]) if lon0 else np.nan,
        'end_lat': num(d[lat1]) if lat1 else np.nan, 'end_long': num(d[lon1]) if lon1 else np.nan,
        'nh_number': d[nh] if nh else None,
        'image_name': clean_text(d[img]) if img else None,
        'datetime': pd.to_datetime(d[dt], errors='coerce') if dt else prj.run_ts,
        'source': 'nsv_cracks'})
    out = out.dropna(subset=['start', 'crack_pct']).reset_index(drop=True)
    out[['start', 'end']] = out[['start', 'end']].round().astype('int64')
    return out.sort_values(['lane', 'start'], kind='mergesort').reset_index(drop=True)


def cracking_rows(prj):
    """First source in config.CRACKING_SOURCES that has data."""
    readers = {'defect_report': _cracking_from_defect_report, 'nsv_cracks': _cracking_from_nsv_cracks}
    for src in config.CRACKING_SOURCES:
        rows = readers[src](prj)
        if len(rows):
            prj.sources_used['cracking rows'] = f"{src} ({len(rows):,} rows)"
            return rows
    prj.sources_used['cracking rows'] = 'none (no cracking data in any source)'
    return pd.DataFrame(columns=CRACK_COLS)


_NUM = r'[-+]?\d+(?:\.\d+)?'


def _ocr_clean_number(v):
    """OCR chainage: used only when the whole cell is a plain number ('54671'); '3:', '34:' … → NaN."""
    t = str(v).strip() if v is not None and v == v else ''
    return float(t) if re.fullmatch(r'\d+(?:\.\d+)?', t) else np.nan


def _ocr_coord(v, after=None, lo=-180.0, hi=180.0):
    """OCR latitude / longitude from text like '77.00685649 Lat: 28.57801152 Alt: 216.8'.
       after=None → the first number; after='Lat' → the number after 'Lat:'. Needs ≥ 5 decimals (≈ 1 m),
       so truncated OCR such as '28.57' or '28' is not used."""
    if v is None or v != v:
        return np.nan
    t = str(v)
    m = re.search((rf'{after}\s*:?\s*' if after else r'^\s*') + rf'({_NUM})', t, re.I)
    if not m:
        return np.nan
    x = m.group(1)
    if '.' not in x or len(x.split('.')[1]) < 5:
        return np.nan
    x = float(x)
    return x if lo <= x <= hi else np.nan


def crack_image_points(prj):
    """D4 crack image list → one row per image: lane, chainage / latitude / longitude (columns H, I, J),
       OCR chainage / latitude / longitude (columns O, P, Q, cleaned), file name, image path."""
    d = prj.crack_images_src
    cols = ['lane', 'ch', 'lat', 'lon', 'ocr_ch', 'ocr_lat', 'ocr_lon', 'name', 'path']
    if d.empty:
        return pd.DataFrame(columns=cols)
    c = list(d.columns)
    name_c = pick(c, 'file_name') or pick(c, 'file', 'name')
    if not name_c:
        return pd.DataFrame(columns=cols)
    path_c = pick(c, 'image_path') or pick(c, 'path')
    name = clean_text(d[name_c])
    lane = clean_text(d[pick(c, 'lane')]).str.upper() if pick(c, 'lane') else pd.Series(None, index=d.index, dtype=object)
    from_name = name.str.extract(r'(?:^|_)([LR][1-8])(?:_|\.|$)', expand=False).str.upper()
    lane = lane.where(lane.notna(), from_name)                       # lane column blank → lane from the file name

    def col(*words, exclude=()):
        k = pick(c, *words, exclude=exclude)
        return d[k] if k else pd.Series(np.nan, index=d.index)
    skip = ('start', 'end', 'label', 'exif')
    ocr_lat_c, ocr_lon_c = col('ocr_latitude'), col('ocr_longitude')
    ocr_lat = ocr_lat_c.map(lambda v: _ocr_coord(v, lo=-90, hi=90))
    ocr_lat = ocr_lat.where(ocr_lat.notna(), ocr_lon_c.map(lambda v: _ocr_coord(v, after='Lat', lo=-90, hi=90)))
    return pd.DataFrame({
        'lane': lane,
        'ch': num(col('chainage', exclude=skip + ('ocr',))),
        'lat': num(col('latitude', exclude=skip + ('ocr',))),
        'lon': num(col('longitude', exclude=skip + ('ocr',))),
        'ocr_ch': col('ocr_chainage').map(_ocr_clean_number),
        'ocr_lat': ocr_lat,
        'ocr_lon': ocr_lon_c.map(_ocr_coord),
        'name': name, 'path': clean_text(d[path_c]) if path_c else name,
    }).dropna(subset=['name']).sort_values('name', kind='mergesort').reset_index(drop=True)


def _to_xy(lat, lon, lat0, lon0):
    """lat / lon → metres on a local flat grid (accurate over a project's extent)."""
    return ((np.asarray(lon, float) - lon0) * 111320.0 * np.cos(np.radians(lat0)),
            (np.asarray(lat, float) - lat0) * 110540.0)


def map_crack_images(prj, rows):
    """rows: 10 m cracking rows (lane, start, end, start_lat, start_long, end_lat, end_long).
       Returns (names, paths, stats): per row a list of image file names / image paths.
       Every image goes to ONE row of its lane, first step that finds a row wins:
         1) chainage (col H) inside the row's range [start, end)
         2) latitude / longitude (cols I, J) within config.CRACK_IMAGE_TOLERANCE_M of the row's segment (nearest row)
         3) ocr_chainage (col O) inside the row's range
         4) ocr_latitude / ocr_longitude (cols P, Q) within the tolerance
       Images that none of the steps can place are only counted in the log (no check fails on them)."""
    n = len(rows)
    names, paths = [[] for _ in range(n)], [[] for _ in range(n)]
    img = crack_image_points(prj)
    stats = {'images': len(img), 'by_chainage': 0, 'by_latlon': 0, 'by_ocr_chainage': 0, 'by_ocr_latlon': 0,
             'not_mapped': 0}
    if img.empty or n == 0:
        stats['not_mapped'] = len(img)
        return names, paths, stats
    lane = rows['lane'].astype(str).str.upper().values
    st = np.fmin(rows['start'].astype(float).values, rows['end'].astype(float).values)
    en = np.fmax(rows['start'].astype(float).values, rows['end'].astype(float).values)
    slat, slon = num(rows['start_lat']).values, num(rows['start_long']).values
    elat = np.where(np.isnan(num(rows['end_lat']).values), slat, num(rows['end_lat']).values)
    elon = np.where(np.isnan(num(rows['end_long']).values), slon, num(rows['end_long']).values)
    ok = ~np.isnan(slat) & ~np.isnan(slon)
    lat0 = float(np.nanmean(slat)) if ok.any() else 0.0
    lon0 = float(np.nanmean(slon)) if ok.any() else 0.0
    ax, ay = _to_xy(slat, slon, lat0, lon0)
    bx, by = _to_xy(elat, elon, lat0, lon0)
    tol = float(config.CRACK_IMAGE_TOLERANCE_M)
    cell = 25.0                                                       # grid for the lat/long search (m)
    by_lane = {}
    for l in np.unique(lane):
        idx = np.where(lane == l)[0]
        o = idx[np.argsort(st[idx], kind='mergesort')]
        grid = {}
        for i in idx[ok[idx]]:
            k = (int(((ax[i] + bx[i]) / 2) // cell), int(((ay[i] + by[i]) / 2) // cell))
            grid.setdefault(k, []).append(i)
        by_lane[l] = (o, st[o], grid)

    def by_chainage(l, ch):
        if not (isinstance(l, str) and l in by_lane) or ch != ch:
            return None
        o, sts, _ = by_lane[l]
        p = np.searchsorted(sts, ch, side='right') - 1
        return o[p] if p >= 0 and ch < en[o[p]] else None

    def by_latlon(l, la, lo):
        if la != la or lo != lo:
            return None
        x, y = _to_xy(la, lo, lat0, lon0)
        x, y = float(x), float(y)
        gx, gy = int(x // cell), int(y // cell)
        best, best_d = None, tol
        for ln in ([l] if isinstance(l, str) and l in by_lane else list(by_lane)):   # unknown lane → any lane
            grid = by_lane[ln][2]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for i in grid.get((gx + dx, gy + dy), ()):
                        vx, vy = bx[i] - ax[i], by[i] - ay[i]
                        L2 = vx * vx + vy * vy
                        t = 0.0 if L2 == 0 else min(1.0, max(0.0, ((x - ax[i]) * vx + (y - ay[i]) * vy) / L2))
                        dd = float(np.hypot(x - (ax[i] + t * vx), y - (ay[i] + t * vy)))
                        if dd <= best_d:
                            best, best_d = i, dd
        return best

    for r in img.itertuples(index=False):
        for step, find in (('by_chainage', lambda: by_chainage(r.lane, r.ch)),
                           ('by_latlon', lambda: by_latlon(r.lane, r.lat, r.lon)),
                           ('by_ocr_chainage', lambda: by_chainage(r.lane, r.ocr_ch)),
                           ('by_ocr_latlon', lambda: by_latlon(r.lane, r.ocr_lat, r.ocr_lon))):
            hit = find()
            if hit is not None:
                stats[step] += 1
                names[hit].append(r.name)
                paths[hit].append(r.path)
                break
        else:
            stats['not_mapped'] += 1
    return names, paths, stats


# ── Potholes ───────────────────────────────────────────────────────────────────
POTHOLE_COLS = ['start', 'end', 'lane', 'lat', 'lon', 'end_lat', 'end_lon', 'area', 'max_depth', 'severity',
                'image', 'side', 'type', 'datetime', 'source']


def _potholes_from_nhai_one(prj):
    d = prj.potholes_src
    if d.empty:
        return pd.DataFrame(columns=POTHOLE_COLS)
    d = d.reset_index(drop=True)
    c = list(d.columns)
    fkm, fm = pick(c, 'from', 'km'), pick(c, 'from', '_m') or pick(c, 'from', 'meter')
    tkm, tm = pick(c, 'to_', 'km') or pick(c, 'to ', 'km'), pick(c, 'to_', '_m') or pick(c, 'to', 'meter')
    chc = pick(c, 'chainage', exclude=('from', 'to'))

    # start = FROM_CH_KM × 1000 + FROM_CH_M  (Chainage if metres look wrong)
    if fkm and fm:
        km, m = num(d[fkm]).fillna(0).astype(int), num(d[fm]).fillna(0).astype(int)
        start = km * 1000 + m
        bad = m > 999
        if bad.any() and chc:
            start[bad] = num(d.loc[bad, chc]).fillna(0).astype(int)
    else:
        start = num(d[chc]).fillna(0)
        start = start.where(start >= 1000, start * 1000)
    # end = TO_CH_KM × 1000 + TO_CH_M, or = start when blank
    if tkm and tm and (num(d[tkm]).notna() | num(d[tm]).notna()).any():
        end = num(d[tkm]).fillna(0).astype(int) * 1000 + num(d[tm]).fillna(0).astype(int)
        end = end.where(num(d[tkm]).notna() | num(d[tm]).notna(), start)
    else:
        end = start.copy()

    lane_c = pick(c, 'lane')
    area_c = pick(c, 'area', exclude=('asset',))
    typ_c = pick(c, 'type', 'defect')
    return pd.DataFrame({
        'start': start.astype('int64'), 'end': end.astype('int64'),
        'lane': clean_text(d[lane_c]).fillna('Unknown').str.upper() if lane_c else 'Unknown',
        'lat': num(d[pick(c, 'latitude')]), 'lon': num(d[pick(c, 'longitude')]),
        'area': num(d[area_c]) if area_c else np.nan,
        'max_depth': num(d[pick(c, 'max', 'depth')]) if pick(c, 'max', 'depth') else np.nan,
        'severity': clean_text(d[pick(c, 'severity')]) if pick(c, 'severity') else None,
        'image': clean_text(d[pick(c, 'image')]) if pick(c, 'image') else None,
        'side': clean_text(d[pick(c, 'side')]) if pick(c, 'side') else None,
        'type': clean_text(d[typ_c]).fillna('Pothole') if typ_c else 'Pothole',
        'datetime': pd.to_datetime(d[pick(c, 'datetime')], errors='coerce') if pick(c, 'datetime') else pd.NaT,
    }).assign(end_lat=lambda x: x['lat'], end_lon=lambda x: x['lon'], source='nhai_one')


def pothole_rows(prj):
    """Potholes from the D4 NHAI One sheet only (no defect-report fallback)."""
    rows = _potholes_from_nhai_one(prj)
    if len(rows):
        prj.sources_used['pothole rows'] = f"nhai_one ({len(rows):,} rows)"
        return rows.reset_index(drop=True)
    prj.sources_used['pothole rows'] = 'none (no D4 NHAI One pothole sheet / rows)'
    return pd.DataFrame(columns=POTHOLE_COLS)


# ── Good / Fair / Poor from configurable ranges (defect_rules.json) ────────────
def _fmt(x):
    if x is None:
        return ''
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:.2f}".rstrip('0').rstrip('.')


def band_limits(ranges, threshold):
    """Upper limit of every band (None for the last / open band). None if a ratio band has no threshold."""
    out = []
    for b in ranges:
        if b.get('to') is not None:
            lim = float(b['to'])
        elif b.get('to_ratio') is not None:
            if threshold is None or threshold != threshold:
                return None
            lim = float(b['to_ratio']) * float(threshold)
            if b.get('round'):
                lim = float(round(lim))                      # old notebook: round(t * 0.5) / round(t * 0.8)
        else:
            lim = None
        out.append(lim)
    return out


def classify(values, ranges, threshold):
    """Label each value with a range list from defect_rules.json. First matching band wins:
    value < limit (or <= when to_inclusive). The written label is the band 'text' ({from} / {to} filled in),
    e.g. 'Good (<10mm)'. NaN stays None (never treated as 0). Independent of breach_flag."""
    v = num(pd.Series(values))
    out = pd.Series(None, index=v.index, dtype=object)
    if not ranges:
        return out
    limits = band_limits(ranges, threshold)
    if limits is None:
        return out
    left = v.notna()
    prev = None
    for b, lim in zip(ranges, limits):
        m = left if lim is None else left & ((v <= lim) if b.get('to_inclusive') else (v < lim))
        text = b.get('text', b['label']).replace('{from}', _fmt(prev)).replace('{to}', _fmt(lim))
        out[m] = text
        left &= ~m
        prev = lim if lim is not None else prev
    return out


def ranges_for(prj, kind, defect, std):
    """kind = 'severity_ranges' or 'rating_ranges' (project overrides applied)."""
    return (prj.rules.get(kind, {}).get(defect) or {}).get(std)
