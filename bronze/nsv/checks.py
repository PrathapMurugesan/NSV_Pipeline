"""
checks.py - checks on an extracted sheet.

    check_headers(...)         GATE   - a sheet missing an expected header is rejected (not saved),
                                        because a different layout means we would save the wrong columns.
    empty_sheet_report(...)    REPORT - sheet has no data rows / no values
    defect_columns_report(...) REPORT - defect columns completely empty or more than NULL_LIMIT % empty,
                                        and lanes with no data at all (not surveyed)

REPORT checks never stop anything: the sheet is saved anyway, the findings go to the log and manifest.
(The defect-column and lane logic is the teammate's original check, unchanged in what it detects.)
"""
import csv
import re
from nsv.excel import is_blank
from nsv.log import get_logger

logger = get_logger("nsv.checks")

LANE_PATTERN = re.compile(r"(?<![a-z0-9])([lr])\s*-?\s*([1-4])(?![a-z0-9])")      # 'L3', 'R-2', 'Lane L1'


# =============================================================================
# Expected headers: schemas/<deliverable>/<workbook>/<sheet>.csv
#   columns: header (required), excel_column + value_type (for the position match, see nsv/headers.py),
#            aliases (other accepted names, separated by |)
# =============================================================================
def load_schema(schema_file):
    """
    (headers, {header: [aliases]}, {header: {"excel_column", "value_type"}}),
    or None when the schema file does not exist yet.
    """
    if not schema_file.is_file():
        return None
    headers, aliases, info = [], {}, {}
    with open(schema_file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            header = (row.get("header") or "").strip()
            if not header:
                continue
            headers.append(header)
            names = [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
            if names:
                aliases[header] = names
            info[header] = {"excel_column": (row.get("excel_column") or "").strip(),
                            "value_type": (row.get("value_type") or "").strip()}
    return headers, aliases, info


# =============================================================================
# GATE: headers  (the matching itself is in nsv/headers.py)
# =============================================================================
def check_headers(result, label, gate=True):
    """
    result: from headers.match_headers.  Logs every non-exact match, hints for missing headers, extras.
    gate=True: a missing header rejects the sheet.
    Returns {"ok", "missing", "extra", "matched_not_exactly", "unit_differs"} for the manifest / report.
    """
    cols = result["columns"]
    loose = [c for c in cols if c["match"] in ("normalized", "unit", "alias")]
    by_position = [c for c in cols if c["match"] == "position"]
    units = [c for c in cols if c["unit_differs"]]
    extra = [c["vendor_header"] for c in cols if c["schema_header"] is None]

    for c in loose:
        unit = (f" - unit in file: {c['unit_in_file'] or 'none'}, in schema: {c['unit_in_schema'] or 'none'}"
                if c["unit_differs"] else "")
        logger.warning(f"[{label}] header '{c['vendor_header']}' accepted as '{c['schema_header']}' ({c['match']}){unit}")
    for c in by_position:
        kind = (f"values: {c['values_in_file']}, same as the template" if "empty" not in
                (c["values_in_file"], c["values_in_schema"]) else "values could not be compared (column empty)")
        logger.warning(f"[{label}] column {c['excel_column']}: expected '{c['schema_header']}', got "
                       f"'{c['vendor_header']}' - accepted (same place in the sheet, {kind})")
    for h in result["missing"]:
        hint = f" -> found '{result['hints'][h]}' instead? (add it as an alias if it is the same column)" \
            if h in result["hints"] else ""
        logger.error(f"[{label}] missing header '{h}'{hint}")
    if extra:
        logger.warning(f"[{label}] {len(extra)} column(s) not in the schema: {extra[:10]}")

    ok = not result["missing"] or not gate
    if result["missing"] and gate:
        logger.error(f"[{label}] REJECTED: {len(result['missing'])} expected header(s) missing")
    elif not result["missing"]:
        not_exact = len(loose) + len(by_position)
        logger.info(f"[{label}] headers OK: {len(cols) - len(extra)} matched"
                    f"{f' ({not_exact} not exactly - see warnings)' if not_exact else ''}")
    return {"ok": ok, "missing": result["missing"], "extra": extra,
            "matched_not_exactly": {c["vendor_header"]: f"{c['schema_header']} ({c['match']})" for c in loose},
            "different_headers": {c["schema_header"]: c["vendor_header"] for c in by_position},   # expected: got
            "unit_differs": {c["schema_header"]: f"file: {c['unit_in_file'] or 'none'}, schema: {c['unit_in_schema'] or 'none'}"
                             for c in units}}


# =============================================================================
# REPORT: empty sheet
# =============================================================================
def empty_sheet_report(df, label):
    data_cols = [c for c in df.columns if c != "source_row"]
    has_values = len(df) > 0 and any(not is_blank(v) for c in data_cols for v in df[c])
    if not has_values:
        logger.warning(f"[{label}] sheet has no data rows (headers only) - saved anyway")
    return {"has_data": has_values, "rows": len(df)}


# =============================================================================
# REPORT: defect columns + lanes (D2, D4)
# =============================================================================
def _is_real_value(v):
    """Filled and not just 0 (calculated columns of an unsurveyed lane are filled with 0)."""
    return not is_blank(v) and str(v).strip() not in ("0", "0.0")


def defect_columns_report(df, keywords, null_limit, ignore, label):
    """
    Defect column = name contains a keyword (lane, roughness, crack, chainage, ...), and no `ignore` word.
    - A lane with no real value in ANY of its defect columns = not surveyed (e.g. L3/L4 on a 4-lane road):
      reported, and its columns are left out of the empty-column report.
    - Every other defect column that is completely empty or over null_limit % empty is reported.
    """
    data_cols = [c for c in df.columns if c != "source_row"]
    defect_cols = [c for c in data_cols
                   if not any(w in c.lower() for w in ignore) and any(k in c.lower() for k in keywords)]

    # ---- lanes not surveyed ----
    by_lane = {}
    for col in defect_cols:
        tokens = LANE_PATTERN.findall(col.lower())
        if tokens and len(set(tokens)) <= 2:                     # 'Lane (L1,L2,L3,L4)' is not about one lane
            lane = tokens[-1][0].upper() + tokens[-1][1]
            by_lane.setdefault(lane, []).append(col)
    not_surveyed = {lane: cols for lane, cols in by_lane.items()
                    if not any(_is_real_value(v) for c in cols for v in df[c])}
    for lane, cols in sorted(not_surveyed.items()):
        logger.info(f"[{label}] lane {lane}: no data in any of its {len(cols)} defect columns (not surveyed)")
    skip = {c for cols in not_surveyed.values() for c in cols}

    # ---- empty / sparse defect columns ----
    empty, sparse = [], []
    checked = [c for c in defect_cols if c not in skip]
    for col in checked:
        pct = 100 * sum(is_blank(v) for v in df[col]) / len(df) if len(df) else 100.0
        if pct >= 100:
            empty.append(col)
        elif pct > null_limit:
            sparse.append({"column": col, "empty_pct": round(pct, 1)})

    for col in empty:
        logger.warning(f"[{label}] defect column completely empty: '{col}'")
    for item in sparse:
        logger.warning(f"[{label}] defect column {item['empty_pct']}% empty (limit {null_limit}%): '{item['column']}'")
    if not empty and not sparse:
        logger.info(f"[{label}] defect columns OK: {len(checked)} checked, all within {null_limit}% empty")

    return {
        "defect_columns_checked": len(checked),
        "lanes_not_surveyed": sorted(not_surveyed),
        "completely_empty": empty,
        "over_limit": sparse,
        "null_limit_pct": null_limit,
    }
