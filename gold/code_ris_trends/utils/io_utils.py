"""Reading the silver tables (parquet / csv) / reference files and writing CSV outputs."""
import logging
from functools import partial
from pathlib import Path

import pandas as pd

from utils.helpers import to_str_id

log = logging.getLogger(__name__)

SILVER_TABLES = [
    "master_roughness_parent", "master_roughness_child_10m",
    "master_rutting_parent", "master_rutting_child_10m",
    "master_chainage",
]

_READERS = {
    ".parquet": pd.read_parquet,
    ".csv": partial(pd.read_csv, low_memory=False),
    ".xlsx": pd.read_excel,
    ".xls": pd.read_excel,
}


def read_table_file(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        reader = _READERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(f"Unsupported file type '{path.suffix}' for {path}") from None
    return reader(path)


def find_table_file(directory, name):
    """Return <directory>/<name>.<ext> for the first supported extension that exists."""
    for ext in _READERS:
        candidate = Path(directory) / f"{name}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No {name}.(parquet|csv|xlsx) found in {directory}")


def load_silver_tables(input_dir, table_names=SILVER_TABLES):
    tables = {}
    for name in table_names:
        df = read_table_file(find_table_file(input_dir, name))
        # The notebook compared phase/cycle as strings ('2.0' -> '2') before every join
        for col in ("phase", "cycle"):
            if col in df.columns:
                df[col] = to_str_id(df[col])
        tables[name] = df
        log.info("Loaded %-30s rows: %s", name, f"{len(df):,}")
    return tables


def load_contract_upcs(path, enabled=True):
    """Set of UPCs in the contract-module project master, or None when the filter is switched off."""
    if not enabled:
        log.warning("FILTER_BY_PROJECT_MASTER=false: UPCs are NOT filtered against the project master")
        return None
    if path is None:
        raise ValueError("FILTER_BY_PROJECT_MASTER=true requires CM_PROJECT_MASTER_PATH")
    cm_df = read_table_file(path)
    # Accept 'UPC' (Databricks Excel / project_master.xlsx) or 'upc'
    upc_col = next((c for c in cm_df.columns if str(c).lower() == "upc"), None)
    if upc_col is None:
        raise ValueError(f"No UPC column in {path}")
    log.info("cm_df loaded — %s rows × %s cols | unique UPCs: %s",
             f"{cm_df.shape[0]:,}", cm_df.shape[1], cm_df[upc_col].nunique())
    return set(cm_df[upc_col].dropna().unique())


def load_threshold_mapping(input_dir, table_name):
    """Threshold mapping (nhai_dhms.dlm_kpi.dlm_threshold_mapping_display) from the silver folder."""
    try:
        path = find_table_file(input_dir, table_name)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Threshold mapping '{table_name}' (.parquet/.csv/.xlsx) not found in {input_dir}. "
            "Add it there or change THRESHOLD_MAPPING_TABLE in .env."
        ) from None
    df = read_table_file(path)
    log.info("Threshold mapping loaded — %s rows (from %s)", f"{len(df):,}", path.name)
    return df


def save_csv(df, output_dir, name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False)
    log.info("Saved %s — %s rows", path, f"{len(df):,}")
    return path
