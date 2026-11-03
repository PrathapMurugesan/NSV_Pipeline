"""
Road info from D1 for any (chainage, lane):
  nh_number, section_code → nearest D1 LRP point (same direction)
  pavement_type           → D1 ServiceRoad + PavementType interval containing the chainage
                            (same direction, narrowest wins; nearest if none contains it)
  carriageway             → D1 CarriagewayType interval containing the chainage
"""
from functools import cached_property

import numpy as np
import pandas as pd

from common.utils import pick, parse_chainage, clean_text, standardize_nh, lane_dir

_JUNK = {'lcms issue', 'uc', 'na', 'n/a', '', 'nan', 'none'}


def _prep_interval(df, value_col_kw, out_name):
    if df.empty:
        return pd.DataFrame(columns=['start', 'end', 'nh_number', 'section_code', 'direction', out_name])
    c = list(df.columns)
    vc = pick(c, *value_col_kw)
    out = pd.DataFrame({
        'start': parse_chainage(df[pick(c, 'start', 'chainage')]),
        'end': parse_chainage(df[pick(c, 'end', 'chainage')]),
        'nh_number': df[pick(c, 'nh')].map(standardize_nh) if pick(c, 'nh') else None,
        'section_code': clean_text(df[pick(c, 'section')]) if pick(c, 'section') else None,
        'direction': clean_text(df[pick(c, 'direction')]) if pick(c, 'direction') else None,
        out_name: clean_text(df[vc]) if vc else None})
    sw = out['end'] < out['start']
    out.loc[sw, ['start', 'end']] = out.loc[sw, ['end', 'start']].values
    out = out.dropna(subset=['start', 'end'])
    return out[~out[out_name].fillna('').str.lower().isin(_JUNK)].reset_index(drop=True)


def dir_mask(lk, direction):
    if not direction or lk.empty or 'direction' not in lk:
        return np.ones(len(lk), bool)
    m = (lk['direction'].fillna('').str.lower() == direction.lower()).values
    return m if m.any() else np.ones(len(lk), bool)


def _interval_idx(lk, ch, direction):
    if lk.empty:
        return np.full(len(ch), -1)
    S, E = lk['start'].values.astype(float), lk['end'].values.astype(float)
    W, dm = E - S, dir_mask(lk, direction)
    out = np.empty(len(ch), dtype=int)
    for i in range(0, len(ch), 20000):
        c = ch[i:i + 20000][:, None]
        C = (S <= c) & (E >= c)
        Cd = C & dm
        use = np.where(Cd.any(1)[:, None], Cd, C)
        idx = np.where(use, W, np.inf).argmin(1)
        none = ~use.any(1)
        if none.any():
            dist = np.where(dm, np.abs((S + E) / 2 - c[none]), np.inf)
            idx[none] = dist.argmin(1)
        out[i:i + 20000] = idx
    return out


def _nearest_idx(lk, ch, direction):
    if lk.empty:
        return np.full(len(ch), -1)
    P, dm = lk['chainage'].values.astype(float), dir_mask(lk, direction)
    if not len(ch):
        return np.array([], int)
    return np.concatenate([np.where(dm, np.abs(P - ch[i:i + 20000][:, None]), np.inf).argmin(1)
                           for i in range(0, len(ch), 20000)])


def _take(lk, idx, col):
    if lk.empty or col not in lk:
        return np.full(len(idx), None, dtype=object)
    v = lk[col].values.astype(object)
    return np.where(idx >= 0, v[np.clip(idx, 0, None)], None)


class RoadContext:
    def __init__(self, prj):
        self.prj = prj

    @cached_property
    def pavement(self):
        # ServiceRoad first (old behaviour: wins ties)
        return pd.concat([_prep_interval(self.prj.d1_serviceroad, ('pavement',), 'pavement_type'),
                          _prep_interval(self.prj.d1_pavement, ('pavement',), 'pavement_type')], ignore_index=True)

    @cached_property
    def carriageway(self):
        return _prep_interval(self.prj.d1_carriageway, ('carriageway',), 'carriageway')

    @cached_property
    def lrms(self):
        df = self.prj.d1_lrp
        if df.empty:
            return pd.DataFrame(columns=['chainage', 'nh_number', 'section_code', 'direction'])
        c = list(df.columns)
        return pd.DataFrame({
            'chainage': parse_chainage(df[pick(c, 'chainage')]),
            'nh_number': df[pick(c, 'nh')].map(standardize_nh),
            'section_code': clean_text(df[pick(c, 'section')]) if pick(c, 'section') else None,
            'direction': clean_text(df[pick(c, 'direction')]) if pick(c, 'direction') else None,
        }).dropna(subset=['chainage']).sort_values('chainage').reset_index(drop=True)

    def lookup(self, start_ch, lanes):
        """DataFrame aligned to inputs: nh_number, section_code, direction, pavement_type, carriageway."""
        q = pd.DataFrame({'ch': np.asarray(start_ch, float), 'dir': [lane_dir(l) for l in lanes]})
        u = q.drop_duplicates().reset_index(drop=True)
        parts = []
        L, P, C = self.lrms, self.pavement, self.carriageway
        for d, g in u.groupby(u['dir'].fillna(''), sort=False):
            ch = g['ch'].values
            il, ip, ic = _nearest_idx(L, ch, d), _interval_idx(P, ch, d), _interval_idx(C, ch, d)
            r = g.copy()
            r['nh_number'] = pd.Series(_take(L, il, 'nh_number'), index=g.index).fillna(
                pd.Series(_take(P, ip, 'nh_number'), index=g.index))
            r['section_code'] = pd.Series(_take(L, il, 'section_code'), index=g.index).fillna(
                pd.Series(_take(P, ip, 'section_code'), index=g.index))
            r['ctx_direction'] = pd.Series(_take(L, il, 'direction'), index=g.index).fillna(
                pd.Series(_take(P, ip, 'direction'), index=g.index))
            r['pavement_type'] = _take(P, ip, 'pavement_type')
            r['carriageway'] = _take(C, ic, 'carriageway')
            parts.append(r)
        u = pd.concat(parts)
        out = q.merge(u, on=['ch', 'dir'], how='left')
        out['direction'] = out['dir'].where(out['dir'].notna(), out['ctx_direction'])
        return out[['nh_number', 'section_code', 'direction', 'pavement_type', 'carriageway']].replace({np.nan: None})
