"""Affected lengths from final_intermediate.

Roughness / Rutting / Cracking -> summed from the <defect>_<std>_length columns
Pothole / Ravelling           -> 0
Total                         -> summed from total_affected_length_<std>, one row per UPC grain
"""
import logging

import pandas as pd

from utils.project_master import PROJECT_COLUMNS

log = logging.getLogger(__name__)

LENGTH_COLS = ["affected_length_ca_m", "affected_length_irc_m", "affected_length_ca_km", "affected_length_irc_km"]
DEFECT_LENGTH_COLUMNS = {
    "Roughness": ("roughness_ca_length", "roughness_irc_length"),
    "Rutting": ("rutting_ca_length", "rutting_irc_length"),
    "Cracking": ("cracking_ca_length", "cracking_irc_length"),
}
UPC_GRAIN = ["upc", "source_system", "detected_on", "phase", "cycle"]


def _sum_lengths(intermediate, grain, ca_col, irc_col):
    out = (
        intermediate.rename(columns={"defect_detected_on": "detected_on"})
        .groupby(grain, dropna=False)
        .agg(affected_length_ca_m=(ca_col, "sum"), affected_length_irc_m=(irc_col, "sum"))
        .astype("float64")
        .reset_index()
    )
    out["affected_length_ca_km"] = out["affected_length_ca_m"] / 1000
    out["affected_length_irc_km"] = out["affected_length_irc_m"] / 1000
    return out


def attach_defect_lengths(counts, intermediate):
    """Per-defect affected lengths joined on the defect grain (pavement_type null-safe)."""
    grain = UPC_GRAIN + ["pavement_type"]
    lengths = pd.concat(
        [_sum_lengths(intermediate, grain, ca, irc).assign(defect_type=defect)
         for defect, (ca, irc) in DEFECT_LENGTH_COLUMNS.items()],
        ignore_index=True,
    )
    log.info("aff_len_df — %s rows", f"{len(lengths):,}")

    out = counts.merge(lengths, on=grain + ["defect_type"], how="left")
    out[LENGTH_COLS] = out[LENGTH_COLS].fillna(0)
    log.info("ris_view_df5 — %s rows × %s cols", f"{len(out):,}", out.shape[1])
    return out


def build_total_rows(intermediate, defect_rows):
    """'Total' rows per UPC grain: total affected lengths, zone / project details taken from the
    defect rows, and the defect counts summed across defect types."""
    totals = _sum_lengths(intermediate, UPC_GRAIN, "total_affected_length_ca", "total_affected_length_irc")
    totals["defect_type"] = "Total"
    totals["pavement_type"] = pd.NA

    enrich_cols = ["zone"] + list(PROJECT_COLUMNS)
    lookup = defect_rows[UPC_GRAIN + enrich_cols].drop_duplicates(UPC_GRAIN)
    defect_totals = (
        defect_rows.groupby(UPC_GRAIN, dropna=False)[["Total_Defects_IRC", "Total_Defects_CA"]].sum().reset_index()
    )

    totals = totals.merge(lookup, on=UPC_GRAIN, how="left").merge(defect_totals, on=UPC_GRAIN, how="left")
    log.info("df_interm_rollup2 — %s rows", f"{len(totals):,}")
    return totals
