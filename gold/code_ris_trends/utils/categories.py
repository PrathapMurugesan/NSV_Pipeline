"""Roughness & rutting condition categories (CA and IRC) from dlm_threshold_mapping_display.

- CA  source: master_<defect>_parent     (threshold_name == 'As per CA')
- IRC source: master_<defect>_child_10m  (threshold_name == 'As per IRC')
- CA and IRC are each grouped by their own pavement_type and full-outer joined:
    pavement_type only in child  -> extra grain rows, CA columns empty
    pavement_type only in parent -> IRC columns empty
- CA  : Good (<= CA Threshold) | Poor (> CA Threshold) | null
- IRC : Good / Fair / Poor -> operator + value parsed per UPC from
        'IRC Threshold - Good' / '- Fair' / '- Bad' (e.g. '<= 2000' vs '<2000')
- lane_length_* = sum(end_chainage - start_chainage) in metres for rows in that category
- Each grain gets 3 rows (row_rank 1..3); child-only grains are added to ris_trends_df
"""
import logging
from functools import reduce

import numpy as np
import pandas as pd

from utils.helpers import GE, GT, LE, LT, null_safe_compare, spark_round, to_str_id, upc_slash
from utils.kpi_metrics import RENAME_TO_TARGET

log = logging.getLogger(__name__)

GRAIN = ["upc", "source_system", "detected_on", "phase", "cycle", "zone", "pavement_type"]
THRESHOLD_KEYS = ["upc", "phase", "cycle", "zone"]
KEY_COLS = ["UPC", "Survey_Source", "Defect_Detected_On", "phase", "cycle", "zone", "pavement_type"]
DEFECTS = {"roughness": "mm/km", "rutting": "mm"}  # defect -> default unit

NUM = r"(\d+(?:\.\d+)?)"

# Silver dlm_threshold_mapping (snake_case) -> Databricks dlm_threshold_mapping_display names
THRESHOLD_ALIASES = {
    "defect": "Defect", "upc": "UPC", "phase": "Phase", "cycle": "Cycle", "zone": "Zone", "unit": "Unit",
    "ca_threshold": "CA Threshold",
    "irc_good": "IRC Threshold - Good", "irc_fair": "IRC Threshold - Fair", "irc_bad": "IRC Threshold - Bad",
}


def category_columns(defect):
    return [f"{defect}_ca_category", f"{defect}_ca_threshold", f"lane_length_{defect}_ca",
            f"{defect}_irc_category", f"{defect}_irc_threshold", f"lane_length_{defect}_irc"]


def _cond_text(raw):
    """'Good (<2000mm/km)' -> '<2000mm/km' ; '<= 2000 mm/km' -> '<= 2000 mm/km'"""
    raw = raw.astype("string")
    inner = raw.str.extract(r"\(([^)]*)\)", expand=False)
    return inner.where(inner.notna() & (inner != ""), raw).str.strip()


def _fmt_number(x):
    """2750.0 -> '2750', 2750.5 -> '2750.50'"""
    if pd.isna(x):
        return pd.NA
    s = f"{x:.2f}"
    return s[:-3] if s.endswith(".00") else s


def _wrap(*parts):
    """Spark concat(): null if any part is null."""
    parts = [p.astype("string") if isinstance(p, pd.Series) else p for p in parts]
    return reduce(lambda a, b: a + b, parts)


def prepare_thresholds(threshold_mapping, defect):
    """Thresholds per UPC / phase / cycle / zone with the IRC operators and values parsed."""
    threshold_mapping = threshold_mapping.rename(
        columns={k: v for k, v in THRESHOLD_ALIASES.items() if v not in threshold_mapping.columns})
    m = threshold_mapping[threshold_mapping["Defect"].astype("string").str.lower() == defect]
    thr = pd.DataFrame({
        "upc": upc_slash(m["UPC"]),
        "phase": to_str_id(m["Phase"]),
        "cycle": to_str_id(m["Cycle"]),
        "zone": m["Zone"],
        "unit": m["Unit"],
        "ca_thr": pd.to_numeric(m["CA Threshold"], errors="coerce"),
        "irc_good_txt": _cond_text(m["IRC Threshold - Good"]),
        "irc_fair_txt": _cond_text(m["IRC Threshold - Fair"]),
        "irc_poor_txt": _cond_text(m["IRC Threshold - Bad"]),
    }).drop_duplicates(THRESHOLD_KEYS)

    as_num = lambda s: pd.to_numeric(s, errors="coerce")
    # Good: operator (< or <=) and value
    thr["irc_good_op"] = thr["irc_good_txt"].str.extract(r"(<=|<)", expand=False)
    thr["irc_good_val"] = as_num(thr["irc_good_txt"].str.extract(NUM, expand=False))
    # Fair: range 'a - b'
    thr["irc_fair_lo"] = as_num(thr["irc_fair_txt"].str.extract(NUM, expand=False))
    thr["irc_fair_hi"] = as_num(thr["irc_fair_txt"].str.extract(r"\d+(?:\.\d+)?\s*-\s*(\d+(?:\.\d+)?)", expand=False))
    # Poor: operator (> or >=) and value
    thr["irc_poor_op"] = thr["irc_poor_txt"].str.extract(r"(>=|>)", expand=False)
    thr["irc_poor_val"] = as_num(thr["irc_poor_txt"].str.extract(NUM, expand=False))
    return thr


def _prep_source(df, threshold_name, thr):
    src = df[df["threshold_name"] == threshold_name]
    out = src[GRAIN].copy()
    out["mv"] = pd.to_numeric(src["metric_value"], errors="coerce")
    out["len_m"] = (pd.to_numeric(src["end_chainage"], errors="coerce")
                    - pd.to_numeric(src["start_chainage"], errors="coerce"))
    return out.merge(thr, on=THRESHOLD_KEYS, how="left")


def _slot(src, rank, category, text, cond, prefix, length_col):
    """One row per grain for a category slot: category, threshold text, summed lane length."""
    work = src[GRAIN].copy()
    work["text"] = text
    if cond is None:
        work["length"] = np.nan
    else:
        # when(cond, len_m).otherwise(0.0): null conditions count as 0
        work["length"] = src["len_m"].where(cond.fillna(False).astype(bool), 0.0)

    grouped = work.groupby(GRAIN, dropna=False)
    out = grouped["text"].first().to_frame(f"{prefix}_threshold")
    out[length_col] = spark_round(grouped["length"].sum(min_count=1), 3)
    out.insert(0, f"{prefix}_category", category)
    out["row_rank"] = rank
    return out.reset_index()


def build_defect_categories(defect, parent_df, child_df, thresholds, default_unit):
    """3 rows per grain (row_rank 1..3) with <defect>_ca_* / <defect>_irc_* / lane_length_* columns."""
    thr = prepare_thresholds(thresholds, defect)
    ca_src = _prep_source(parent_df, "As per CA", thr)
    irc_src = _prep_source(child_df, "As per IRC", thr)

    # CA : slot1 Good, slot2 Poor, slot3 null
    unit = ca_src["unit"].astype("string").fillna(default_unit)
    ca_thr_txt = ca_src["ca_thr"].map(_fmt_number).astype("string")
    mv = ca_src["mv"]
    ca_slots = [
        _slot(ca_src, 1, "Good", _wrap("(<= ", ca_thr_txt, " ", unit, ")"),
              null_safe_compare(mv, ca_src["ca_thr"], LE), f"{defect}_ca", f"lane_length_{defect}_ca"),
        _slot(ca_src, 2, "Poor", _wrap("(> ", ca_thr_txt, " ", unit, ")"),
              null_safe_compare(mv, ca_src["ca_thr"], GT), f"{defect}_ca", f"lane_length_{defect}_ca"),
        _slot(ca_src, 3, None, pd.NA, None, f"{defect}_ca", f"lane_length_{defect}_ca"),
    ]

    # IRC: slot1 Good, slot2 Poor, slot3 Fair (bounds + operators from the IRC columns)
    #      -> CA Good pairs with IRC Good, CA Poor with IRC Poor, CA empty with IRC Fair
    mv = irc_src["mv"]
    good_lt = (irc_src["irc_good_op"] == "<").fillna(False).astype(bool)
    poor_ge = (irc_src["irc_poor_op"] == ">=").fillna(False).astype(bool)
    irc_good = null_safe_compare(mv, irc_src["irc_good_val"], LT).where(
        good_lt, null_safe_compare(mv, irc_src["irc_good_val"], LE))
    irc_poor = null_safe_compare(mv, irc_src["irc_poor_val"], GE).where(
        poor_ge, null_safe_compare(mv, irc_src["irc_poor_val"], GT))
    # Fair = inside the Fair range, excluding values already counted as Good or Poor
    irc_fair = (null_safe_compare(mv, irc_src["irc_fair_lo"], GE)
                & null_safe_compare(mv, irc_src["irc_fair_hi"], LE)
                & ~irc_good & ~irc_poor)
    irc_slots = [
        _slot(irc_src, 1, "Good", _wrap("(", irc_src["irc_good_txt"], ")"), irc_good,
              f"{defect}_irc", f"lane_length_{defect}_irc"),
        _slot(irc_src, 2, "Poor", _wrap("(", irc_src["irc_poor_txt"], ")"), irc_poor,
              f"{defect}_irc", f"lane_length_{defect}_irc"),
        _slot(irc_src, 3, "Fair", _wrap("(", irc_src["irc_fair_txt"], ")"), irc_fair,
              f"{defect}_irc", f"lane_length_{defect}_irc"),
    ]

    ca_all = pd.concat(ca_slots, ignore_index=True)
    irc_all = pd.concat(irc_slots, ignore_index=True)
    # Full outer: grains only in child keep empty CA columns, grains only in parent keep empty IRC columns
    return ca_all.merge(irc_all, on=GRAIN + ["row_rank"], how="outer")


def build_category_table(tables, thresholds):
    frames = [
        build_defect_categories(defect, tables[f"master_{defect}_parent"], tables[f"master_{defect}_child_10m"],
                                thresholds, unit)
        for defect, unit in DEFECTS.items()
    ]
    cat = reduce(lambda a, b: a.merge(b, on=GRAIN + ["row_rank"], how="outer"), frames)
    return cat.rename(columns=RENAME_TO_TARGET)


def merge_categories(ris_trends_df, cat_df, cm_upcs):
    """Outer merge -> each grain has 3 rows; grains only in the category data become new rows."""
    grains = (ris_trends_df[KEY_COLS].drop_duplicates()
              .merge(cat_df[KEY_COLS].drop_duplicates(), on=KEY_COLS, how="outer", indicator=True))
    log.info("Grains only in ris_trends_df (no category data): %s", (grains["_merge"] == "left_only").sum())
    log.info("Grains only in category data (new rows):         %s", (grains["_merge"] == "right_only").sum())

    before = len(ris_trends_df)
    merged = ris_trends_df.merge(cat_df, on=KEY_COLS, how="outer", indicator="_src")
    new_mask = merged["_src"] == "right_only"

    # New rows: Roughness_Average / Average_Rutting_Depth / PCI_Value stay empty (they come from parent).
    # Same rule as the project-master filter step: UPC must be in cm_df
    drop_mask = pd.Series(False, index=merged.index)
    if cm_upcs is not None:
        drop_mask = new_mask & ~merged["UPC"].isin(cm_upcs)
    log.info("New rows dropped (UPC not in cm_df): %s", int(drop_mask.sum()))

    out = (
        merged[~drop_mask]
        .drop(columns="_src")
        .sort_values(KEY_COLS + ["row_rank"])
        .reset_index(drop=True)
    )

    n_grains = len(out[KEY_COLS].drop_duplicates())
    log.info("Rows: %s -> %s (expected %s = %s grains x 3)",
             f"{before:,}", f"{len(out):,}", f"{n_grains * 3:,}", f"{n_grains:,}")
    for col in ("roughness_ca_threshold", "rutting_ca_threshold"):
        log.info("Rows with %s: %s", col, out[col].notna().sum())
    return out
