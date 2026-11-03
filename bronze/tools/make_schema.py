"""
make_schema.py - create the expected-header files (schemas/<deliverable>/<workbook>/<sheet>.csv)
from a template / known-good file, instead of typing headers by hand.

    python tools/make_schema.py d2                              from the first survey that has a d2 folder
    python tools/make_schema.py d2 --upc N-08004-03001-HR       from this survey's d2 folder
    python tools/make_schema.py d2 --force                      overwrite existing schema files too

It reads the files in that survey's raw folder exactly like the pipeline does (same header
options from deliverables/dN.py), so the schema always matches what the extractor produces.
ALWAYS open the created CSVs and check them before committing - they become the header gate.
"""
import csv
import importlib
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settings
from nsv.checks import load_schema
from nsv.excel import ExcelWorkbook
from nsv.headers import value_type
from nsv.matching import identify_names
from nsv.survey import discover_surveys


def main(deliverable, upc=None, force=False):
    module = importlib.import_module(f"deliverables.{deliverable}")
    if not hasattr(module, "WORKBOOKS"):
        print(f"{deliverable} has no WORKBOOKS (csv deliverable) - nothing to do")
        return

    # ---- which survey's files to read: the given UPC, or the first survey that has this deliverable ----
    surveys = [s for s, present in discover_surveys(upcs=[upc] if upc else None).items() if deliverable in present]
    if not surveys:
        print(f"no survey with a '{deliverable}' folder found")
        return
    folder = surveys[0].raw_path(deliverable)
    print(f"reading templates from: {folder}")

    files = {PurePosixPath(p).name: p for p in settings.INPUT.list_files(folder)
             if p.lower().endswith((".xlsx", ".xlsm", ".xls")) and not PurePosixPath(p).name.startswith("~$")}
    found, _, _ = identify_names(list(files), {k: c["file_names"] for k, c in module.WORKBOOKS.items()},
                                 settings.FUZZY_THRESHOLD)

    for wb_key, file_name in found.items():
        wb = ExcelWorkbook(file_name, settings.INPUT.read_bytes(files[file_name]))
        sheets = module.WORKBOOKS[wb_key]["sheets"]
        found_sheets, _, _ = identify_names(wb.sheet_names, {k: c["sheet_names"] for k, c in sheets.items()},
                                            settings.FUZZY_THRESHOLD, label=f"sheet in '{file_name}'", is_file=False)
        for sheet_key, sheet_name in found_sheets.items():
            options = sheets[sheet_key]
            if options.get("skip") or options.get("kind") == "cells":
                continue
            target = settings.SCHEMAS_DIR / deliverable / wb_key / f"{sheet_key}.csv"
            if target.exists() and not force:
                print(f"exists, kept:  {target.relative_to(settings.PROJECT_ROOT)}")
                continue
            table = wb.read_table(sheet_name, options)
            kept = load_schema(target)[1] if target.exists() else {}     # aliases typed in by hand are kept
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # excel_column + value_type: used when a header differs but the column in the same place
                # holds the same kind of values (accepted with a warning). aliases: other names, sep. by |
                writer.writerow(["header", "excel_column", "value_type", "aliases"])
                for i, col in enumerate(table.columns):
                    kind = value_type([row[i] for row in table.rows])
                    writer.writerow([col["name"], col["letter"], kind, "|".join(kept.get(col["name"], []))])
            print(f"written:       {target.relative_to(settings.PROJECT_ROOT)}  ({len(table.columns)} headers)")
        wb.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    upc = sys.argv[sys.argv.index("--upc") + 1] if "--upc" in sys.argv else None
    main(sys.argv[1], upc=upc, force="--force" in sys.argv)
