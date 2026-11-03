"""
report.py - one simple table for a whole batch: one row per sheet / csv file, with status and reason.

Saved next to the batch summary as  bronze/nsv/_batches/batch=<timestamp>_report.csv  (open in Excel).

    status            meaning
    saved             extracted and saved                      (notes may still say: no data, empty columns ...)
    saved_headers_differ  saved, but some headers differ from the schema: the column in the same place holds
                      the same kind of values, so it was accepted (reason: expected '...', got '...')
    saved_headers_missing  saved although schema headers are missing (settings.CHECK_HEADERS off) - the
                      columns keep the vendor's headers; reason lists the missing ones
    saved_not_in_config    a sheet / Excel file that is not in the deliverable config, saved as found
    not_readable_not_in_config  a sheet not in the config that is not a table (empty, chart) - not saved
    split_into_variants   several sheets / files fit one configured sheet / workbook ('Rigid - Comparison as
                      per CA' + 'Flexible - ...', 'x.xlsm' + 'x (1).xlsm'): each extracted with its settings
    optional_missing  an optional sheet / folder (e.g. a CRRI road class) was not delivered - not an error
    saved_cells       report sheet saved cell by cell
    skipped_by_config deliberately not extracted (skip: True)
    REJECTED_HEADERS  headers differ from the schema -> NOT saved (reason lists the missing headers)
    SHEET_MISSING     expected sheet not found in the workbook
    WORKBOOK_MISSING  expected workbook not found in the folder
    READ_ERROR        the file / sheet could not be read
    CRASHED           the run stopped with an error (traceback in the console / run log)
    not_delivered     the survey has no folder for this deliverable
    not_extracted     workbook was found, but the deliverable stopped because another workbook is missing
    unexpected_file   a file in the folder that is not expected - ignored
    images_saved      an image folder was read: one row per image in d4_images_<group> (notes: status counts)
    IMAGES_MISSING    a REQUIRED image folder was not delivered
Upper-case statuses are the problems to look at.
"""
import settings
from nsv.headers import split_unit

PROBLEMS = {"REJECTED_HEADERS", "SHEET_MISSING", "WORKBOOK_MISSING", "READ_ERROR", "CRASHED", "IMAGES_MISSING"}
COLUMNS = ["zone", "phase", "cycle", "upc", "deliverable", "workbook", "file", "sheet", "interval_m", "period",
           "status", "rows", "reason", "notes", "run_folder"]   # interval_m / period: read from the sheet name

_STATUS = {"rejected_headers": "REJECTED_HEADERS", "missing": "SHEET_MISSING", "read_error": "READ_ERROR"}


def _notes(result):
    """Things worth knowing about a SAVED sheet (it was saved anyway)."""
    notes = []
    if result.get("empty_check", {}).get("has_data") is False:
        notes.append("no data rows")
    if result.get("formula_only_rows"):
        notes.append(f"{result['formula_only_rows']} formula-only rows (inputs empty)")
    defect = result.get("defect_check") or {}
    if defect.get("completely_empty"):
        notes.append(f"{len(defect['completely_empty'])} defect column(s) completely empty")
    if defect.get("over_limit"):
        notes.append(f"{len(defect['over_limit'])} defect column(s) over {defect['null_limit_pct']}% empty")
    check = result.get("header_check")
    if isinstance(check, dict):
        if check.get("matched_not_exactly"):
            notes.append(f"{len(check['matched_not_exactly'])} header(s) accepted not exactly (case/spaces/unit/alias)")
        if check.get("unit_differs"):
            units = "; ".join(f"{split_unit(h)[0]}: {u}" for h, u in list(check["unit_differs"].items())[:3])
            notes.append(f"unit differs from schema - {units}")
        if check.get("extra"):
            notes.append(f"{len(check['extra'])} extra column(s)")
    if check == "no schema file":
        notes.append("headers not checked (no schema file)")
    return "; ".join(notes)


def report_rows(survey, deliverable, status, manifest=None, error=None):
    """All report rows for one survey x deliverable."""
    base = {"zone": survey.zone, "phase": survey.phase, "cycle": survey.cycle, "upc": survey.upc,
            "deliverable": deliverable, "workbook": "", "file": "", "sheet": "", "rows": "", "reason": "",
            "notes": "", "run_folder": (manifest or {}).get("output", "")}
    if status == "not_delivered":
        return [{**base, "status": "not_delivered"}]
    if status == "crashed":
        return [{**base, "status": "CRASHED", "reason": error}]

    rows = []
    # ---- Excel deliverables: one row per configured sheet ----
    for wb_key, wb in manifest.get("workbooks", {}).items():
        wb_base = {**base, "workbook": wb_key, "file": wb.get("file") or ""}
        if wb.get("status") == "missing":
            rows.append({**wb_base, "status": "WORKBOOK_MISSING", "reason": "expected workbook not found in the folder"
                         + (f" - {wb['closest']}" if wb.get("closest") else "")})
            continue
        if wb.get("status") == "split_into_variants":
            copies = wb.get("identical_copies")
            rows.append({**wb_base, "status": "split_into_variants",
                         "reason": f"several files fit, each extracted on its own: {wb.get('variants')}"
                                   + (f" (identical copies not extracted again: {copies})" if copies else "")})
            continue
        if wb.get("identified_by") and wb["identified_by"] != "file name":
            wb_base["notes"] = f"file recognised by content: {wb['identified_by']}"
        if wb.get("variant_of"):
            wb_base["notes"] = f"one of several files for '{wb['variant_of']}'"
        if wb.get("status") == "found" and not wb.get("sheets"):
            rows.append({**wb_base, "status": "not_extracted",
                         "reason": "deliverable stopped: another expected workbook is missing"})
            continue
        if wb.get("status") == "read_error":
            rows.append({**wb_base, "status": "READ_ERROR", "reason": wb.get("error")})
            continue
        for sheet_key, result in wb.get("sheets", {}).items():
            status = _STATUS.get(result.get("status"), result.get("status"))
            reason = ""
            if status == "REJECTED_HEADERS":
                missing = result["header_check"]["missing"]
                reason = f"{len(missing)} header(s) missing: {missing[:5]}"
            elif status == "SHEET_MISSING":
                reason = f"no sheet like {result.get('expected_names')}" + \
                         (f" - {result['closest']}" + (" (saved as not in config)" if settings.EXTRACT_UNMATCHED
                                                       else "") if result.get("closest") else "")
            elif status == "optional_missing":
                reason = f"optional sheet not in the file (like {result.get('expected_names')})"
            elif status == "split_into_variants":
                reason = f"several sheets like it, each extracted with its settings: {result.get('variants')}"
            elif status in ("READ_ERROR", "not_readable_not_in_config"):
                reason = result.get("error")
            check = result.get("header_check")
            differ = check.get("different_headers") if isinstance(check, dict) else None
            if status == "saved" and result.get("not_in_config"):
                status = "saved_not_in_config"
                reason = "sheet / file not in the deliverable config - saved as found (header found automatically)"
            elif status == "saved" and isinstance(check, dict) and check.get("missing"):
                status = "saved_headers_missing"
                reason = (f"{len(check['missing'])} schema header(s) not in the file, saved with the vendor's "
                          f"headers: {check['missing'][:5]}")
            elif status == "saved" and differ:
                status = "saved_headers_differ"
                reason = "; ".join(f"expected '{e}', got '{g}'" for e, g in list(differ.items())[:3]) + \
                         (f" (+{len(differ) - 3} more, see schema.json)" if len(differ) > 3 else "")
            rows.append({**wb_base, "sheet": result.get("sheet_name") or sheet_key, "status": status,
                         "interval_m": result.get("interval_m", ""), "period": result.get("period", ""),
                         "rows": result.get("rows", ""), "reason": reason,
                         "notes": "; ".join(n for n in (wb_base["notes"],
                                                        f"one of several sheets for '{result['variant_of']}'"
                                                        if result.get("variant_of") else "",
                                                        _notes(result) if status.startswith("saved") else "") if n)})

    # ---- csv deliverable (D3): one row per file ----
    for relative, entry in manifest.get("csv_files", {}).items():
        status = _STATUS.get(entry.get("status"), entry.get("status"))
        rows.append({**base, "workbook": entry.get("group"), "file": relative, "status": status,
                     "rows": entry.get("rows", ""), "reason": entry.get("error", ""), "notes": _notes(entry)})

    # ---- image folders (D4) ----
    for group, entry in manifest.get("image_folders", {}).items():
        if entry.get("status") == "missing":
            rows.append({**base, "workbook": "images", "sheet": group,
                         "status": "IMAGES_MISSING" if entry.get("required") else "optional_missing",
                         "reason": "no image folder for this group in the deliverable folder"})
            continue
        notes = [f"{n} {st}" for st, n in entry.get("image_status", {}).items()]
        if entry.get("images_with_issues"):
            notes.append(f"{entry['images_with_issues']} with issues (see 'issues' column)")
        if not entry.get("ocr"):
            notes.append("OCR not available - text on images not read")
        rows.append({**base, "workbook": "images", "file": ", ".join(entry.get("folders", [])), "sheet": group,
                     "status": "images_saved", "rows": entry.get("images", ""), "notes": "; ".join(notes)})

    # ---- files in the folder that were not expected ----
    for name in manifest.get("unexpected_files", []):
        rows.append({**base, "file": name, "status": "unexpected_file", "reason": "not in the deliverable config - ignored"})
    return rows
