"""Row filters: UPC / pavement_type on the silver tables, project-master UPCs on the KPI rows."""
import logging

from utils.helpers import upc_slash

log = logging.getLogger(__name__)


def filter_upcs(tables, keep_upcs):
    """Keep only selected UPCs. Compared on the '/' form since master_chainage uses '_'."""
    if not keep_upcs:
        log.info("KEEP_UPCS empty — all UPCs kept")
        return tables
    return {name: df[upc_slash(df["upc"]).isin(keep_upcs).fillna(False)] for name, df in tables.items()}


def filter_pavement_types(tables, keep_pavement_types):
    filtered = {}
    for name, df in tables.items():
        if "pavement_type" in df.columns:
            filtered[name] = df[df["pavement_type"].isin(keep_pavement_types)]
        else:
            log.info("  %s: no pavement_type column, not filtered", name)
            filtered[name] = df
    return filtered


def log_table_summary(tables):
    for name, df in tables.items():
        upcs = ", ".join(sorted(map(str, upc_slash(df["upc"]).dropna().unique())))
        info = f"{name:30s} — {len(df):>8,} rows | UPCs: {upcs}"
        for col in ("pavement_type", "threshold_name"):
            if col in df.columns:
                info += f" | {col}: {', '.join(sorted(map(str, df[col].dropna().unique())))}"
        log.info(info)


def filter_contract_upcs(df, cm_upcs):
    """Drop rows whose UPC is not in the contract-module project master (skipped when None)."""
    if cm_upcs is None:
        return df
    missing_mask = ~df["UPC"].isin(cm_upcs)
    missing = df.loc[missing_mask, "UPC"].unique()
    if len(missing):
        log.info("%s UPC(s) not found in cm_df: %s", len(missing), ", ".join(map(str, missing)))
    log.info("Dropped %s rows not in cm_df — %s rows remaining", f"{int(missing_mask.sum()):,}",
             f"{int((~missing_mask).sum()):,}")
    return df[~missing_mask].reset_index(drop=True)
