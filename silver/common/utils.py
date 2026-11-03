"""Small helpers used everywhere: column picking, numbers, chainage ('677+100'), NH number cleanup."""
import re
import numpy as np
import pandas as pd

import config


def norm_name(c):
    """Column name → lowercase, single spaces, no newlines."""
    return re.sub(r'\s+', ' ', str(c).replace('\n', ' ')).strip().lower()


def pick(cols, *keywords, exclude=()):
    """First column whose name contains ALL keywords and NONE of `exclude`."""
    for c in cols:
        n = norm_name(c)
        if all(k in n for k in keywords) and not any(x in n for x in exclude):
            return c
    return None


def num(s):
    return pd.to_numeric(s, errors='coerce')


def parse_chainage(s):
    """677000 | 677000.0 | '677+000' | '677+050.5' → metres (float)."""
    s = pd.Series(s)
    txt = s.astype(str).str.strip()
    plus = txt.str.match(r'^\d+\s*\+\s*\d+(\.\d+)?$')
    out = num(s).astype(float)
    if plus.any():
        parts = txt[plus].str.split('+', expand=True)
        out[plus] = num(parts[0]) * 1000 + num(parts[1])
    return out


def clean_text(s):
    """Strip text; blanks / NaN → None."""
    s = pd.Series(s, dtype='object')
    return s.where(s.notna(), None).map(lambda v: (str(v).strip() or None) if v is not None else None)


_NH_RE = re.compile(r'^\s*(?:N\.?\s*H\.?)?\s*[-_/ ]*0*(\d+)\s*([A-Za-z]*)\s*$', re.I)


def standardize_nh(v):
    """NH0027 / NH 27 / 27 → NH-27."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    v = str(v).strip()
    if not v or v.lower() == 'nan':
        return None
    m = _NH_RE.match(v)
    return f"NH-{int(m.group(1))}{m.group(2).upper()}" if m else v.upper()


def lane_dir(lane):
    return config.LANE_TO_DIRECTION.get(str(lane).strip().upper()) if lane is not None else None


def coalesce(*series):
    out = series[0].copy()
    for s in series[1:]:
        out = out.where(out.notna(), s)
    return out
