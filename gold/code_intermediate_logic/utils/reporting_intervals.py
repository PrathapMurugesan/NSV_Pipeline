"""Reporting intervals (RI) per UPC / phase / cycle from the silver master_reporting_interval table.

Replaces the Databricks table nhai_dhms.ris_kpi_output.upc_reporting_intervals.
Two layouts are accepted, both turned into one row per upc / phase / cycle with
<defect>_interval_ca / <defect>_interval_irc:

- wide: one row per UPC_code / Phase / Cycle with <Defect>_Interval_CA / <Defect>_Interval_IRC
- long: one row per upc / phase / cycle / standard (CA or IRC) with <defect>_reporting_interval
        (falls back to overall_reporting_interval); a standard with no row gets DEFAULT_INTERVAL

Empty intervals fall back to DEFAULT_INTERVAL.
"""
import logging

import pandas as pd

from utils.helpers import to_int, to_str_id
from utils.io_utils import find_table_file, read_table_file

log = logging.getLogger(__name__)

RI_KEYS = ["upc", "phase", "cycle"]
DEFECTS = ["roughness", "rutting", "cracking"]
STANDARDS = ["ca", "irc"]

INTERVAL_COLUMNS = [f"{d}_interval_{s}" for d in DEFECTS for s in STANDARDS]


def _digits(series):
    """regexp_extract(col, '(\\d+)', 1): 'Phase 2' -> '2', 2 -> '2'."""
    return to_str_id(series.astype("string").str.extract(r"(\d+)", expand=False))


def _drop_duplicates(ri, label):
    dups = ri.duplicated(RI_KEYS, keep="first")
    if dups.any():
        log.warning("%s duplicate upc/phase/cycle rows%s — keeping the first of each", int(dups.sum()), label)
        ri = ri[~dups]
    return ri


def _from_wide(raw):
    ri = pd.DataFrame({
        "upc": raw["UPC_code"].astype("string"),
        "phase": _digits(raw["Phase"]),
        "cycle": _digits(raw["Cycle"]),
    })
    for defect in DEFECTS:
        for standard in STANDARDS:
            ri[f"{defect}_interval_{standard}"] = to_int(raw[f"{defect.capitalize()}_Interval_{standard.upper()}"])
    return _drop_duplicates(ri, "")


def _standard_rows(raw, standard):
    """Rows for one standard ('ca' / 'irc'); a standard value like 'CA & IRC' counts for both."""
    std = raw["standard"].astype("string").str.upper()
    rows = raw[std.str.contains(standard.upper(), regex=False).fillna(False).astype(bool)]

    out = pd.DataFrame({
        "upc": rows["upc"].astype("string"),
        "phase": _digits(rows["phase"]),
        "cycle": _digits(rows["cycle"]),
    })
    overall = to_int(rows["overall_reporting_interval"])
    for defect in DEFECTS:
        out[f"{defect}_interval_{standard}"] = to_int(rows[f"{defect}_reporting_interval"]).fillna(overall)

    return _drop_duplicates(out, f" for standard {standard.upper()}")


def _from_long(raw, default_interval):
    ca = _standard_rows(raw, "ca")
    irc = _standard_rows(raw, "irc")
    ri = ca.merge(irc, on=RI_KEYS, how="outer", indicator=True)

    for side, std in (("right_only", "CA"), ("left_only", "IRC")):
        n = int((ri["_merge"] == side).sum())
        if n:
            log.warning("%s upc/phase/cycle rows have no %s reporting interval — using DEFAULT_INTERVAL=%s",
                        n, std, default_interval)
    return ri.drop(columns="_merge")


def load_reporting_intervals(input_dir, table_name, default_interval):
    """One row per upc / phase / cycle with an interval (m) per defect and standard.
    Empty intervals fall back to default_interval."""
    path = find_table_file(input_dir, table_name)
    raw = read_table_file(path)

    ri = _from_long(raw, default_interval) if "standard" in raw.columns else _from_wide(raw)
    for col in INTERVAL_COLUMNS:
        ri[col] = ri[col].fillna(default_interval).astype("Int64")

    log.info("Reporting intervals loaded — %s upc/phase/cycle rows (from %s)", f"{len(ri):,}", path.name)
    return ri[RI_KEYS + INTERVAL_COLUMNS].reset_index(drop=True)
