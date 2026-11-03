"""
display_defect_id — hierarchical ids, ported from the DLM notebook post-process (Clone Update_5 Cell 13).
Runs once per UPC AFTER all tables are built; only display_defect_id changes (defect_id is left as is).

  Parent / standalone (config.DISPLAY_ID_BASE_TABLES: the four 1 km tables + master_pothole):
      breach rows → D/{ddmmyy}/N/{counter:05d}                      e.g. D/051225/N/641795
  Child 100 m / 10 m (config.DISPLAY_ID_CHILD_PARENT):
      breach rows whose 1 km parent (same parent_id AND threshold_name) has an id
                  → {parent display_defect_id}/{child_seq:04d}      e.g. D/051225/N/641795/0001
      breach rows whose parent has none (orphans) → their own D/{ddmmyy}/N/{counter:05d}
  Non-breach rows → NULL.
  ddmmyy  = tracker survey end date, else the defect_id date (prj.display_id_date_str).
  counter = one running number per date: it continues after the highest top-level counter of OTHER UPCs with
            the same date already in the output folder (<output root>/**/dlm_kpi/<table>.parquet).
  Order   = top-level: threshold_name, lane, start_chainage, end_chainage, record_id, id (blanks last);
            children: numbered inside (parent_id, threshold_name) by start_chainage, end_chainage, record_id, id.
"""
import glob
import os
import re

import numpy as np
import pandas as pd

import config

SRC_LETTER = 'N'
_ORDER = ['threshold_name', 'lane', 'start_chainage', 'end_chainage', 'record_id', 'id']
_CHILD_ORDER = ['start_chainage', 'end_chainage', 'record_id', 'id']


def _max_counter_other_upcs(prj, date_str):
    """Highest top-level counter D/{date}/[NDRO]/<n> used by other UPCs in the output folder (0 if none)."""
    top = re.compile(rf'^D/{re.escape(date_str)}/[NDRO]/(\d+)$')
    tables = list(config.DISPLAY_ID_BASE_TABLES) + list(config.DISPLAY_ID_CHILD_PARENT)
    own = os.path.normpath(prj.out_dir)
    best = 0
    for t in tables:
        for f in glob.glob(os.path.join(prj.output_root, '**', 'dlm_kpi', f'{t}.parquet'), recursive=True):
            if os.path.normpath(f).startswith(own + os.sep):
                continue
            try:
                d = pd.read_parquet(f, columns=['upc_code', 'display_defect_id'])
            except Exception:
                continue
            d = d[(d['upc_code'].astype(str) != str(prj.upc)) & d['display_defect_id'].notna()]
            m = d['display_defect_id'].astype(str).str.extract(top)[0].dropna()
            if len(m):
                best = max(best, int(m.astype(int).max()))
    return best


def _sorted_index(df, rows, cols):
    """Index of the rows (bool mask) in the given order, blanks last (as Spark asc_nulls_last)."""
    sub = df.loc[rows, [c for c in cols if c in df.columns]]
    return sub.sort_values(list(sub.columns), na_position='last', kind='mergesort').index


class _Counter:
    def __init__(self, date_str, start):
        self.date, self.n = date_str, start

    def assign(self, df, mask):
        """Number the rows of mask with the running counter: D/{date}/N/{%05d}."""
        idx = _sorted_index(df, mask, _ORDER)
        if len(idx):
            df.loc[idx, 'display_defect_id'] = [f"D/{self.date}/{SRC_LETTER}/{self.n + i:05d}"
                                                for i in range(1, len(idx) + 1)]
            self.n += len(idx)
        return df


def _breach(df):
    return pd.to_numeric(df['breach_flag'], errors='coerce').eq(1).values


def assign_display_ids(prj, tables):
    """tables: {name: DataFrame} of this UPC (built in this run, else read from its dlm_kpi folder).
       Returns {name: DataFrame} with display_defect_id set; only tables that have rows are returned."""
    names = list(config.DISPLAY_ID_BASE_TABLES) + list(config.DISPLAY_ID_CHILD_PARENT)
    work = {}
    for t in names:
        df = tables.get(t)
        if df is None:
            f = os.path.join(prj.out_dir, 'dlm_kpi', f'{t}.parquet')
            df = pd.read_parquet(f) if os.path.exists(f) else None
        if df is not None and len(df) and 'breach_flag' in df.columns:
            work[t] = df.copy()
    if not work:
        return {}

    date_str = prj.display_id_date_str
    start = _max_counter_other_upcs(prj, date_str)
    cnt = _Counter(date_str, start)
    print(f"\ndisplay_defect_id: date {date_str} | existing max counter (other UPCs): {start}")

    # Pass 1: parent / standalone tables
    for t in config.DISPLAY_ID_BASE_TABLES:
        if t not in work:
            continue
        df = work[t]
        df['display_defect_id'] = None
        cnt.assign(df, _breach(df))
        print(f"  {t}: parent/standalone ids → counter now {cnt.n}")

    # Pass 2: children inherit the parent id + /0001; orphan breaches get their own counter
    for c, p in config.DISPLAY_ID_CHILD_PARENT.items():
        if c not in work:
            continue
        df = work[c]
        df['display_defect_id'] = None
        if p in work:
            par = work[p]
            par = par[par['display_defect_id'].notna()]
            look = par.drop_duplicates(['parent_id', 'threshold_name']).set_index(['parent_id', 'threshold_name'])[
                'display_defect_id']
        else:
            look = pd.Series(dtype=object)
        key = pd.MultiIndex.from_arrays([df['parent_id'], df['threshold_name']])
        pdid = pd.Series(look.reindex(key).values if len(look) else [None] * len(df), index=df.index)
        br = _breach(df)
        has_p = br & pdid.notna().values
        n_child = 0
        if has_p.any():
            sub = df.loc[has_p].assign(_pdid=pdid[has_p])
            sub = sub.sort_values(['parent_id', 'threshold_name'] + _CHILD_ORDER, na_position='last', kind='mergesort')
            rn = sub.groupby(['parent_id', 'threshold_name'], dropna=False).cumcount() + 1
            df.loc[sub.index, 'display_defect_id'] = [f"{a}/{int(b):04d}" for a, b in zip(sub['_pdid'], rn)]
            n_child = int(has_p.sum())
        orphan = br & pdid.isna().values
        n_orph = int(orphan.sum())
        cnt.assign(df, orphan)
        print(f"  {c}: child ids = {n_child:,} | orphan (own counter) = {n_orph:,}")
    return work
