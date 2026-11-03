"""
pipeline.py - the steps a deliverable script calls, in order:

    files = find_workbooks(run, "d2", WORKBOOKS)            1. which expected workbooks are in the folder
    extract_workbook(run, files, "comparison", WORKBOOKS)   2. per workbook: sheets -> read -> check -> save
    extract_csv_folder(run, "d3", GROUPS)                   (D3: csv files instead of workbooks)
    extract_everything(run, "d2")                           (settings.EXTRACT_ALL: no config at all)

What happens to each sheet of a workbook (extract_workbook):
    a. find it by name (exact or fuzzy)                     missing          -> logged, manifest
    b. read it as a table (header found, merges handled)    unreadable       -> logged, manifest
    c. header check against schemas/...csv  (the GATE)      headers missing  -> REJECTED, not saved
                                                            (settings.CHECK_HEADERS off: saved, vendor headers)
    sheets / Excel files not in the config: saved as found (settings.EXTRACT_UNMATCHED, extract_unmatched_files)
    d. formulas -> formulas.json, legends -> data_dictionary.json
    e. profile -> profile.json
    f. reports: empty sheet, defect columns, lanes (never stop anything)
    g. save the table as parquet + csv (all text, exactly as delivered), named d2_<workbook>_<sheet>

Sheet options in WORKBOOKS (deliverables/dN.py):
    sheet_names   known names of the sheet (required)
    kind          "table" (default) or "cells" (report-style sheet saved cell by cell as JSON)
    skip          True = known sheet, deliberately not extracted
    header_row, header_rows, info_rows, skip_rows, columns    -> see nsv/excel.py
    cells         also save this column range cell by cell, e.g. "I:M" (side tables next to the main table)
"""
import re
from fnmatch import fnmatch
from pathlib import PurePosixPath

import settings
from nsv.checks import check_headers, defect_columns_report, empty_sheet_report, load_schema
from nsv.excel import ExcelWorkbook
from nsv.headers import final_names, match_headers
from nsv.formulas import describe_formulas
from nsv.log import get_logger
from nsv.matching import classify_path, identify_names, match_score, name_tags, normalize_name, safe_name
from nsv.profiler import profile_table

logger = get_logger("nsv.pipeline")

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")


# =============================================================================
# 1. Which expected workbooks are in the folder?
# =============================================================================
def find_workbooks(run, folder, workbooks):
    """
    Returns {workbook key: file path} for the workbooks that were found,
    or None when one is missing and settings.STOP_IF_WORKBOOK_MISSING is on.
    Files directly in the folder come first; files in sub-folders ('d4/Excel/Defect Report.xlsx') are used
    for workbooks not found at the top. Excel lock files (~$) are ignored.

    1. by file name (exact / normalized / contained / fuzzy, see matching.py)
    2. by keywords in the name + sheets inside: config "file_keywords" + "identify_by_sheets"
       (e.g. D2: the name changes per road and month, but always contains 'comparison')
    3. by the sheets inside only: the one left-over file that has all the configured sheets
       (a renamed 'NSV Cracks_DHMS.xlsx' still has its 'Cracks' sheet)
    4. by keywords only: the ONE left-over file whose name has all "file_keywords", even if some sheets
       differ - those sheets then show as SHEET_MISSING in the summary instead of the whole workbook missing
    """
    top, deeper = {}, {}
    for p in _excel_files(folder):
        relative = PurePosixPath(p).relative_to(folder.strip("/")).as_posix()   # 'x.xlsx' or 'Excel/x.xlsx'
        (top if "/" not in relative else deeper)[relative] = p
    paths = {**top, **deeper}
    logger.info(f"{len(top)} Excel file(s) in '{settings.INPUT.describe()}/{folder}': {list(top)}"
                + (f", {len(deeper)} in sub-folders: {list(deeper)}" if deeper else ""))

    # ---- 1. by file name: top level first, then sub-folders for what is still missing ----
    expected = {key: cfg["file_names"] for key, cfg in workbooks.items()}
    found, missing, unexpected = identify_names(list(top), expected, settings.FUZZY_THRESHOLD, "workbook",
                                                quiet=True)
    if deeper:
        more, missing, unexpected_deeper = identify_names(list(deeper), {k: expected[k] for k in missing},
                                                          settings.FUZZY_THRESHOLD, "workbook", quiet=True)
        found.update(more)
        unexpected += unexpected_deeper
    how = {key: "file name" for key in found}

    # ---- 2-4. by what is inside the left-over files ----
    sheet_cache, several = {}, {}
    for step in ("keywords_and_sheets", "sheets", "keywords"):
        for key in list(missing):
            names, reason = _find_by_content(key, workbooks[key], step, unexpected, paths, sheet_cache)
            if len(names) == 1:
                found[key], how[key] = names[0], reason
                missing.remove(key)
                unexpected.remove(names[0])
            elif len(names) > 1 and key not in several:
                several[key] = (names, reason)

    # ---- several files fit one workbook (a copy 'x (1).xlsm', two road sections): each is extracted with
    #      that workbook's settings, as '<workbook>__<file name>' (see extract_unmatched_files) ----
    run.workbook_variants = {}
    for key, (names, reason) in several.items():
        if key not in missing or any(n not in unexpected for n in names):
            continue
        logger.warning(f"[{key}] several files fit ({reason}): {names} - each is extracted")
        missing.remove(key)
        for name in names:
            unexpected.remove(name)
            run.workbook_variants[name] = (key, workbooks[key], paths[name])
        how[key] = f"several files: {names}"

    # ---- log what is still missing / unexpected ----
    closest = {}
    for key in missing:
        cfg = workbooks[key]
        scores = sorted(((match_score(n, cfg["file_names"]), n) for n in unexpected), reverse=True)
        closest[key] = f"closest file: '{scores[0][1]}' ({scores[0][0]:.2f})" if scores else "no other Excel file"
        logger.error(f"[{key}] no workbook found (expected like '{cfg['file_names'][0]}'"
                     + (f", or a name containing {cfg['file_keywords']}" if cfg.get("file_keywords") else "")
                     + f") - {closest[key]}")
    for name in unexpected:
        logger.warning(f"workbook not in the config: '{name}'"
                       + (" - every sheet saved as found" if settings.EXTRACT_UNMATCHED else ", ignored"))

    for key in workbooks:
        variants = [n for n, v in run.workbook_variants.items() if v[0] == key]
        status = "found" if key in found else "split_into_variants" if variants else "missing"
        run.manifest["workbooks"][key] = {"file": found.get(key), "status": status, "variants": variants,
                                          "identified_by": how.get(key), "closest": closest.get(key), "sheets": {}}
    run.manifest["unexpected_files"] = unexpected
    run.unmatched_files = {name: paths[name] for name in unexpected}     # -> extract_unmatched_files

    if missing and settings.STOP_IF_WORKBOOK_MISSING:
        logger.error(f"[{run.survey.label}] {run.deliverable} NOT extracted: missing workbook(s) {missing} "
                     f"(settings.STOP_IF_WORKBOOK_MISSING = True)")
        return None
    return {key: paths[name] for key, name in found.items()}


def _find_by_content(key, cfg, step, names, paths, sheet_cache):
    """
    ([files among names that fit this step], reason)  (see find_workbooks)
      keywords_and_sheets  name has all file_keywords + file has all identify_by_sheets
      sheets               file has all identify_by_sheets, or else all configured (not skipped) sheets
      keywords             name has all file_keywords (sheets not checked)
    """
    keywords = [normalize_name(k, is_file=False) for k in cfg.get("file_keywords", [])]
    sheets_wanted = cfg.get("identify_by_sheets") or [s["sheet_names"][0] for s in cfg["sheets"].values()
                                                      if not s.get("skip")]
    if step != "sheets" and not keywords:
        return [], None
    if step == "keywords_and_sheets" and not cfg.get("identify_by_sheets"):
        return [], None

    candidates = [n for n in names if all(k in normalize_name(n) for k in keywords)] if step != "sheets" else names
    accepted = []
    for name in candidates:
        if step == "keywords":
            accepted.append(name)
            continue
        sheets = _sheet_names(key, name, paths, sheet_cache)
        if sheets is None:
            continue
        lacking = [s for s in sheets_wanted
                   if not any(match_score(x, [s], is_file=False) >= settings.FUZZY_THRESHOLD for x in sheets)]
        if not lacking:
            accepted.append(name)
        elif step == "keywords_and_sheets":
            logger.warning(f"[{key}] '{name}' has {cfg['file_keywords']} in its name but not the sheets {lacking} "
                           f"- its sheets: {sheets}")

    reason = {"keywords_and_sheets": f"name contains {cfg.get('file_keywords')} + has sheets {sheets_wanted}",
              "sheets": f"has sheets {sheets_wanted}",
              "keywords": f"name contains {cfg.get('file_keywords')} (sheets not checked)"}[step]
    if len(accepted) == 1:
        logger.warning(f"[{key}] workbook '{accepted[0]}' recognised: {reason} (file name not in config)")
    return accepted, reason


def _sheet_names(key, name, paths, cache):
    """Sheet names of a file (opened once per run of find_workbooks); None if it cannot be opened."""
    if name not in cache:
        try:
            wb = ExcelWorkbook(name, settings.INPUT.read_bytes(paths[name]))
            cache[name] = wb.sheet_names
            wb.close()
        except Exception as e:
            logger.warning(f"[{key}] '{name}' cannot be opened: {e}")
            cache[name] = None
    return cache[name]


def _excel_files(folder):
    """Excel files below the folder, without lock files (~$) and files in settings.IGNORE_FILES."""
    files = []
    for p in settings.INPUT.list_files(folder):
        name = PurePosixPath(p).name
        if PurePosixPath(p).suffix.lower() not in EXCEL_EXTENSIONS or name.startswith("~$"):
            continue
        if any(fnmatch(name.lower(), pattern.lower()) for pattern in settings.IGNORE_FILES):
            logger.info(f"ignored (settings.IGNORE_FILES): '{name}'")
            continue
        files.append(p)
    return files


# =============================================================================
# 2. One workbook: every configured sheet
# =============================================================================
def extract_workbook(run, files, wb_key, workbooks, check_nulls=False):
    """
    files:       {workbook key: file path}   (from find_workbooks)
    wb_key:      which workbook of WORKBOOKS to extract
    check_nulls: also run the defect-column / lane report (D2, D4)
    """
    config = workbooks[wb_key]
    entry = run.manifest["workbooks"].setdefault(wb_key, {"file": None, "status": "missing", "sheets": {}})
    if entry.get("status") == "split_into_variants":
        logger.info(f"[{wb_key}] several files fit - each is extracted on its own: {entry['variants']}")
        return
    if wb_key not in files:
        logger.error(f"[{wb_key}] workbook not found - nothing extracted")
        return

    # ---- open the workbook (from local disk or S3, via settings.INPUT) ----
    path = files[wb_key]
    logger.info(f"---------- [{wb_key}] {path} ----------")
    data = settings.INPUT.read_bytes(path)
    entry.update(_file_facts(path, data))
    try:
        wb = ExcelWorkbook(PurePosixPath(path).name, data)
    except Exception as e:
        logger.error(f"[{wb_key}] cannot open workbook: {e}")
        entry["status"] = "read_error"
        entry["error"] = str(e)
        return
    entry["sheet_names_in_file"] = wb.sheet_names

    # ---- a. which sheet is which (configured, variants, not in the config) ----
    sheet_cfgs = config["sheets"]
    to_read, extras = _resolve_sheets(wb, wb_key, sheet_cfgs, entry)
    if extras and not settings.EXTRACT_UNMATCHED:
        logger.warning(f"[{wb_key}] sheet(s) not in the config, ignored: {extras}")
        extras = []

    # ---- b-g. read, check and save every sheet ----
    meta = {"schema": {}, "profile": {}, "formulas": {}, "data_dictionary": {}}
    schema_wb = config.get("schema_workbook", wb_key)          # a variant file uses its workbook's schemas
    for sheet_key, cfg_key, sheet_name in to_read + [(f"extra_{safe_name(n)}", None, n) for n in extras]:
        label = f"{wb_key}/{sheet_key}"
        result = {"sheet_name": sheet_name, **name_tags(sheet_name)}      # + interval_m / period from the name
        entry["sheets"][sheet_key] = result
        if cfg_key is None:                                   # not in the config: header found automatically
            options, result["not_in_config"] = {}, True
            logger.info(f"[{label}] sheet '{sheet_name}' is not in the config - saved as found")
        else:
            options = {**sheet_cfgs[cfg_key], "schema": f"{schema_wb}/{cfg_key}"}
            if sheet_key != cfg_key:
                result["variant_of"] = cfg_key
        if options.get("skip"):
            logger.info(f"[{label}] skipped (configured skip: True)")
            result["status"] = "skipped_by_config"
            continue
        try:
            if options.get("kind") == "cells":
                _save_cells(run, wb, wb_key, sheet_key, sheet_name, None, result)
                result["status"] = "saved_cells"
            else:
                _extract_sheet(run, wb, wb_key, sheet_key, sheet_name, options, check_nulls, result, meta)
        except Exception as e:
            if cfg_key is None:          # e.g. an empty or chart-only sheet: not a problem for an extra sheet
                logger.warning(f"[{label}] sheet '{sheet_name}' not in the config and not a table: {e}")
                result.update(status="not_readable_not_in_config", error=str(e))
            else:
                logger.error(f"[{label}] could not read sheet '{sheet_name}': {e}")
                result.update(status="read_error", error=str(e))

    # ---- workbook-level JSONs ----
    for name, content in meta.items():
        if content:
            entry.setdefault("meta_files", []).append(run.save_json(content, wb_key, name))
    entry["status"] = "extracted"
    wb.close()


def _resolve_sheets(wb, wb_key, sheet_cfgs, entry):
    """
    Decide which sheet of the file is which configured sheet. Returns (to_read, extras):
      to_read  [(result key, config key, sheet name)]   read with that config key's settings + schema
      extras   [sheet names not in the config]           saved as found (settings.EXTRACT_UNMATCHED)
    Rules, in order:
      1. by name (exact / normalized / contained / fuzzy, '1km' = '1000m' - see matching.py)
      2. several sheets look like ONE configured sheet ('Rigid - Comparison as per CA' + 'Flexible - ...'):
         each is read with that sheet's settings, as '<key>__<sheet name>'
      3. one required sheet missing and ONE sheet left over named 'Sheet1' (or the only sheet): that sheet is it
      4. still missing: 'missing' (an error) or 'optional_missing' (config "optional": True)
    """
    names = {key: cfg["sheet_names"] for key, cfg in sheet_cfgs.items()}
    found, missing, extras = identify_names(wb.sheet_names, names, settings.FUZZY_THRESHOLD,
                                            label=f"sheet in '{wb.file_name}'", is_file=False, quiet=True)
    entry["unexpected_sheets"] = list(extras)
    to_read = [(key, key, name) for key, name in found.items()]

    # ---- 2. several sheets like one configured sheet ----
    for key in list(missing):
        alike = [n for n in extras if match_score(n, names[key], is_file=False) >= settings.FUZZY_THRESHOLD]
        if len(alike) > 1 and not sheet_cfgs[key].get("skip"):
            logger.warning(f"[{wb_key}/{key}] several sheets like '{names[key][0]}': {alike} - each read with its settings")
            missing.remove(key)
            entry["sheets"][key] = {"status": "split_into_variants", "variants": alike}
            for name in alike:
                extras.remove(name)
                to_read.append((f"{key}__{safe_name(name)}", key, name))

    # ---- 3. one required sheet missing, one sheet left with a default name ('Sheet1') or the file has one
    #         sheet only: that is the one (a real name like 'IRI' is never taken for another sheet) ----
    required = [k for k in missing if not sheet_cfgs[k].get("optional") and not sheet_cfgs[k].get("skip")]
    if len(required) == 1 and len(extras) == 1 and (len(wb.sheet_names) == 1
                                                     or re.fullmatch(r"sheet\s*\d*", extras[0].strip().lower())):
        key, name = required[0], extras.pop()
        logger.warning(f"[{wb_key}/{key}] '{names[key][0]}' not found by name - the only other sheet '{name}' is used")
        missing.remove(key)
        to_read.append((key, key, name))

    # ---- 4. still missing ----
    for key in missing:
        optional = sheet_cfgs[key].get("optional") or sheet_cfgs[key].get("skip")   # a help sheet is never needed
        entry["sheets"][key] = {"status": "optional_missing" if optional else "missing", "expected_names": names[key]}
        scores = sorted(((match_score(n, names[key], is_file=False), n) for n in extras), reverse=True)
        if scores:
            entry["sheets"][key]["closest"] = f"closest sheet in the file: '{scores[0][1]}' ({scores[0][0]:.2f})"
        (logger.info if optional else logger.error)(
            f"[{wb_key}/{key}] {'optional ' if optional else ''}sheet not in '{wb.file_name}' (expected like "
            f"'{names[key][0]}')" + (f" - {entry['sheets'][key]['closest']}" if scores else ""))
    return to_read, extras


def _extract_sheet(run, wb, wb_key, sheet_key, sheet_name, options, check_nulls, result, meta):
    label = f"{wb_key}/{sheet_key}"

    # ---- b. read as a table ----
    table = wb.read_table(sheet_name, options)
    df = table.to_frame()
    result.update(header_excel_rows=table.header_excel_rows, rows=len(df), columns=len(table.columns),
                  columns_read=options.get("columns", "all"))
    logger.info(f"[{label}] read '{sheet_name}': header rows {table.header_excel_rows}, "
                f"{len(df)} data rows, {len(table.columns)} columns")

    # ---- c. GATE: headers - match to the schema (exact / normalized / unit / alias), then final names ----
    schema = None if settings.EXTRACT_ALL else load_schema(
        settings.SCHEMAS_DIR / run.deliverable / f"{options.get('schema', f'{wb_key}/{sheet_key}')}.csv")
    expected, aliases, info = schema if schema else ([], {}, {})
    matched = match_headers(table.names, expected, aliases, info,
                            letters=[c["letter"] for c in table.columns],
                            values=[[row[i] for row in table.rows] for i in range(len(table.columns))])
    if settings.EXTRACT_ALL:
        result["header_check"] = "off (EXTRACT_ALL)"
    elif schema is None:
        logger.warning(f"[{label}] no schema file - headers not checked "
                       f"(create it with: python tools/make_schema.py {run.deliverable})")
        result["header_check"] = "no schema file"
    else:
        result["header_check"] = check_headers(matched, label, gate=settings.CHECK_HEADERS)

    columns = matched["columns"]
    if matched["missing"]:     # layout differs from the template: the column in the template's place may be
        columns = [{**c, "schema_header": None} if c["match"] == "position" else c for c in columns]  # another one
    names = final_names(columns)                       # schema name without unit, e.g. 'Chainage from'
    meta["schema"][sheet_key] = {
        "sheet_name": sheet_name,
        "status": "rejected" if result["header_check"] not in ("off (EXTRACT_ALL)", "no schema file")
                  and not result["header_check"]["ok"] else "accepted",
        "missing_headers": matched["missing"],
        "columns": [{"column": n, "excel_column": col["letter"], **c}
                    for n, col, c in zip(names, table.columns, columns)],
    }
    if meta["schema"][sheet_key]["status"] == "rejected":
        result["status"] = "rejected_headers"
        return
    table.rename(names)
    df = table.to_frame()

    # ---- d. formulas + legends ----
    formulas = wb.read_formulas(sheet_name, table)
    if formulas:
        meta["formulas"][sheet_key] = {"sheet_name": sheet_name, "columns": describe_formulas(formulas, table)}
        logger.info(f"[{label}] {len(formulas)} calculated column(s) documented in formulas.json")
    if table.dictionary:
        meta["data_dictionary"][sheet_key] = {"sheet_name": sheet_name, "columns": table.dictionary}

    # ---- e. profile ----
    profile = profile_table(df, table.columns, formulas.keys(), table.header_excel_rows)
    meta["profile"][sheet_key] = {"sheet_name": sheet_name, **profile}
    result["formula_only_rows"] = profile["formula_only_rows"]
    if profile["formula_only_rows"]:
        logger.warning(f"[{label}] {profile['formula_only_rows']} row(s) have values ONLY in formula columns "
                       f"(input columns empty - e.g. unfilled template rows) - saved as-is, see profile.json")

    # ---- f. reports (never stop anything) ----
    result["empty_check"] = empty_sheet_report(df, label)
    if check_nulls and options.get("check_nulls", True) and len(df):
        result["defect_check"] = defect_columns_report(df, settings.DEFECT_KEYWORDS, settings.NULL_LIMIT,
                                                       settings.NULL_IGNORE, label)

    # ---- g. save ----
    result["outputs"] = run.save_table(df, wb_key, sheet_key)
    if options.get("cells"):
        _save_cells(run, wb, wb_key, sheet_key, sheet_name, options["cells"], result)
    result["status"] = "saved"
    logger.info(f"[{label}] saved {len(df)} rows -> {run.file_name(wb_key, sheet_key)}.parquet + .csv")


def _save_cells(run, wb, wb_key, sheet_key, sheet_name, columns, result):
    """Report-style sheet (or a side area of a sheet): every non-empty cell with its address."""
    cells = wb.read_cells(sheet_name, columns)
    content = {"sheet_name": sheet_name, "columns": columns or "all",
               "merged_cells": wb.merged_refs(sheet_name), "cells": cells}
    area = [columns.replace(":", "")] if columns else []                 # e.g. 'IM' for columns I:M
    path = run.save_json(content, wb_key, sheet_key, *area, "cells")
    result.setdefault("outputs", []).append(path)
    logger.info(f"[{wb_key}/{sheet_key}] {len(cells)} cells{' in ' + columns if columns else ''} saved -> {path}")


def extract_unmatched_files(run, check_nulls=False):
    """
    After the configured workbooks (find_workbooks):
      - several files that fit one workbook: each extracted with that workbook's settings as
        '<workbook>__<file name>' (a byte-identical copy such as 'x (1).xlsm' only once)
      - Excel files not in the deliverable config: every sheet saved, header found automatically
        (settings.EXTRACT_UNMATCHED)
    """
    import hashlib
    done = {}
    for name, (key, cfg, path) in getattr(run, "workbook_variants", {}).items():
        digest = hashlib.sha256(settings.INPUT.read_bytes(path)).hexdigest()
        if digest in done:
            logger.info(f"[{key}] '{name}' is the same file as '{done[digest]}' (identical content) - not extracted again")
            run.manifest["workbooks"][key].setdefault("identical_copies", []).append(name)
            continue
        done[digest] = name
        vkey = f"{key}__{safe_name(PurePosixPath(name).stem)}"
        run.manifest["workbooks"][vkey] = {"file": PurePosixPath(name).name, "status": "found", "variant_of": key,
                                           "identified_by": run.manifest["workbooks"][key].get("identified_by"),
                                           "sheets": {}}
        extract_workbook(run, {vkey: path}, vkey, {vkey: {**cfg, "schema_workbook": key}}, check_nulls=check_nulls)

    if not settings.EXTRACT_UNMATCHED:
        return
    for name, path in getattr(run, "unmatched_files", {}).items():
        key = f"extra_{safe_name(PurePosixPath(name).stem)}"
        logger.info(f"[{key}] '{name}' is not in the config - every sheet saved as found")
        run.manifest["workbooks"][key] = {"file": PurePosixPath(name).name, "status": "found", "sheets": {}}
        extract_workbook(run, {key: path}, key, {key: {"file_names": [name], "sheets": {}}})
        run.manifest["unexpected_files"].remove(name)


def _file_facts(path, data):
    import hashlib
    return {"file": PurePosixPath(path).name, "path": path, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


# =============================================================================
# 3. D3: every csv under a folder, any depth
# =============================================================================
def extract_csv_folder(run, folder, groups):
    """
    Each csv is saved as d3_<group>_<file>.parquet + .csv. The group comes from its folder / file name
    (groups = {"ca": ["as per ca", "ca"], ...}); no match -> group 'ungrouped'.
    """
    import io
    import pandas as pd

    files = [p for p in settings.INPUT.list_files(folder)
             if p.lower().endswith(".csv") and not PurePosixPath(p).name.startswith("~$")]
    logger.info(f"{len(files)} csv file(s) under '{settings.INPUT.describe()}/{folder}'")
    profiles, schemas, used = {}, {}, set()

    for path in files:
        relative = PurePosixPath(path).relative_to(folder).as_posix()
        group = classify_path(relative, groups) or "ungrouped"
        data = settings.INPUT.read_bytes(path)
        entry = {"group": group, **_file_facts(path, data)}
        run.manifest["csv_files"][relative] = entry

        # ---- read as text, exactly as in the file ----
        try:
            try:
                df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="latin-1")
        except Exception as e:
            logger.error(f"[{relative}] could not read: {e}")
            entry.update(status="read_error", error=str(e))
            continue
        df = df.astype(object).where(df != "", None)
        matched = match_headers(list(df.columns), [])                  # no schema for csv: just unit-free names
        names = final_names(matched["columns"])
        schemas[relative] = {"columns": [{"column": n, **c} for n, c in zip(names, matched["columns"])]}
        columns = [{"name": n, "vendor_header": c["vendor_header"]} for n, c in zip(names, matched["columns"])]
        df.columns = names
        df.insert(0, "source_row", range(2, len(df) + 2))             # line 1 is the header

        # ---- report + profile + save ----
        entry["empty_check"] = empty_sheet_report(df, relative)
        profiles[relative] = profile_table(df, columns, header_excel_rows=[1])
        name = safe_name(PurePosixPath(relative).stem)
        while (group, name) in used:                                   # same file name in two folders
            name += "_2"
        used.add((group, name))
        entry["outputs"] = run.save_table(df, group, name)
        entry.update(status="saved", rows=len(df), columns=len(df.columns) - 1)
        logger.info(f"[{relative}] -> {group}: saved {len(df)} rows -> {run.file_name(group, name)}.parquet + .csv")

    if profiles:
        run.save_json(schemas, "schema")
        run.save_json(profiles, "profile")


# =============================================================================
# 4. Survey image folders (D4: crack_images_DHMS/L1/*.jpg, pothole images ...) - optional
# =============================================================================
IMAGE_COLUMNS_FIRST = ["image_folder", "lane", "file_name", "image_path", "status", "issues", "fields_found",
                       "chainage", "latitude", "longitude", "location_source", "location_check_m",
                       "chainage_start", "chainage_end", "nsv_cracks_chainage",
                       "ocr_chainage", "ocr_latitude", "ocr_longitude"]


def _image_location_tables(files, location):
    """location = {"defect_report": wb key, "sheet": name, "nsv_cracks": wb key} -> (lanes, offsets)."""
    from nsv.images import read_lane_locations, read_offsets
    lanes, offsets = {}, {}
    if not location:
        return lanes, offsets
    files = files or {}
    try:
        if files.get(location.get("defect_report")):
            lanes = read_lane_locations(settings.INPUT.read_bytes(files[location["defect_report"]]),
                                        location.get("sheet", "Data Sheet"))
            logger.info(f"[images] defect report: lat/long for lane(s) {sorted(lanes)}")
        else:
            logger.warning("[images] no defect report - images located by EXIF / OCR only")
        if files.get(location.get("nsv_cracks")):
            offsets = read_offsets(settings.INPUT.read_bytes(files[location["nsv_cracks"]]))
            logger.info(f"[images] NSV Cracks chainage offset per survey run: {offsets}")
    except Exception as e:
        logger.error(f"[images] could not read the location workbooks ({e}) - images located by EXIF / OCR only")
    return lanes, offsets


def extract_image_folders(run, folder, image_folders, files=None, location=None):
    """
    image_folders: {group: {"keywords": ["crack"], "required": False}}
    A sub-folder (directly under the deliverable folder) whose name contains all keywords holds that
    group's images, at any depth below it (lane folders L1..R4). One table per group:
    d4_images_<group>.parquet + .csv, one row per image (see nsv/images.py).
    A group with no folder: 'missing' - highlighted in the summary; a problem only if required.
    files + location (optional): workbooks used to give every image its chainage and lat/long
    (see images.locate); without them the OCR values stay as read.
    """
    import pandas as pd
    from nsv.images import lane_from_path, locate, ocr_available, read_images

    base = folder.strip("/")
    by_folder = {}                                         # first-level sub-folder -> image paths
    for path in settings.INPUT.list_files(folder):
        rel = PurePosixPath(path).relative_to(base)
        if len(rel.parts) >= 2 and rel.suffix.lower() in settings.IMAGE_EXTENSIONS:
            by_folder.setdefault(rel.parts[0], []).append(path)
    manifest = run.manifest.setdefault("image_folders", {})

    ocr = settings.OCR_IMAGES and ocr_available(settings.TESSERACT_CMD)
    if settings.OCR_IMAGES and not ocr and by_folder:
        logger.error("OCR is not available (tesseract not found) - images are listed, but the text on them "
                     "is not read. Install tesseract and set settings.TESSERACT_CMD.")
    cfg = {"ocr": ocr, "tesseract_cmd": settings.TESSERACT_CMD, "regions": settings.IMAGE_TEXT_REGIONS,
           "fields": settings.IMAGE_FIELDS, "ocr_mode": settings.IMAGE_OCR}
    lanes, offsets = _image_location_tables(files, location) if by_folder else ({}, {})
    if offsets:
        run.manifest["image_chainage_offsets"] = offsets

    used = set()
    for group, gcfg in image_folders.items():
        keywords = [normalize_name(k, is_file=False) for k in gcfg["keywords"]]
        folders = sorted(f for f in by_folder
                         if f not in used and all(k in normalize_name(f, is_file=False) for k in keywords))
        entry = manifest[group] = {"required": gcfg.get("required", False), "folders": folders}

        # ---- folder not delivered ----
        if not folders:
            entry["status"] = "missing"
            say = logger.error if entry["required"] else logger.warning
            say(f"[{group}] no image folder with {gcfg['keywords']} in its name"
                f"{' (REQUIRED)' if entry['required'] else ' (optional)'}")
            continue
        used.update(folders)

        # ---- read every image (parallel, in batches) ----
        paths = sorted(p for f in folders for p in by_folder[f])
        logger.info(f"[{group}] {len(paths)} image(s) in {folders} - "
                    f"{'reading text with OCR' if ocr else 'OCR off'} on {settings.IMAGE_WORKERS} core(s)")

        def make_job(path):
            rel = PurePosixPath(path).relative_to(base)
            return (settings.INPUT.full_path(path), settings.INPUT.read_bytes(path),
                    lane_from_path(rel.parts[1:-1]), rel.parts[0], cfg)

        def progress(done, total):
            logger.info(f"[{group}] {done}/{total} images read")

        rows = read_images(paths, make_job, settings.IMAGE_WORKERS, progress=progress)
        if location:
            rows = [locate(r, lanes, offsets, settings.IMAGE_LOCATION_AGREE_M) for r in rows]
        df = pd.DataFrame(rows)
        first = [c for c in IMAGE_COLUMNS_FIRST if c in df.columns]
        std = [c for c in settings.IMAGE_FIELDS if c in df.columns and c not in first]
        rest = sorted(c for c in df.columns if c not in first and c not in std)
        df = df[first + std + rest]

        counts = df["status"].value_counts().to_dict()
        with_issues = int((df["issues"].fillna("") != "").sum())
        sources = df["location_source"].value_counts().to_dict() if "location_source" in df else {}
        entry.update(status="saved", images=len(df), image_status=counts, images_with_issues=with_issues,
                     location_source=sources, ocr=ocr, outputs=run.save_table(df, "images", group))
        logger.info(f"[{group}] saved {len(df)} image rows -> {run.file_name('images', group)}.parquet + .csv "
                    f"(status: {counts}, {with_issues} with issues, location: {sources})")

    # ---- image folders that belong to no group ----
    other = sorted(f for f in by_folder if f not in used)
    if other:
        logger.warning(f"image folder(s) not in the config, ignored: {other}")
        run.manifest["unexpected_image_folders"] = other


# =============================================================================
# 5. settings.EXTRACT_ALL: every workbook, every sheet, header found automatically, no header check
# =============================================================================
def extract_everything(run, folder):
    logger.warning(f"{run.deliverable}: EXTRACT_ALL is on - config ignored, every workbook and sheet is read")
    for path in _excel_files(folder):
        key = safe_name(PurePosixPath(path).stem)
        wb = ExcelWorkbook(PurePosixPath(path).name, settings.INPUT.read_bytes(path))
        sheets = {safe_name(s): {"sheet_names": [s]} for s in wb.sheet_names}
        wb.close()
        workbooks = {key: {"file_names": [PurePosixPath(path).name], "sheets": sheets}}
        run.manifest["workbooks"][key] = {"file": PurePosixPath(path).name, "status": "found", "sheets": {}}
        extract_workbook(run, {key: path}, key, workbooks)
