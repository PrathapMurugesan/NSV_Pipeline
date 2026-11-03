"""Reading the silver tables / reference files and writing CSV outputs."""
import logging
from functools import partial
from pathlib import Path

import pandas as pd

from utils.helpers import to_date, to_str_id

log = logging.getLogger(__name__)

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


def _normalize_keys(df):
    # Compare phase/cycle as strings ('2.0' -> '2'), upc as text and detected_on as a date
    # (CSV sources give text dates) across all sources
    for col in ("phase", "cycle"):
        if col in df.columns:
            df[col] = to_str_id(df[col])
    if "upc" in df.columns:
        df["upc"] = df["upc"].astype("string")
    if "detected_on" in df.columns:
        df["detected_on"] = to_date(df["detected_on"])
    return df


def load_table(input_dir, name):
    path = find_table_file(input_dir, name)
    df = _normalize_keys(read_table_file(path))
    log.info("Loaded %-35s rows: %s", path.name, f"{len(df):,}")
    return df


def load_intermediate(path):
    """final_intermediate.csv (Databricks: <OUTPUT_SCHEMA>.final_intermediate)."""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Intermediate table not found: {path}. Run code_intermediate_logic/main.py first "
            "or change INTERMEDIATE_PATH in .env."
        )
    df = _normalize_keys(read_table_file(path))
    df["defect_detected_on"] = to_date(df["defect_detected_on"])
    log.info("final_intermediate loaded — %s rows (from %s)", f"{len(df):,}", Path(path).name)
    return df


def save_csv(df, output_dir, name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False)
    log.info("Saved %s — %s rows", path, f"{len(df):,}")
    return path
