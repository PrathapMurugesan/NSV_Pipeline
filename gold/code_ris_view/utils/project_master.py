"""Contract-module project master: UPC filter and project details (length, state, NH, ...)."""
import logging

import pandas as pd

from utils.io_utils import read_table_file

log = logging.getLogger(__name__)

# RIS view column -> project master column (same names as Databricks project_master_onetime)
PROJECT_COLUMNS = {
    "UPC Length(in km)": "Length-km",
    "UPC Lane Length(in km)": "Lane-km",
    "State": "State Name",
    "RO": "RO",
    "PIU": "PIU",
    "NH": "NH No.",
    "AE/IE": "AE/IE Name",
    "Contractor": "Contractor Name",
}


def load_project_master(path):
    """One row per UPC (column 'upc') with the PROJECT_COLUMNS details."""
    raw = read_table_file(path)
    upc_col = next((c for c in raw.columns if str(c).lower() == "upc"), None)
    if upc_col is None:
        raise ValueError(f"No UPC column in {path}")

    missing = [src for src in PROJECT_COLUMNS.values() if src not in raw.columns]
    if missing:
        raise ValueError(f"Project master {path} is missing columns: {', '.join(missing)}")

    info = pd.DataFrame({"upc": raw[upc_col].astype("string")})
    for out_col, src_col in PROJECT_COLUMNS.items():
        info[out_col] = raw[src_col]
    log.info("Project master loaded — %s rows | unique UPCs: %s", f"{len(info):,}", info["upc"].nunique())
    return info


def filter_project_upcs(tables, project_info, enabled):
    """Keep only UPCs present in the project master (all tables), when enabled."""
    if not enabled:
        log.warning("FILTER_BY_PROJECT_MASTER=false: UPCs are NOT filtered against the project master")
        return tables
    valid_upcs = set(project_info["upc"].dropna())
    filtered = {}
    for name, df in tables.items():
        keep = df["upc"].isin(valid_upcs).fillna(False)
        log.info("  %-28s — %8s → %8s  (dropped %s rows with unknown UPC)",
                 name, f"{len(df):,}", f"{int(keep.sum()):,}", f"{int((~keep).sum()):,}")
        filtered[name] = df[keep]
    return filtered
