"""
profiler.py - describe what each column REALLY contains (profile.json). Nothing is changed.

Per sheet:   rows, columns, header rows, completely empty columns, formula-only rows
Per column:  excel_column, filled / empty counts, value types found (number / text / date / bool),
             numbers or dates stored as text, min / max, distinct values, examples,
             text in brackets in the header (e.g. 'km', 'in mm/km', 'dd-mm-yyyy') as a unit/format hint,
             a snake_case name suggestion for later layers.

'formula_only_rows': rows where every column WITHOUT formulas is empty, but formula columns show a
value - e.g. an unfilled template row whose formulas still display defaults (PCI = 100).
"""
import re
from datetime import date, datetime, time

import settings
from nsv.excel import is_blank
from nsv.matching import safe_name

NUMBER_TEXT = re.compile(r"^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$")
DATE_TEXT = re.compile(r"^\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$")


def _kind(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (datetime, date, time)):      # a time of day ('10:30') counts as a date / time
        return "date"
    return "text"


def profile_column(name, values, letter=None, has_formula=False, vendor_header=None):
    filled = [v for v in values if not is_blank(v)]
    kinds = {"number": 0, "text": 0, "date": 0, "bool": 0}
    numbers_as_text = dates_as_text = 0
    numbers, dates = [], []
    distinct, distinct_capped = set(), False
    examples = []

    for v in filled:
        kind = _kind(v)
        kinds[kind] += 1
        if kind == "number":
            numbers.append(v)
        elif kind == "date":
            dates.append(v)
        elif kind == "text":
            if NUMBER_TEXT.match(v):
                numbers_as_text += 1
            elif DATE_TEXT.match(v):
                dates_as_text += 1
        if not distinct_capped:
            key = str(v)
            if key not in distinct:
                distinct.add(key)
                if len(examples) < settings.PROFILE_EXAMPLES:
                    examples.append(v)
            distinct_capped = len(distinct) >= settings.PROFILE_MAX_DISTINCT

    profile = {
        "excel_column": letter,
        "suggested_name": safe_name(name),
        "vendor_header": vendor_header or name,
        "header_hint": re.findall(r"[\(\[]([^\)\]]+)[\)\]]", vendor_header or name),   # () or [] in the vendor header
        "has_formula": has_formula,
        "filled": len(filled),
        "empty": len(values) - len(filled),
        "empty_pct": round(100 * (len(values) - len(filled)) / len(values), 1) if values else 0.0,
        "types": {k: n for k, n in kinds.items() if n},
        "numbers_stored_as_text": numbers_as_text,
        "dates_stored_as_text": dates_as_text,
        "distinct": f">={settings.PROFILE_MAX_DISTINCT}" if distinct_capped else len(distinct),
        "examples": examples,
    }
    if numbers:
        profile["min"], profile["max"] = min(numbers), max(numbers)
    if dates:
        try:
            profile["first_date"], profile["last_date"] = min(dates), max(dates)
        except TypeError:                              # dates and times of day mixed: compare as text
            profile["first_date"], profile["last_date"] = min(map(str, dates)), max(map(str, dates))
    return profile


def profile_table(df, columns=None, formula_columns=(), header_excel_rows=None):
    """
    df:              the extracted table (with 'source_row' first)
    columns:         [{"name", "letter"}] - Excel letters, when the table came from Excel
    formula_columns: names of columns that contain formulas (from formulas.py)
    """
    letters = {c["name"]: c.get("letter") for c in (columns or [])}
    vendor = {c["name"]: c.get("vendor_header") for c in (columns or [])}
    data_cols = [c for c in df.columns if c != "source_row"]
    formula_columns = set(formula_columns)

    col_profiles = {c: profile_column(c, df[c].tolist(), letters.get(c), c in formula_columns, vendor.get(c))
                    for c in data_cols}

    # rows where only formula columns show something (unfilled template rows)
    formula_only = 0
    input_cols = [c for c in data_cols if c not in formula_columns]
    if formula_columns and input_cols:
        inputs_empty = df[input_cols].apply(lambda r: all(is_blank(v) for v in r), axis=1)
        formulas_filled = df[list(formula_columns)].apply(lambda r: any(not is_blank(v) for v in r), axis=1)
        formula_only = int((inputs_empty & formulas_filled).sum())

    return {
        "rows": len(df),
        "columns": len(data_cols),
        "header_excel_rows": header_excel_rows,
        "first_data_row": int(df["source_row"].min()) if len(df) else None,
        "last_data_row": int(df["source_row"].max()) if len(df) else None,
        "completely_empty_columns": [c for c, p in col_profiles.items() if p["filled"] == 0],
        "formula_only_rows": formula_only,
        "column_profiles": col_profiles,
    }
