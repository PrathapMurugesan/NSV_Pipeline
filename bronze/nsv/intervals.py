"""
intervals.py - master table of reporting intervals: one row per UPC, built from what d1 / d2 already saved
(nothing is extracted again).

    Zone, Project_Name, Phase, Cycle, UPC_code,
    Rutting_Interval_IRC,   Rutting_Interval_CA,     Roughness_Interval_IRC, Roughness_Interval_CA,
    Ravelling_Interval_IRC, Ravelling_Interval_CA,   Cracking_Interval_IRC,  Cracking_Interval_CA,
    Interval_Source, Updated_At                      (intervals in metres)

Where the intervals come from, in this order:
    1. d2 sheet 'Inputs (Thresholds)' (saved cell by cell): block 'Contract Agreement' -> _CA, block 'IRC' -> _IRC,
       column 'Reporting Interval (m)' of the Current Cycle. Flexible pavement first, rigid if flexible is empty.
    2. a value still empty: the most common value of 'Current Cycle | <parameter> | Reporting Interval' in the
       comparison tables (comparison_ca -> _CA, comparison_irc -> _IRC).
    Ravelling is filled only when a file has it (today's templates have Roughness, Rutting, Cracking).
Project_Name: d1 visual condition column 'Project Name', else the d2 comparison workbook's file name.

Saved as  master/reporting_intervals.parquet + .csv  in the output (settings.MASTER_FOLDER).
Each update adds its UPCs; a UPC already in the table (same zone / phase / cycle / UPC) is replaced by the new row.

    python run_intervals.py                 update the table from everything already extracted
"""
import io
import json
import re
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import settings
from nsv.log import get_logger
from nsv.writer import csv_bytes

logger = get_logger(__name__)

PARAMETERS = {"Rutting": "rut", "Roughness": "rough", "Ravelling": "ravel", "Cracking": "crack"}   # name: keyword
KEYS = ["Zone", "Phase", "Cycle", "UPC_code"]
INTERVALS = [f"{p}_Interval_{b}" for p in PARAMETERS for b in ("IRC", "CA")]
COLUMNS = ["Zone", "Project_Name", "Phase", "Cycle", "UPC_code", *INTERVALS, "Interval_Source", "Updated_At"]
CELL = re.compile(r"^([A-Z]+)(\d+)$")
MONTH_PREFIX = re.compile(r"^[A-Za-z]{3}-\d{2}\s+")          # 'Sep-26 Comparison ...' -> 'Comparison ...'


# =============================================================================
# small helpers
# =============================================================================
def _parameter(text):
    """'Rut Depth', 'Rutting' -> 'Rutting' ... None for anything else."""
    text = str(text).lower()
    return next((name for name, word in PARAMETERS.items() if word in text), None)


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _latest_files(storage, bronze_folder, deliverable):
    """Files of the newest run of one deliverable ('' list when it never ran)."""
    try:
        latest = json.loads(storage.read_bytes(f"{bronze_folder}/{deliverable}/{deliverable}_latest.json"))
    except Exception:
        return []
    return storage.list_files(latest["folder"])


def _read_parquet(storage, path):
    return pd.read_parquet(io.BytesIO(storage.read_bytes(path)))


# =============================================================================
# 1. intervals from the 'Inputs (Thresholds)' sheet
# =============================================================================
def from_thresholds(cells):
    """{(parameter, 'CA' / 'IRC'): interval} from the sheet's saved cells."""
    grid = {}
    for c in cells.get("cells", []):
        m = CELL.match(str(c.get("cell", "")))
        if m:
            grid.setdefault(int(m[2]), {})[m[1]] = c.get("value")
    found, block, column = {}, None, None
    for row in sorted(grid):
        texts = {col: str(v).strip().lower() for col, v in grid[row].items()}
        if "contract agreement" in texts.values() or "irc" in texts.values():     # header row of a block
            block = "CA" if "contract agreement" in texts.values() else "IRC"
            interval_cols = sorted((c for c, t in texts.items() if t.startswith("reporting interval")),
                                   key=lambda c: (len(c), c))
            column = interval_cols[0] if interval_cols else None                   # first one = current cycle
            continue
        parameter = _parameter(texts.get("A", ""))
        value = _number(grid[row].get(column)) if column else None
        if parameter and block and value:
            found.setdefault((parameter, block), value)        # first block wins: flexible before rigid
    return found


# =============================================================================
# 2. intervals from the comparison tables (when the sheet is missing / empty)
# =============================================================================
def from_tables(storage, files):
    found = {}
    for block, table in (("CA", "comparison_ca"), ("IRC", "comparison_irc")):
        path = next((f for f in files if re.search(rf"_{table}(__[^/]*)?\.parquet$", f)), None)
        if not path:
            continue
        df = _read_parquet(storage, path)
        for column in df.columns:
            low = column.lower()
            parameter = _parameter(low.split("|")[1]) if low.count("|") >= 2 else None
            if parameter and low.startswith("current cycle") and "reporting interval" in low:
                values = pd.to_numeric(df[column], errors="coerce").dropna()
                if len(values):
                    found.setdefault((parameter, block), float(values.mode()[0]))
    return found


# =============================================================================
# project name
# =============================================================================
def project_name(storage, d1_files, d2_files):
    path = next((f for f in d1_files if f.endswith("visual_condition_visual_condition.parquet")), None)
    if path:
        df = _read_parquet(storage, path)
        column = next((c for c in df.columns if re.sub(r"[^a-z]", "", c.lower()) == "projectname"), None)
        if column:
            names = df[column].dropna().astype(str).str.strip()
            names = names[names != ""]
            if len(names):
                return names.mode()[0]
    manifest = next((f for f in d2_files if f.endswith("d2_manifest.json")), None)    # fallback: workbook name
    if manifest:
        workbooks = json.loads(storage.read_bytes(manifest)).get("workbooks", {})
        name = next((wb.get("file") for wb in workbooks.values() if wb.get("file")), None)
        if name:
            return MONTH_PREFIX.sub("", name.rsplit(".", 1)[0]).strip()
    return None


# =============================================================================
# one row per survey
# =============================================================================
def interval_row(storage, survey):
    """The master row of one survey (zone, phase, cycle, upc_folder), None when d2 was never extracted."""
    zone, phase, cycle, upc_folder = survey
    bronze = settings.BRONZE_LAYOUT.format(zone=zone, phase=phase, cycle=cycle, upc=upc_folder, deliverable="")
    bronze = bronze.rstrip("/")
    d2_files = _latest_files(storage, bronze, "d2")
    if not d2_files:
        return None
    found, sources = {}, []
    cells_path = next((f for f in d2_files if f.endswith("inputs_thresholds_cells.json")), None)
    if cells_path:
        found = from_thresholds(json.loads(storage.read_bytes(cells_path)))
        if found:
            sources.append("Inputs (Thresholds)")
    if len(found) < len(INTERVALS):
        extra = {k: v for k, v in from_tables(storage, d2_files).items() if k not in found}
        if extra:
            found.update(extra)
            sources.append("comparison tables")
    row = {"Zone": zone, "Phase": phase, "Cycle": cycle, "UPC_code": upc_folder.replace("-", "/"),
           "Project_Name": project_name(storage, _latest_files(storage, bronze, "d1"), d2_files),
           "Interval_Source": " + ".join(sources) or "not found",
           "Updated_At": datetime.now().isoformat(timespec="seconds")}
    for parameter in PARAMETERS:
        for block in ("IRC", "CA"):
            row[f"{parameter}_Interval_{block}"] = found.get((parameter, block))
    return row


def extracted_surveys(storage):
    """Every (zone, phase, cycle, upc_folder) that has a d2 extraction in the output."""
    pattern = re.compile(r"zone=([^/]+)/phase=([^/]+)/cycle=([^/]+)/([^/]+)/d2/d2_latest\.json$")
    return sorted({m.groups() for f in storage.list_files("bronze/nsv") if (m := pattern.search(f))})


# =============================================================================
# the master table: read, add / replace rows, write
# =============================================================================
def _master_path(extension):
    return f"{settings.MASTER_FOLDER}/reporting_intervals.{extension}"


def read_master(storage):
    try:
        return _read_parquet(storage, _master_path("parquet"))
    except Exception:
        return pd.DataFrame(columns=COLUMNS)


def write_master(storage, df):
    df = df.reindex(columns=COLUMNS)
    for column in INTERVALS:                                  # whole metres, empty stays empty
        df[column] = pd.to_numeric(df[column], errors="coerce").round().astype("Int64")
    schema = pa.schema([pa.field(c, pa.int64() if c in INTERVALS else pa.string()) for c in COLUMNS])
    text = {c: df[c].astype("string").where(df[c].notna(), None) for c in COLUMNS if c not in INTERVALS}
    table = pa.Table.from_pandas(df.assign(**text), schema=schema, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    storage.write_bytes(_master_path("parquet"), buffer.getvalue())
    storage.write_bytes(_master_path("csv"), csv_bytes(df.astype(object).where(df.notna(), None)))  # empty, not <NA>


def update_master(surveys=None, storage=None):
    """Add / replace the rows of these surveys (default: every extracted survey). Returns the new rows."""
    storage = storage or settings.OUTPUT
    surveys = extracted_surveys(storage) if surveys is None else surveys
    rows = []
    for survey in surveys:
        try:
            row = interval_row(storage, survey)
        except Exception as e:
            logger.warning(f"[intervals] {'/'.join(survey)}: could not read the intervals - {e}")
            continue
        if row is None:
            logger.info(f"[intervals] {'/'.join(survey)}: no d2 extraction - no row")
            continue
        rows.append(row)
        values = ", ".join(f"{c.replace('_Interval', '')}={row[c]:g}" for c in INTERVALS if row[c] is not None)
        logger.info(f"[intervals] {'/'.join(survey)}: {values or 'no intervals found'} ({row['Interval_Source']})")
    if not rows:
        return []
    new = pd.DataFrame(rows, columns=COLUMNS)
    old = read_master(storage)
    if len(old):
        replaced = old.set_index(KEYS).index.isin(new.set_index(KEYS).index)
        new = pd.concat([old[~replaced], new], ignore_index=True)
    master = new.sort_values(KEYS, ignore_index=True)
    write_master(storage, master)
    logger.info(f"[intervals] master table: {len(master)} UPC(s), {len(rows)} added / updated -> "
                f"{storage.describe()}/{_master_path('parquet')} (+ .csv)")
    return rows
