"""Roughness_Average and Average_Rutting_Depth per grain (from the parent tables, 'As per CA')."""
import logging

import pandas as pd

from utils.helpers import spark_round

log = logging.getLogger(__name__)

GRAIN = ["upc", "source_system", "detected_on", "phase", "cycle", "zone", "pavement_type"]

RENAME_TO_TARGET = {
    "upc": "UPC",
    "source_system": "Survey_Source",
    "detected_on": "Defect_Detected_On",
}


def average_metric(parent_df, out_col, use_breach_flag=False):
    src = parent_df[parent_df["threshold_name"] == "As per CA"]
    if use_breach_flag:
        src = src[src["breach_flag"] == 1]
    src = src.assign(metric_value=pd.to_numeric(src["metric_value"], errors="coerce"))
    agg = src.groupby(GRAIN, dropna=False)["metric_value"].mean()
    return spark_round(agg, 4).rename(out_col).reset_index()


def build_ris_trends(roughness_parent, rutting_parent, use_breach_flag=False):
    """One row per grain: UPC × Survey_Source × Defect_Detected_On × phase × cycle × zone × pavement_type."""
    rough_agg = average_metric(roughness_parent, "Roughness_Average", use_breach_flag)
    rut_agg = average_metric(rutting_parent, "Average_Rutting_Depth", use_breach_flag)

    ris_trends_df = rough_agg.merge(rut_agg, on=GRAIN, how="outer").rename(columns=RENAME_TO_TARGET)

    log.info("breach_flag filter: %s", "ON" if use_breach_flag else "OFF")
    log.info("ris_trends_df created — %s rows | UPCs: %s",
             f"{len(ris_trends_df):,}", ris_trends_df["UPC"].nunique())
    return ris_trends_df
