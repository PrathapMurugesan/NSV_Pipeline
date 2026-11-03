"""Small helpers that reproduce Spark semantics the notebook relied on."""
import operator
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

LT, LE, GT, GE = operator.lt, operator.le, operator.gt, operator.ge


def upc_slash(series):
    """N_02015_03002_GJ -> N/02015/03002/GJ (defect tables use '/', master_chainage uses '_')."""
    return series.astype("string").str.replace("_", "/", regex=False)


def upc_underscore(series):
    return series.astype("string").str.replace("/", "_", regex=False)


def to_str_id(series):
    """Cast phase/cycle style keys to string, '2.0' -> '2', so joins match across sources."""
    return series.astype("string").str.replace(r"\.0$", "", regex=True)


def spark_round(series, decimals):
    """Round half-up like Spark's F.round (pandas .round() rounds half-to-even)."""
    exp = Decimal(1).scaleb(-decimals)

    def _round(x):
        if pd.isna(x):
            return np.nan
        return float(Decimal(repr(float(x))).quantize(exp, rounding=ROUND_HALF_UP))

    return series.map(_round).astype("float64")


def null_safe_compare(left, right, op):
    """Element-wise comparison returning nullable booleans: <NA> when either side is null,
    so &, | and ~ follow Spark's three-valued logic instead of treating NaN as False."""
    left = pd.to_numeric(left, errors="coerce")
    right = pd.to_numeric(right, errors="coerce")
    result = pd.Series(op(left, right), index=left.index, dtype="boolean")
    result[left.isna() | right.isna()] = pd.NA
    return result
