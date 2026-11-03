"""Final table: 10m chainage spine × defect flags from the defected reporting intervals.

Every 10m spine segment that falls inside a defected interval (defect_flag = 1) in the
defect report inherits that defect's flag; affected length = flag × segment length.
"""
import logging

import numpy as np
import pandas as pd

from utils.helpers import to_int, upc_slash
from utils.reporting_intervals import RI_KEYS

log = logging.getLogger(__name__)

JOIN_KEYS = ["upc", "phase", "cycle", "lane", "source_system", "detected_on"]
SPINE_KEYS = JOIN_KEYS + ["pavement_type", "start_chainage", "end_chainage"]
DEFECT_TYPES = ["roughness", "rutting", "cracking"]
FLAG_TYPES = [f"{d}_{rt}" for d in DEFECT_TYPES for rt in ("ca", "irc")]

FINAL_COLUMNS = [
    "upc", "source_system", "defect_detected_on",
    "start_chainage", "end_chainage", "phase", "cycle", "lane", "pavement_type", "length",
    "roughness_interval_ca", "roughness_interval_irc",
    "cracking_interval_ca", "cracking_interval_irc",
    "rutting_interval_ca", "rutting_interval_irc",
    "roughness_ca_flag", "rutting_ca_flag", "cracking_ca_flag", "defect_ca_flag",
    "roughness_irc_flag", "rutting_irc_flag", "cracking_irc_flag", "defect_irc_flag",
    "roughness_ca_length", "rutting_ca_length", "cracking_ca_length", "total_affected_length_ca",
    "roughness_irc_length", "rutting_irc_length", "cracking_irc_length", "total_affected_length_irc",
]


def build_spine(chainage_df):
    """10m spine from master_chainage, with upc converted to the '/' form used by the defect report."""
    spine = pd.DataFrame({
        "upc": upc_slash(chainage_df["upc"]),
        "lane": chainage_df["lane"],
        "phase": chainage_df["phase"],
        "cycle": chainage_df["cycle"],
        "source_system": chainage_df["source_system"],
        "detected_on": chainage_df["defect_detected_on"],
        "start_chainage": pd.to_numeric(chainage_df["start_chainage"], errors="coerce"),
        "end_chainage": pd.to_numeric(chainage_df["end_chainage"], errors="coerce"),
        "pavement_type": chainage_df["pavement_type"],
    })
    log.info("Master spine rows: %s | UPCs: %s | dates: %s",
             f"{len(spine):,}", spine["upc"].nunique(), spine["detected_on"].nunique())
    return spine


def _covered(segments, intervals):
    """1 where a segment [_smin, _smax] lies inside any interval [_min, _max] with the same JOIN_KEYS.

    Equivalent to the notebook's range join, without materialising segment × interval pairs:
    among intervals starting at or before the segment, the furthest-reaching end must cover it.
    """
    flags = np.zeros(len(segments), dtype=int)
    intervals = intervals.dropna(subset=["_min", "_max"]).sort_values("_min", kind="stable")
    if intervals.empty:
        return flags
    intervals = intervals.assign(_reach=intervals.groupby(JOIN_KEYS, dropna=False)["_max"].cummax())

    left = segments.dropna(subset=["_smin"]).sort_values("_smin", kind="stable")
    matched = pd.merge_asof(
        left[["_row", "_smin", "_smax"] + JOIN_KEYS],
        intervals[["_min", "_reach"] + JOIN_KEYS],
        left_on="_smin", right_on="_min", by=JOIN_KEYS, direction="backward",
    )
    flags[matched.loc[matched["_reach"] >= matched["_smax"], "_row"].to_numpy()] = 1
    return flags


def flag_spine(spine, defect_report):
    """One row per spine segment with a 0/1 flag per defect type and standard."""
    defected = defect_report[defect_report["defect_flag"] == 1]
    defected = pd.DataFrame({
        **{k: defected[k] for k in ("upc", "phase", "cycle", "lane", "source_system")},
        "detected_on": defected["defect_detected_on"],
        "_min": np.fmin(defected["start_chainage"], defected["end_chainage"]),
        "_max": np.fmax(defected["start_chainage"], defected["end_chainage"]),
        "_dt": defected["defect_type"],
    })

    segments = spine.reset_index(drop=True).assign(
        _row=lambda d: np.arange(len(d)),
        _smin=lambda d: np.fmin(d["start_chainage"], d["end_chainage"]),
        _smax=lambda d: np.fmax(d["start_chainage"], d["end_chainage"]),
    )
    for dt in FLAG_TYPES:
        segments[f"{dt}_flag"] = _covered(segments, defected[defected["_dt"] == dt])

    flag_cols = [f"{dt}_flag" for dt in FLAG_TYPES]
    return segments.groupby(SPINE_KEYS, dropna=False)[flag_cols].max().reset_index()


def build_final_table(flags, ri):
    final = flags.merge(ri, on=RI_KEYS, how="left")

    final["length"] = to_int((final["end_chainage"] - final["start_chainage"]).abs())
    for rt in ("ca", "irc"):
        final[f"defect_{rt}_flag"] = final[[f"{d}_{rt}_flag" for d in DEFECT_TYPES]].max(axis=1)

    # Affected length = flag * length for each defect type
    for dt in FLAG_TYPES:
        final[f"{dt}_length"] = final[f"{dt}_flag"] * final["length"]
    for rt in ("ca", "irc"):
        final[f"total_affected_length_{rt}"] = final[f"defect_{rt}_flag"] * final["length"]

    final = (
        final.rename(columns={"detected_on": "defect_detected_on"})[FINAL_COLUMNS]
        .sort_values(["upc", "lane", "start_chainage"], kind="stable")
        .reset_index(drop=True)
    )
    log.info("Final table: %s rows", f"{len(final):,}")
    return final

