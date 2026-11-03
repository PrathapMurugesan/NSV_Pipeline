"""
Everything about ONE project (UPC): where its files are, reading D1–D4, the tracker,
the D2 threshold JSON, thresholds, survey date and reporting interval.
Loaded once and shared by all tables.

Sections
  1. D4 defect report reader     (wide sheet, one column per lane → one row per lane per 10 m)
  2. Tracker reader              (NSV Activity Tracker row of this UPC)
  3. D2 threshold JSON reader    (bronze meta: Inputs (Thresholds) + data dictionary)
  4. Threshold resolution        (D2 json → D2 data dictionary → D2 limit column → defect_rules.json)
  5. Project class               (file discovery by name, then by content; cached data)
"""
import copy
import json
import os
import re
from datetime import datetime
from functools import cached_property

import numpy as np
import pandas as pd

import config
from common.utils import pick, num, parse_chainage, clean_text, norm_name


# ═════════════════════════════════════════════════════════════════════════════
# 1. D4 defect report reader
# ═════════════════════════════════════════════════════════════════════════════
_LANE_AT_END = re.compile(r'(?:^|[^a-z0-9])([lr][1-8])\s*$')
_LANE_AT_START = re.compile(r'^([lr][1-8])(?:[^a-z0-9]|$)')

# Checked in order; first match wins (visual-condition rules come first)
METRIC_RULES = [
    ('vc_rough_ca',  lambda n: 'visual' in n and 'rough' in n and '(ca)' in n),
    ('vc_rough_irc', lambda n: 'visual' in n and 'rough' in n and '(irc)' in n),
    ('vc_rut_ca',    lambda n: 'visual' in n and 'rut' in n and '(ca)' in n),
    ('vc_rut_irc',   lambda n: 'visual' in n and 'rut' in n and '(irc)' in n),
    ('vc_pot_ca',    lambda n: 'visual' in n and 'pothole' in n and '(ca)' in n),
    ('vc_pot_irc',   lambda n: 'visual' in n and 'pothole' in n and '(irc)' in n),
    ('vc_crack_ca',  lambda n: 'visual' in n and 'crack' in n and '(ca)' in n),
    ('vc_crack_irc', lambda n: 'visual' in n and 'crack' in n and '(irc)' in n),
    ('vc_rav_ca',    lambda n: 'visual' in n and 'ravel' in n and '(ca)' in n),
    ('vc_rav_irc',   lambda n: 'visual' in n and 'ravel' in n and '(irc)' in n),
    # D4 bronze names (one column per lane, text values → numbers via num()):
    #   'Roughness BI @ 10 mtr | L1' … '| R4'  → bi   (used only when IRI is blank)
    #   'Roughness IRI @ 10 mtr | L1' … '| R4' → iri  (BI = 630 × IRI^1.12)
    ('bi',           lambda n: 'roughness bi' in n),
    ('iri',          lambda n: 'roughness iri' in n),
    ('rut',          lambda n: 'rut depth' in n),
    ('crack_pct',    lambda n: 'crack' in n and '%' in n),
    ('crack_sqm',    lambda n: 'crack' in n and 'sq' in n),
    ('rav_area',     lambda n: 'ravel' in n and 'visual' not in n),      # never a Visual Condition column
    ('patch_area',   lambda n: 'patch' in n and 'visual' not in n),
    ('pothole_area', lambda n: 'pothole' in n and 'area' in n),
]
TEXT_METRICS = {k for k, _ in METRIC_RULES if k.startswith('vc_')}
GEO_RULES = [('start_lat',  lambda n: 'start' in n and 'lat' in n),
             ('start_long', lambda n: 'start' in n and 'lon' in n),
             ('end_lat',    lambda n: 'end' in n and 'lat' in n),
             ('end_long',   lambda n: 'end' in n and 'lon' in n)]


def vc_label(s):
    """D4 Visual Condition text; a whole number 1–5 becomes its label (config.D4_VC_NUMBER_LABELS).
       Returns (labels, number of cells converted)."""
    t = clean_text(s)
    n = 0

    def one(v):
        nonlocal n
        if v is None:
            return None
        try:
            k = float(v)
        except (TypeError, ValueError):
            return v
        if np.isfinite(k) and k == int(k) and int(k) in config.D4_VC_NUMBER_LABELS:
            n += 1
            return config.D4_VC_NUMBER_LABELS[int(k)]
        return v
    return t.map(one), n


def d4_to_long(raw):
    cols = list(raw.columns)
    start_c = pick(cols, 'chainage', 'start')
    end_c = pick(cols, 'chainage', 'end')
    nh_c = pick(cols, 'nh', 'number')

    lane_cols = {}
    for c in cols:
        n = norm_name(c)
        m = _LANE_AT_START.match(n)
        if m:
            f = next((f for f, rule in GEO_RULES if rule(n)), None)
            if f:
                lane_cols.setdefault(m.group(1).upper(), {})[f] = c
            continue
        m = _LANE_AT_END.search(n)
        if m:
            f = next((f for f, rule in METRIC_RULES if rule(n)), None)
            if f:
                lane_cols.setdefault(m.group(1).upper(), {})[f] = c

    start = parse_chainage(raw[start_c])
    end = parse_chainage(raw[end_c]) if end_c else start + 10
    no_end = end.isna() & start.notna()
    if no_end.any():                         # blank / unreadable end chainage → start + 10 (D4 is 10 m), never a crash
        print(f"  WARNING: D4 end chainage blank on {int(no_end.sum()):,} row(s) → start + 10 used")
        end = end.where(~no_end, start + 10)
    frames = []
    n_vc_num = 0
    for lane, fmap in sorted(lane_cols.items()):
        values = [f for f in fmap if f not in dict(GEO_RULES)]
        if not any(raw[fmap[f]].notna().any() for f in values):
            continue
        filled = pd.concat([raw[fmap[f]].notna() for f in values], axis=1).any(axis=1)
        share = float(filled.mean()) if len(filled) else 0.0
        if share < config.MIN_LANE_FILL:          # a few stray cells, not a surveyed lane
            print(f"  WARNING: D4 lane {lane} skipped — only {int(filled.sum()):,} of {len(filled):,} rows have a value "
                  f"({share:.2%} < {config.MIN_LANE_FILL:.0%})")
            continue
        part = {'lane': lane, 'start_chainage': start, 'end_chainage': end,
                'nh_number': raw[nh_c] if nh_c else None}
        for f, _ in GEO_RULES:
            part[f] = num(raw[fmap[f]]) if f in fmap else np.nan
        for f, _ in METRIC_RULES:
            if f in fmap:
                if f in TEXT_METRICS:
                    part[f], k = vc_label(raw[fmap[f]])
                    n_vc_num += k
                else:
                    part[f] = num(raw[fmap[f]])
            else:
                part[f] = None if f in TEXT_METRICS else np.nan
        frames.append(pd.DataFrame(part))
    if n_vc_num:
        print(f"  D4 Visual Condition given as numbers on {n_vc_num:,} cells → labels "
              f"({', '.join(f'{k} = {v}' for k, v in sorted(config.D4_VC_NUMBER_LABELS.items(), reverse=True))})")
    long = pd.concat(frames, ignore_index=True).dropna(subset=['start_chainage'])
    return long.sort_values(['lane', 'start_chainage'], kind='mergesort').reset_index(drop=True)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Tracker reader
# ═════════════════════════════════════════════════════════════════════════════
def _parse_std(val):
    s = str(val).strip().upper() if pd.notna(val) else ""
    return 'IRC' if 'IRC' in s else ('CA' if 'CA' in s else None)


# Tracker columns are found by their HEADER NAME (row 2 of the 'Zone <zone>' sheet), never by position.
TRACKER_COLS = {
    'upc': 'UPC code as per O&M master', 'upc_survey': 'UPC as per survey conducted',
    'phase': 'Phase', 'cycle': 'Cycle', 'state': 'State', 'piu': 'PIU', 'contractor': 'Contractor/ Concessionaire',
    'ae_ie': 'AE/ IE', 'total_length_km': 'Total Length (km)', 'number_of_lanes': 'Number of lanes',
    'lane_length_km': 'Lane Length (km)', 'survey_start': 'Actual Survey Start Date',
    'survey_end': 'Actual Survey End Date', 'pavement_type': 'Type of pavement', 'pci': 'PCI',
}
# defect → its tracker interval column; the standard (CA / IRC) is the 'Reporting as per CA/IRC' column just before
TRACKER_DEFECT_COLS = {'ravelling': 'Ravelling', 'rutting': 'Rutting', 'cracking': 'Cracks', 'roughness': 'Roughness'}


def _upc_key(v):
    return re.sub(r'[^0-9a-z]', '', str(v).lower())


def load_tracker_context(prj):
    """Project row of the NSV Activity Tracker (sheet 'Zone <zone>', header row 2), columns found by name.
       Row: UPC equal to 'UPC code as per O&M master' (else 'UPC as per survey conducted'; a cell may hold several
       UPCs on separate lines), same Phase + Cycle preferred. Reporting intervals are NOT taken from the tracker
       (they come from D2, see Project.d2_interval)."""
    defs = {d: {'standard': 'CA'} for d in ['ravelling', 'rutting', 'cracking', 'roughness', 'pothole']}
    ctx = {'survey_start_date': None, 'survey_end_date': None, 'piu': None, 'ae_ie': None,
           'contractor': None, 'state': None, 'pavement_type': None, 'pci': None,
           'total_length_km': None, 'number_of_lanes': None, 'lane_length_km': None,
           'defect_standards': defs, 'found': False}
    try:
        df = pd.read_excel(prj.tracker_path, sheet_name=f"Zone {prj.zone}", header=1)
    except Exception as e:
        print(f"  WARNING: tracker not loaded ({e}) — standard CA and survey dates from the data")
        return ctx
    cols = {str(c).strip().lower(): c for c in df.columns}

    def col(name):
        return cols.get(name.strip().lower())

    want = _upc_key(prj.upc)
    best = None
    for key in ['upc', 'upc_survey']:
        c = col(TRACKER_COLS[key])
        if c is None:
            continue
        for _, row in df.iterrows():
            cell = row[c]
            if pd.isna(cell) or want not in {_upc_key(x) for x in re.split(r'[\n,;]+', str(cell)) if x.strip()}:
                continue
            ph = str(row[col('Phase')]).lower().replace('phase', '').strip() if col('Phase') else ''
            cy = str(row[col('Cycle')]).lower().replace('cycle', '').strip() if col('Cycle') else ''
            if ph == str(prj.phase) and cy == str(prj.cycle):
                best = row
                break
            if best is None:
                best = row
        if best is not None:
            break
    if best is None:
        print(f"  WARNING: UPC {prj.upc} not in tracker — standard CA for every defect, survey dates from the data")
        return ctx

    def val(name):
        c = col(TRACKER_COLS[name])
        return None if c is None or pd.isna(best[c]) else best[c]

    def txt(name):
        v = val(name)
        return None if v is None else str(v).strip()

    names = list(df.columns)
    ds = {}
    for d, header in TRACKER_DEFECT_COLS.items():
        c = col(header)
        i = names.index(c) if c is not None else None
        std_col = names[i - 1] if i and str(names[i - 1]).strip().lower().startswith('reporting as per') else None
        ds[d] = {'standard': (_parse_std(best[std_col]) if std_col is not None else None) or 'CA'}
    ds['pothole'] = dict(ds['cracking'])
    s0, s1 = pd.to_datetime(val('survey_start'), errors='coerce'), pd.to_datetime(val('survey_end'), errors='coerce')
    n = lambda k: (float(num(pd.Series([val(k)])).iloc[0]) if pd.notna(num(pd.Series([val(k)])).iloc[0]) else None)
    ctx.update({
        'survey_start_date': s0.date() if pd.notnull(s0) else None,
        'survey_end_date': s1.date() if pd.notnull(s1) else None,
        'piu': txt('piu'), 'contractor': txt('contractor'), 'ae_ie': txt('ae_ie'), 'state': txt('state'),
        'pavement_type': txt('pavement_type'), 'pci': n('pci'),
        'total_length_km': n('total_length_km'), 'lane_length_km': n('lane_length_km'),
        'number_of_lanes': int(n('number_of_lanes')) if n('number_of_lanes') is not None else None,
        'defect_standards': ds, 'found': True,
    })
    print(f"  tracker: found {prj.upc} ({txt('phase')}, {txt('cycle')}) | standards "
          + ", ".join(f"{d} {v['standard']}" for d, v in ds.items() if d != 'pothole'))
    return ctx


# ═════════════════════════════════════════════════════════════════════════════
# 3. D2 threshold JSON reader (bronze meta)
# ═════════════════════════════════════════════════════════════════════════════
DEFECT_WORDS = [('rough', 'roughness'), ('rut', 'rutting'), ('crack', 'cracking'), ('ravel', 'ravelling')]
_OPERATORS = {'>', '>=', '≥', '<', '<=', '≤'}


def _defect(text):
    t = str(text).strip().lower()
    return next((d for k, d in DEFECT_WORDS if k in t), None)


def _num(v):
    try:
        return float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _split_cell(ref):
    m = re.match(r'^([A-Z]+)(\d+)$', str(ref).strip().upper())
    if not m:
        return None, None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def find_meta(prj, *must):
    """Newest JSON in d2/<run>/meta whose name contains all words."""
    d = prj.d_dirs.get('d2')
    if not d:
        return None
    meta = os.path.join(d, 'meta')
    if not os.path.isdir(meta):
        return None
    hits = sorted(f for f in os.listdir(meta) if f.endswith('.json') and all(m in f.lower() for m in must))
    return os.path.join(meta, hits[-1]) if hits else None


def read_inputs_thresholds(prj):
    path = find_meta(prj, 'threshold', 'cells')
    if not path:
        return {}, None
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    grid = {}
    for c in data.get('cells', []):
        r, col = _split_cell(c.get('cell'))
        if r is not None and c.get('value') not in (None, ''):
            grid[(r, col)] = c['value']
    rows = sorted({r for r, _ in grid})

    out = {}
    for r in rows:
        label = grid.get((r, 1))                     # column A = defect name
        defect = _defect(label) if label is not None else None
        if not defect:
            continue
        # header row of this block = nearest row above that has a 'Unit' cell
        hdr = max((h for h in rows if h < r and any(str(v).strip().lower() == 'unit'
                                                     for (hr, _), v in grid.items() if hr == h)), default=None)
        if hdr is None:
            continue
        hcells = sorted((c, str(v).strip()) for (hr, c), v in grid.items() if hr == hdr)
        unit_col = next(c for c, v in hcells if v.lower() == 'unit')           # first = current cycle
        val_col = max((c for c, v in hcells if c < unit_col), default=unit_col - 1)
        int_col = next((c for c, v in hcells if c > unit_col and 'interval' in v.lower()), None)
        std_txt = grid.get((hdr, val_col), '')
        std = 'irc' if 'irc' in str(std_txt).lower() else 'ca'
        op = next((str(grid[(r, c)]).strip() for c in range(2, val_col)
                   if (r, c) in grid and str(grid[(r, c)]).strip() in _OPERATORS), None)
        value = _num(grid.get((r, val_col)))
        if value is None:
            continue
        out[(std, defect)] = {'value': value,
                              'operator': {'≥': '>=', '≤': '<='}.get(op, op),
                              'unit': grid.get((r, unit_col)),
                              'interval_m': _num(grid.get((r, int_col))) if int_col else None}
    return out, path


def read_header_thresholds(prj):
    path = find_meta(prj, 'data_dictionary')
    if not path:
        return {}, None
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for sheet_key, sheet in data.items():
        std = 'irc' if 'irc' in sheet_key.lower() else 'ca' if 'ca' in sheet_key.lower() else None
        if not std:
            continue
        for col, info in (sheet.get('columns') or {}).items():
            if not str(col).lower().startswith('current cycle'):
                continue
            defect = _defect(col.split('|')[1] if '|' in col else col)
            v = next((_num(x) for x in (info or {}).values() if _num(x) is not None), None)
            if defect and v is not None and (std, defect) not in out:
                out[(std, defect)] = {'value': v}
    return out, path


# ═════════════════════════════════════════════════════════════════════════════
# 4. Threshold resolution
#    1) D2 Inputs (Thresholds) JSON  2) D2 data dictionary JSON  3) D2 limit column
#    (roughness from D2 given as IRI m/km → converted to BI mm/km)
#    No defaults: not in D2 → None (threshold_value / breach_flag / road_condition NULL; DQ flags it).
#    Operator from 1) ('>' = strictly greater), else '>='.
# ═════════════════════════════════════════════════════════════════════════════
DEFECTS = list(config.THRESHOLD_DEFECTS)


def breach(values, threshold, operator='>='):
    """1 when the value crosses the threshold, else 0 (blank value → 0).
       No threshold (not in D2) → NaN on every row: a breach cannot be judged."""
    v = num(values)
    if threshold is None or threshold != threshold:
        return pd.Series(np.nan, index=v.index)
    hit = v > threshold if operator == '>' else v >= threshold
    return hit.fillna(False).astype(int)


def resolve_thresholds(prj):
    thr = {'ca': {d: None for d in DEFECTS}, 'irc': {d: None for d in DEFECTS}}
    src, ops, units, intervals = {}, {}, {}, {}

    def put(std, d, value, source):
        if d in thr[std] and thr[std][d] is None and value is not None:
            thr[std][d] = float(value)
            src[(std, d)] = source

    # 1) Inputs (Thresholds) JSON
    inp, path = read_inputs_thresholds(prj)
    for (std, d), info in inp.items():
        put(std, d, info['value'], 'D2 Inputs (Thresholds) json')
        if info.get('operator'):
            ops[(std, d)] = info['operator']
        if info.get('unit'):
            units[(std, d)] = str(info['unit']).strip()
        if info.get('interval_m'):
            intervals[(std, d)] = int(info['interval_m'])

    # 2) data dictionary JSON (header row 2)
    hdr, _ = read_header_thresholds(prj)
    for (std, d), info in hdr.items():
        put(std, d, info['value'], 'D2 header row json')

    # 3) D2 limit columns
    for df, std in [(prj.d2_ca, 'ca'), (prj.d2_irc, 'irc')]:
        if df.empty:
            continue
        for c in df.columns:
            n = norm_name(c)
            if 'limit' not in n or '>' in n:
                continue
            v = num(df[c]).dropna()
            if v.empty:
                continue
            d = ('rutting' if 'rut' in n else 'cracking' if 'crack' in n else 'ravelling' if 'ravel' in n
                 else 'roughness' if ('rough' in n or 'bi' in n or 'iri' in n) else None)
            if d:
                put(std, d, v.iloc[0], 'D2 limit column')

    # 3b) roughness must be BI mm/km (metric_value is BI). A D2 roughness value given as IRI — unit m/km,
    #     or a value below 100 (BI limits are in the thousands, IRI limits are ~2–5) — is converted with the
    #     same formula: BI = 630 × IRI^1.12. The D2 value is still the source; only the unit changes.
    for std in ['ca', 'irc']:
        v = thr[std].get('roughness')
        if v is None:
            continue
        u = str(units.get((std, 'roughness')) or '').lower().replace(' ', '')
        is_iri = 'iri' in u or ('m/km' in u and 'mm/km' not in u) or v < 100
        if is_iri:
            bi = round(630.0 * v ** 1.12, 2)
            print(f"  WARNING: {std.upper()} roughness threshold from D2 = {v:g} looks like IRI (m/km) "
                  f"→ converted to BI {bi:g} mm/km")
            thr[std]['roughness'] = bi
            src[(std, 'roughness')] = f"{src.get((std, 'roughness'), 'D2')} (IRI {v:g} m/km → BI)"
            units[(std, 'roughness')] = 'mm/km'

    # 4) NO defaults: a defect + standard with no D2 threshold stays None (DQ 'threshold' check flags it)
    for std in ['ca', 'irc']:
        for d in DEFECTS:
            if thr[std][d] is None:
                src[(std, d)] = 'NOT IN D2'

    return {'thr': thr, 'src': src, 'ops': ops, 'units': units, 'intervals': intervals, 'json': path}


# ═════════════════════════════════════════════════════════════════════════════
# 5. Project
# ═════════════════════════════════════════════════════════════════════════════
_EXT_RANK = {'.parquet': 0, '.csv': 1, '.xlsx': 2}


class Project:
    def __init__(self, project_dir=None, tracker_path=None, output_root=None, schema_workbook=None):
        self.project_dir = project_dir or config.PROJECT_DIR
        self.tracker_path = tracker_path or config.TRACKER_PATH
        self.output_root = output_root or config.OUTPUT_ROOT
        self.schema_workbook = schema_workbook if schema_workbook is not None else config.SCHEMA_WORKBOOK

        p = self.project_dir.replace('\\', '/').rstrip('/')
        self.zone = (re.findall(r'zone=([A-Za-z0-9]+)', p) or ['NA'])[-1]
        self.phase = (re.findall(r'phase=(\d+)', p) or ['0'])[-1]
        self.cycle = (re.findall(r'cycle=(\d+)', p) or ['0'])[-1]
        self.upc_folder = os.path.basename(p)                 # N-02017-03001-RJ
        self.upc = self.upc_folder.replace('-', '_')          # N_02017_03001_RJ
        self.upc_slash = self.upc.replace('_', '/')           # N/02017/03001/RJ

        self.run_date = datetime.today().date()
        self.run_ts = pd.Timestamp(self.run_date)
        self.now_ts = pd.Timestamp.now()
        self.out_dir = os.path.join(self.output_root, f"zone={self.zone}", f"phase={self.phase}",
                                    f"cycle={self.cycle}", self.upc_folder)
        self.d_dirs = {k: self._latest_run(os.path.join(self.project_dir, k)) for k in ['d1', 'd2', 'd4']}   # D3 is not used
        self.sources_used = {}          # label → file actually used (printed + written to the run log)

    def describe(self):
        print(f"UPC {self.upc} | Zone={self.zone} Phase={self.phase} Cycle={self.cycle}")
        for k, v in self.d_dirs.items():
            print(f"  {k}: {v or 'MISSING'}")
        print(f"  output: {self.out_dir}")

    # ── file discovery ──────────────────────────────────────────────────────
    @staticmethod
    def _latest_run(dpath):
        if not os.path.isdir(dpath):
            return None
        runs = sorted(d for d in os.listdir(dpath) if d.startswith('run=') and os.path.isdir(os.path.join(dpath, d)))
        return os.path.join(dpath, runs[-1]) if runs else dpath

    def find_files(self, dkey, *must, exclude=()):
        """Files in d1..d4 whose name contains all `must` words; parquet preferred over csv/xlsx."""
        d = self.d_dirs.get(dkey)
        if not d or not os.path.isdir(d):
            return []
        best = {}
        for f in sorted(os.listdir(d)):
            stem, ext = os.path.splitext(f)
            fl = f.lower()
            if ext.lower() not in _EXT_RANK:
                continue
            if not all(m in fl for m in must) or any(x in fl for x in exclude):
                continue
            if stem not in best or _EXT_RANK[ext.lower()] < _EXT_RANK[os.path.splitext(best[stem])[1].lower()]:
                best[stem] = f
        return [os.path.join(d, f) for f in sorted(best.values())]

    @staticmethod
    def read_any(path):
        ext = os.path.splitext(path)[1].lower()
        df = (pd.read_parquet(path) if ext == '.parquet'
              else pd.read_csv(path, low_memory=False) if ext == '.csv'
              else pd.read_excel(path))
        return df.drop(columns=[c for c in df.columns if str(c).lower() == 'source_row'])

    def read_first(self, dkey, *must, exclude=()):
        files = self.find_files(dkey, *must, exclude=exclude)
        if not files:
            print(f"  WARNING: no {dkey} file matching {must}")
            return pd.DataFrame()
        return self.read_any(files[0])

    @staticmethod
    def _columns_of(path):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.parquet':
                import pyarrow.parquet as pq
                return [str(c) for c in pq.read_schema(path).names]
            if ext == '.csv':
                return [str(c) for c in pd.read_csv(path, nrows=0).columns]
            return [str(c) for c in pd.read_excel(path, nrows=0).columns]
        except Exception:
            return []

    def find_by_content(self, dkey, signature, exclude=()):
        """Files whose COLUMNS match `signature` (list of keyword groups; every group must hit one column).
        Used when the file name is not the expected one."""
        hits = []
        for f in self.find_files(dkey, exclude=exclude):
            cols = [norm_name(c) for c in self._columns_of(f)]
            if all(any(all(k in c for k in group) for c in cols) for group in signature):
                hits.append(f)
        return hits

    def locate(self, dkey, label, name_words, signature, exclude=()):
        """1) by file name words  2) by column content. Logs how the file was found."""
        files = self.find_files(dkey, *name_words, exclude=exclude)
        how = 'name'
        if not files:
            files = self.find_by_content(dkey, signature, exclude=exclude)
            how = 'content'
        if files:
            self.sources_used[label] = f"{os.path.basename(files[0])} (by {how})"
        else:
            self.sources_used[label] = 'not found'
        return files

    # ── deliverables (loaded once, on first use) ───────────────────────────
    @cached_property
    def d4_long(self):
        files = (self.find_files('d4', 'defect', 'report') or
                 self.locate('d4', 'D4 defect report', ('data_sheet',),
                             [('rut depth',), ('roughness',), ('@ 10 mtr',)]))
        if files:
            self.sources_used.setdefault('D4 defect report', f"{os.path.basename(files[0])} (by name)")
        if not files:
            raise FileNotFoundError("D4 10m defect report not found")
        df = pd.concat([d4_to_long(self.read_any(f)) for f in files], ignore_index=True)
        print(f"  D4 10m report: {[os.path.basename(f) for f in files]} → lanes {sorted(df['lane'].unique())}, {len(df):,} lane-rows")
        return df

    @cached_property
    def potholes_src(self):
        """NHAI One pothole sheet (one row per pothole). Never the pothole IMAGE list."""
        files = self.locate('d4', 'D4 NHAI One potholes', ('nhai_one', 'pothole'),
                            [('from_ch_km',), ('area',)], exclude=('image', 'defect_report', 'data_sheet'))
        if not files:
            files = self.find_files('d4', 'pothole', exclude=('defect_report', 'data_sheet', 'image'))
        return self.read_any(files[0]) if files else pd.DataFrame()

    @cached_property
    def nsv_cracks_src(self):
        """Standalone NSV cracks sheet (one row per lane per 10 m, crack area %)."""
        files = self.locate('d4', 'D4 NSV cracks', ('nsv_cracks',), [('crack area',), ('lane',), ('chainage',)],
                            exclude=('image', 'defect_report', 'data_sheet'))
        return self.read_any(files[0]) if files else pd.DataFrame()

    @cached_property
    def crack_images_src(self):
        """D4 crack image list (one row per image: file_name, image_path, chainage, latitude, longitude)."""
        files = self.locate('d4', 'D4 crack images', ('images', 'crack'), [('file_name',), ('image_path',)],
                            exclude=('pothole',))
        return self.read_any(files[0]) if files else pd.DataFrame()

    @cached_property
    def d2_ca(self):
        return self.read_first('d2', '_ca', exclude=('irc',))

    @cached_property
    def d2_irc(self):
        return self.read_first('d2', 'irc')

    @cached_property
    def d1_lrp(self):
        return self.read_first('d1', 'lrpmaster')

    @cached_property
    def d1_pavement(self):
        return self.read_first('d1', 'pavementtype')

    @cached_property
    def d1_serviceroad(self):
        return self.read_first('d1', 'serviceroad')

    @cached_property
    def d1_carriageway(self):
        return self.read_first('d1', 'carriagewaytype')

    @cached_property
    def road(self):
        from common.road import RoadContext
        return RoadContext(self)

    @cached_property
    def pci_lookup(self):
        from common.finalize import build_pci
        return build_pci(self)

    # ── tracker / survey date ───────────────────────────────────────────────
    @cached_property
    def _tracker_raw(self):
        return load_tracker_context(self)

    @cached_property
    def tracker_ctx(self):
        ctx = copy.deepcopy(self._tracker_raw)
        # Fill survey dates from the data when the tracker has none
        sts = self.survey_ts
        ctx['survey_start_date'] = ctx['survey_start_date'] or (sts.date() if sts is not None else None)
        ctx['survey_end_date'] = ctx['survey_end_date'] or ctx['survey_start_date']
        return ctx

    @cached_property
    def survey_ts(self):
        """Project survey date: D4 potholes Datetime → D1 LRP Survey Date → tracker start."""
        def mode_date(df, *kw):
            c = pick(df.columns, *kw) if not df.empty else None
            if not c:
                return None
            s = pd.to_datetime(df[c], errors='coerce').dropna()
            return s.mode().iloc[0] if len(s) else None
        ts = mode_date(self.potholes_src, 'datetime') or mode_date(self.d1_lrp, 'survey', 'date')
        if ts is None:
            raw = self._tracker_raw['survey_start_date']
            ts = pd.Timestamp(raw) if raw else None
        return ts

    @property
    def survey_date_str(self):
        """ddmmyy used in defect_id — as the DLM notebook: the FIRST non-empty Datetime of the D4 NHAI One pothole
           sheet (file order), else the run date. (survey_ts, used for the date columns, is separate.)"""
        d = self.potholes_src
        c = pick(d.columns, 'datetime') or pick(d.columns, 'date') if not d.empty else None
        s = pd.to_datetime(d[c], errors='coerce').dropna() if c else pd.Series(dtype='datetime64[ns]')
        return (s.iloc[0] if len(s) else self.run_ts).strftime('%d%m%y')

    @property
    def display_id_date_str(self):
        """ddmmyy used in display_defect_id: tracker survey end date, else survey_date_str (DLM notebook)."""
        raw = self._tracker_raw.get('survey_end_date')
        return pd.Timestamp(raw).strftime('%d%m%y') if raw else self.survey_date_str

    def threshold_name_for(self, defect):
        std = self.tracker_ctx['defect_standards'].get(defect, {}).get('standard')
        return 'As per IRC' if std == 'IRC' else 'As per CA'

    # ── thresholds / reporting interval ─────────────────────────────────────
    @cached_property
    def rules(self):
        """defect_rules.json with project_overrides[<UPC folder or UPC>] merged on top."""
        def merge(a, b):
            out = copy.deepcopy(a)
            for k, v in (b or {}).items():
                out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
            return out
        ov = config.DEFECT_RULES.get('project_overrides', {})
        o = ov.get(self.upc_folder) or ov.get(self.upc)
        if o:
            print(f"  defect_rules.json: project override applied for {self.upc_folder}")
        return merge(config.DEFECT_RULES, o)

    @cached_property
    def _thr_info(self):
        return resolve_thresholds(self)

    @property
    def thr(self):
        """{'ca': {defect: value}, 'irc': {...}} — see common/thresholds.py for the source order."""
        return self._thr_info['thr']

    @property
    def thr_src(self):
        return self._thr_info['src']

    def thr_op(self, std, defect):
        """'>' or '>=' for the breach check (default '>=')."""
        return self._thr_info['ops'].get((std, defect), '>=')

    def thr_unit(self, std, defect):
        return self._thr_info['units'].get((std, defect))

    def print_thresholds(self):
        if self._thr_info['json']:
            print(f"  thresholds json: {os.path.basename(self._thr_info['json'])}")
        for std in ['ca', 'irc']:
            print(f"  {std.upper():3s} thresholds: " + ", ".join(
                (f"{d} {self.thr_op(std, d)} {v}"
                 + (f" {self.thr_unit(std, d)}" if self.thr_unit(std, d) else "")
                 + f" ({self.thr_src.get((std, d), 'D2')})") if v is not None
                else f"{d} NOT IN D2 → threshold_value / breach_flag / road_condition NULL"
                for d, v in self.thr[std].items()))

    # D2 comparison column words per defect ('overall' = 'Overall Reporting Interval'); pothole has none in D2
    D2_INTERVAL_WORDS = {'roughness': ('roughness',), 'rutting': ('rut',), 'cracking': ('crack',),
                         'ravelling': ('ravel',)}

    def d2_interval(self, std, defect):
        """Reporting interval (m) from D2, CURRENT CYCLE only — the one place every interval comes from:
           sheet 'Comparison as per CA' (std CA) or 'as per IRC' (std IRC), column
           'Current Cycle | <defect> … | Reporting Interval' (defect 'overall': 'Overall Reporting Interval');
           the most common value of the column (D2's last, shorter block is ignored).
           No sheet / column / value → the interval of the defect in D2 'Inputs (Thresholds)' (same D2 file;
           e.g. the CA comparison sheet was not delivered or not extracted), else None.
           The tracker interval is never used."""
        key = (str(std).upper(), defect)
        cache = self.__dict__.setdefault('_d2_interval_cache', {})
        if key in cache:
            return cache[key]
        d2 = self.d2_irc if key[0] == 'IRC' else self.d2_ca
        out = None
        if d2 is not None and not d2.empty:
            for c in d2.columns:
                n = norm_name(c)
                if 'previous' in n or 'reporting interval' not in n:
                    continue
                hit = ('overall reporting interval' in n) if defect == 'overall' else (
                    'current cycle' in n and defect in self.D2_INTERVAL_WORDS
                    and all(w in n for w in self.D2_INTERVAL_WORDS[defect]))
                if hit:
                    v = num(d2[c]).dropna()
                    out = float(v.mode().iloc[0]) if len(v) else None
                    break
        if out is None and defect != 'overall':
            fallback = self._thr_info['intervals'].get((key[0].lower(), defect))
            out = float(fallback) if fallback else None
        cache[key] = out
        return out

    def reporting_interval(self, defect, std=None):
        """D2 reporting interval of the defect for a standard (default: the tracker standard of the defect)."""
        std = std or self.tracker_ctx['defect_standards'].get(defect, {}).get('standard') or 'CA'
        return self.d2_interval(std, defect)
