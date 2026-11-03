"""Defect report: metric per reporting-interval window, flagged where metric_value > threshold_value.

The source table is chosen per defect / standard (CA, IRC) / upc / phase / cycle from the
reporting interval in master_reporting_interval; the first table with rows for that standard is used:
  1000 m -> parent (1 km segments, as-is) | child 10m averaged into 1000 m | child 100m (as-is)
  100 m  -> child 100m (as-is)            | child 10m averaged into 100 m
  other  -> child 10m averaged into the interval | child 100m (as-is)
The provider's parent / child 100m values are used where the interval matches them: for roughness
they are not the plain average of the 10m values (rutting / cracking child 100m are).
"""
import logging

import numpy as np
import pandas as pd

from utils.helpers import to_int
from utils.reporting_intervals import RI_KEYS

log = logging.getLogger(__name__)

# Notebook table key -> silver file name (the cracking 10m table comes from settings)
SOURCE_TABLES = {
    "roughness_parent": "master_roughness_parent",
    "roughness": "master_roughness_child_10m",
    "roughness_child_100m": "master_roughness_child_100m",
    "rutting_parent": "master_rutting_parent",
    "rutting": "master_rutting_child_10m",
    "rutting_child_100m": "master_rutting_child_100m",
    "cracking_parent": "master_cracking_parent",
    "cracking_child_100m": "master_cracking_child_100m",
}

# defect -> 10m base table key (parent / child 100m keys are <defect>_parent / <defect>_child_100m)
DEFECTS = {"roughness": "roughness", "rutting": "rutting", "cracking": "cracking"}

PARENT_INTERVAL = 1000
CHILD_100M_INTERVAL = 100

REPORT_TYPES = {"ca": "As per CA", "irc": "As per IRC"}

SRC_COLS = ["source_system", "upc", "phase", "cycle", "lane",
            "start_chainage", "end_chainage", "detected_on",
            "metric_value", "threshold_name", "threshold_value"]

WINDOW_KEYS = ["source_system", "upc", "phase", "cycle", "lane", "detected_on"]


def prepare_source(df):
    out = df[SRC_COLS].copy()
    for col in ("start_chainage", "end_chainage", "metric_value", "threshold_value"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _candidates(defect, base, interval):
    """Source preference for one interval -> list of (interval, table_key, needs_agg).
    interval None means the source segments are used as-is."""
    parent = (None, f"{defect}_parent", False)
    child_100m = (None, f"{defect}_child_100m", False)
    averaged = (interval, base, True)
    if interval == PARENT_INTERVAL:
        return [parent, averaged, child_100m]
    if interval == CHILD_100M_INTERVAL:
        return [child_100m, averaged]
    return [averaged, child_100m]


def _has_rows(ri_rows, table_key, rt, sources):
    """Boolean mask: ri_rows whose upc/phase/cycle have rows for this standard in the table."""
    src = sources[table_key]
    avail = src.loc[src["threshold_name"] == REPORT_TYPES[rt], RI_KEYS].drop_duplicates()
    return (ri_rows.merge(avail, on=RI_KEYS, how="left", indicator=True)["_merge"] == "both").to_numpy()


def _source_groups(defect, base, rt, ri_rt, interval_col, sources):
    """Routing for one defect / standard -> list of (interval, table_key, needs_agg, ri_rows)."""
    groups = []
    for iv in sorted(ri_rt[interval_col].unique()):
        remaining = ri_rt[ri_rt[interval_col] == iv]
        for interval, table_key, needs_agg in _candidates(defect, base, int(iv)):
            found = _has_rows(remaining, table_key, rt, sources)
            if found.any():
                groups.append((interval, table_key, needs_agg, remaining[found]))
            remaining = remaining[~found]
    return groups


def _report_part(defect, rt, interval, table_key, needs_agg, ri_rows, sources):
    src = sources[table_key]
    src = src[src["threshold_name"] == REPORT_TYPES[rt]]
    matched = src.merge(ri_rows[RI_KEYS], on=RI_KEYS, how="inner")

    if needs_agg:
        window_start = np.floor(np.fmin(matched["start_chainage"], matched["end_chainage"]) / interval) * interval
        matched = (
            matched.assign(start_chainage=window_start, end_chainage=window_start + interval)
            .groupby(WINDOW_KEYS + ["start_chainage", "end_chainage"], dropna=False)
            .agg(metric_value=("metric_value", "mean"), threshold_value=("threshold_value", "max"))
            .reset_index()
        )

    # parent / child 100m used as-is: actual segment length; otherwise the RI window
    if interval is None:
        reporting_interval = to_int((matched["end_chainage"] - matched["start_chainage"]).abs())
    else:
        reporting_interval = pd.Series(int(interval), index=matched.index, dtype="Int64")

    return pd.DataFrame({
        "source_system": matched["source_system"],
        "upc": matched["upc"],
        "upc_code_as_per_om_master": matched["upc"],
        "phase": matched["phase"],
        "cycle": matched["cycle"],
        "defect_detected_on": matched["detected_on"],
        "lane": matched["lane"],
        "start_chainage": matched["start_chainage"],
        "end_chainage": matched["end_chainage"],
        "defect_type": f"{defect}_{rt}",
        "reporting_interval": reporting_interval,
        "metric_value": matched["metric_value"],
        "threshold_value": matched["threshold_value"],
        "defect_flag": (matched["metric_value"] > matched["threshold_value"]).astype(int),
        "source_table": table_key,
    })


def build_defect_report(sources, ri):
    """sources: notebook table key -> prepared source DataFrame; ri: wide reporting intervals."""
    parts = []
    for defect, base in DEFECTS.items():
        for rt in REPORT_TYPES:
            interval_col = f"{defect}_interval_{rt}"
            ri_rt = ri[RI_KEYS + [interval_col]]
            for interval, table_key, needs_agg, ri_rows in _source_groups(defect, base, rt, ri_rt, interval_col, sources):
                parts.append(_report_part(defect, rt, interval, table_key, needs_agg, ri_rows, sources))
    log.info("Defect report parts: %s", len(parts))

    report = (
        pd.concat(parts, ignore_index=True)
        .sort_values(["upc", "defect_type", "phase", "cycle", "lane", "start_chainage"], kind="stable")
        .reset_index(drop=True)
    )

    log.info("Full report: %s rows | defect types: %s",
             f"{len(report):,}", sorted(report["defect_type"].unique()))
    counts = report.groupby(["upc", "defect_type", "source_table"]).size()
    for (upc, dt, table), n in counts.items():
        log.info("  %s | %-14s | %-22s | %s rows", upc, dt, table, f"{n:,}")
    return report
