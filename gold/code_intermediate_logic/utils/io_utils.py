"""Reading the silver tables and writing CSV outputs."""
import logging
from functools import partial
from pathlib import Path

import pandas as pd

from utils.helpers import to_str_id

log = logging.getLogger(__name__)

_READERS = {
    ".parquet": pd.read_parquet,
    ".csv": partial(pd.read_csv, low_memory=False),
    ".xlsx": pd.read_excel,
    ".xls": pd.read_excel,
}


def read_table_file(path):
    path = Path(path)
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


def load_table(input_dir, name):
    path = find_table_file(input_dir, name)
    df = read_table_file(path)
    # The notebook joined on phase/cycle; compare them as strings ('2.0' -> '2') across sources
    for col in ("phase", "cycle"):
        if col in df.columns:
            df[col] = to_str_id(df[col])
    if "upc" in df.columns:
        df["upc"] = df["upc"].astype("string")
    log.info("Loaded %-30s rows: %s", path.name, f"{len(df):,}")
    return df


def save_csv(df, output_dir, name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False)
    log.info("Saved %s — %s rows", path, f"{len(df):,}")
    return path
