"""PCI_Value: 1 km chainage buckets populated with PCI from the parent defect tables."""
import logging

import numpy as np
import pandas as pd

from utils.helpers import spark_round, upc_slash, upc_underscore

log = logging.getLogger(__name__)

BUCKET_M = 1000
CHAINAGE_GRAIN = ["upc", "source_system", "defect_detected_on", "zone", "phase", "cycle", "lane"]
BUCKET_KEYS = CHAINAGE_GRAIN + ["start_chainage", "end_chainage"]
PCI_GRAIN = ["upc", "source_system", "defect_detected_on", "zone", "phase", "cycle", "pavement_type"]


def _bucket_starts(min_start, max_end):
    if pd.isna(min_start) or pd.isna(max_end):
        return []
    return list(np.arange(int(min_start), int(max_end - 1) + 1, BUCKET_M, dtype="float64"))


def build_1km_buckets(chainage_df):
    """Split each chainage grain's [floor(min start, 1 km), max end) range into 1 km buckets."""
    ch = chainage_df.assign(
        start_chainage=pd.to_numeric(chainage_df["start_chainage"], errors="coerce"),
        end_chainage=pd.to_numeric(chainage_df["end_chainage"], errors="coerce"),
    )
    grain = (
        ch.groupby(CHAINAGE_GRAIN, dropna=False)
        .agg(min_start_raw=("start_chainage", "min"), max_end=("end_chainage", "max"))
        .reset_index()
    )
    grain["min_start"] = np.floor(grain["min_start_raw"] / BUCKET_M) * BUCKET_M
    log.info("Chainage grain-level summary — %s groups", f"{len(grain):,}")

    grain["start_chainage"] = [_bucket_starts(s, e) for s, e in zip(grain["min_start"], grain["max_end"])]
    buckets = grain.explode("start_chainage").dropna(subset=["start_chainage"])
    buckets["start_chainage"] = buckets["start_chainage"].astype("float64")
    buckets["end_chainage"] = np.minimum(buckets["start_chainage"] + BUCKET_M, buckets["max_end"])

    buckets = buckets[BUCKET_KEYS].reset_index(drop=True)
    log.info("df_chainage_1km — %s rows", f"{len(buckets):,}")
    return buckets


def _defect_pci_rows(parent_df):
    src = parent_df[parent_df["threshold_name"] == "As per CA"]
    return pd.DataFrame({
        "upc": upc_underscore(src["upc"]),  # match master_chainage's '_' form
        "source_system": src["source_system"],
        "defect_detected_on": src["detected_on"],
        "zone": src["zone"],
        "phase": src["phase"],
        "cycle": src["cycle"],
        "lane": src["lane"],
        "pavement_type": src["pavement_type"],
        "defect_start": pd.to_numeric(src["start_chainage"], errors="coerce"),
        "defect_end": pd.to_numeric(src["end_chainage"], errors="coerce"),
        "pci": pd.to_numeric(src["pci"], errors="coerce"),
    })


def build_bucket_pci(buckets, defect_parents):
    """Average PCI per 1 km bucket and pavement_type.

    A defect row belongs to a bucket when it lies fully inside it (same grain). Buckets with no
    defect rows are kept with empty pavement_type / pci; a bucket spanning two pavement types is
    split into one row per pavement_type.
    """
    defects = pd.concat([_defect_pci_rows(df) for df in defect_parents], ignore_index=True)

    matched = buckets.merge(defects, on=CHAINAGE_GRAIN, how="inner")
    matched = matched[
        (matched["defect_start"] >= matched["start_chainage"])
        & (matched["defect_end"] <= matched["end_chainage"])
    ]
    joined = buckets.merge(matched[BUCKET_KEYS + ["pavement_type", "pci"]], on=BUCKET_KEYS, how="left")

    bucket_pci = joined.groupby(BUCKET_KEYS + ["pavement_type"], dropna=False)["pci"].mean()
    bucket_pci = spark_round(bucket_pci, 4).reset_index()

    log.info("df_chainage_1km_pci — %s rows | with PCI: %s | without PCI: %s",
             f"{len(bucket_pci):,}", f"{bucket_pci['pci'].notna().sum():,}",
             f"{bucket_pci['pci'].isna().sum():,}")
    return bucket_pci


def average_pci_per_grain(bucket_pci):
    """Average the bucket PCI per UPC grain (lane dropped), named to match ris_trends_df."""
    pci = bucket_pci.groupby(PCI_GRAIN, dropna=False)["pci"].mean()
    pci = spark_round(pci, 4).rename("PCI_Value").reset_index()
    pci["upc"] = upc_slash(pci["upc"])
    return pci.rename(columns={
        "upc": "UPC",
        "source_system": "Survey_Source",
        "defect_detected_on": "Defect_Detected_On",
    })


def attach_pci(ris_trends_df, chainage_df, defect_parents):
    buckets = build_1km_buckets(chainage_df)
    bucket_pci = build_bucket_pci(buckets, defect_parents)
    pci_avg = average_pci_per_grain(bucket_pci)
    log.info("PCI averages — %s rows", f"{len(pci_avg):,}")

    keys = ["UPC", "Survey_Source", "Defect_Detected_On", "phase", "cycle", "zone", "pavement_type"]
    out = ris_trends_df.merge(pci_avg, on=keys, how="left")
    log.info("PCI_Value populated: %s / %s", out["PCI_Value"].notna().sum(), len(out))
    return out
