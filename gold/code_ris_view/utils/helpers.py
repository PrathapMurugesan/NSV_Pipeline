"""Small type-normalisation helpers shared by the loaders."""
from datetime import date

import pandas as pd


def to_str_id(series):
    """Cast phase/cycle style keys to string, '2.0' -> '2', so joins match across sources."""
    return series.astype("string").str.replace(r"\.0$", "", regex=True)


def to_date(series):
    """'0202-07-14' -> datetime.date, matching the date objects in the parquet tables.
    (pd.to_datetime cannot hold years before 1677, which occur in the source data.)"""
    return series.map(lambda v: date.fromisoformat(str(v)[:10]) if pd.notna(v) else None)
