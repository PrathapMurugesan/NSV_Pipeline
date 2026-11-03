"""Defect rows from all defect tables, project details, and breached-defect counts per grain."""
import logging

import pandas as pd

log = logging.getLogger(__name__)

GRAIN_COLS = ["source_system", "upc", "phase", "cycle", "detected_on", "defect_type", "zone", "pavement_type"]
DETAIL_COLS = ["defect_id", "affected_length_ca", "affected_length_irc", "breach_flag", "threshold_name"]


def combine_defects(tables):
    """All rows (no breach_flag filter here; it is applied in the defect counts)."""
    combined = (
        pd.concat([df[GRAIN_COLS + DETAIL_COLS] for df in tables], ignore_index=True)
        .drop_duplicates()
        .reset_index(drop=True)
    )
    log.info("ris_view_df1 — %s rows", f"{len(combined):,}")
    return combined


def count_defects(defects, project_info):
    """Left-join project details on upc, then count distinct breached defect_ids per grain.

    A defect counts for a standard when breach_flag == 1, the row is that standard's threshold
    and its affected length for that standard is > 0.
    """
    df = defects.merge(project_info, on="upc", how="left")
    group_cols = [c for c in df.columns if c not in DETAIL_COLS]

    breached = df["breach_flag"] == 1
    irc = breached & (df["threshold_name"] == "As per IRC") & (df["affected_length_irc"] > 0)
    ca = breached & (df["threshold_name"] == "As per CA") & (df["affected_length_ca"] > 0)

    counts = (
        df.assign(_irc_id=df["defect_id"].where(irc), _ca_id=df["defect_id"].where(ca))
        .groupby(group_cols, dropna=False)
        .agg(Total_Defects_IRC=("_irc_id", "nunique"), Total_Defects_CA=("_ca_id", "nunique"))
        .reset_index()
    )
    log.info("ris_view_df4 — %s rows × %s cols", f"{len(counts):,}", counts.shape[1])
    return counts
