"""Small helpers that reproduce Spark semantics the notebook relied on."""
import numpy as np
import pandas as pd


def upc_slash(series):
    """N_02015_03002_GJ -> N/02015/03002/GJ (defect tables use '/', master_chainage uses '_')."""
    return series.astype("string").str.replace("_", "/", regex=False)


def to_str_id(series):
    """Cast phase/cycle style keys to string, '2.0' -> '2', so joins match across sources."""
    return series.astype("string").str.replace(r"\.0$", "", regex=True)


def to_int(series):
    """Spark .cast('int') on a double: truncate toward zero, null stays null."""
    return np.trunc(pd.to_numeric(series, errors="coerce")).astype("Int64")
