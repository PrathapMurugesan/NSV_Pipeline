"""
d2.py - Deliverable 2: Comparison as per CA
    One macro workbook: 'Sep-26 Comparison as per CA <road>.xlsm' (name changes every month - fuzzy match)

Run:    python main.py d2                       every survey that has a d2 folder
        python main.py d2 --upc N-08004-03001-HR  one survey
Expected headers: schemas/d2/<workbook>/<sheet>.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # allows: python deliverables/d2.py ...

import settings
from nsv.pipeline import extract_everything, extract_unmatched_files, extract_workbook, find_workbooks
from nsv.run import Run

DELIVERABLE = "d2"


# =============================================================================
# WHAT to extract
# =============================================================================
COMPARISON_HEADER = {          # 'Comparison as per CA / IRC': 4 header rows, row 2 = thresholds (formulas)
    "header_row": 1,           # header starts in Excel row 1
    "header_rows": 4,          # rows 1-4 form the column names: 'Current Cycle | Roughness BI (in mm/km) | L1'
    "info_rows": [2],          # row 2 holds the thresholds (2500, 10.00 ...) -> data_dictionary.json, not names
}

WORKBOOKS = {
    "comparison": {
        "file_names": ["Sep-26 Comparison as per CA Kaithal to Rajasthan border NH-152.xlsm",
                       "Comparison as per CA.xlsm", "Comparison as per CA.xlsx"],
        # The name changes with every road and month ('Sep-26 D2 Swaroopganj-Pindwara-Udaipur Section of
        # NH-27Comparison File - Single Pavement Type.xlsm'). If no name above matches, the file whose name
        # contains these words AND that has these sheets is the comparison workbook:
        "file_keywords": ["comparison"],
        "identify_by_sheets": ["Comparison as per CA", "Comparison as per IRC"],
        "sheets": {
            # ---- report sheets: saved cell by cell (JSON), not as tables ----
            "summary":           {"sheet_names": ["Summary"], "kind": "cells"},
            "inputs_thresholds": {"sheet_names": ["Inputs (Thresholds)"], "kind": "cells"},

            # ---- data tables ----
            "comparison_ca":     {"sheet_names": ["Comparison as per CA"], **COMPARISON_HEADER},
            "comparison_irc":    {"sheet_names": ["Comparison as per IRC"], **COMPARISON_HEADER},
            # interval sheets, only in some templates: '100m', '2025-100m', '1000m', '1km', '1 Km' all match
            "sheet_100m":        {"sheet_names": ["100m"], "optional": True},
            "sheet_1000m":       {"sheet_names": ["1000m", "1km"], "optional": True},
            # extra sheets some vendors deliver (no schema yet: saved with the vendor's headers)
            "iri":               {"sheet_names": ["IRI", "IRI-Rut"], "optional": True},
            "cracks":            {"sheet_names": ["Cracks"], "optional": True},
            "rut_depth":         {"sheet_names": ["RutDepth", "Rut Depth"], "optional": True},
            "uc_maintenance":    {"sheet_names": ["UC & Maintenance Stretches"]},
            "potholes":          {"sheet_names": ["Potholes & Minor Depressions"],
                                  "columns": "A:G",     # the pothole list; I:M are small count tables ...
                                  "cells": "I:M"},      # ... saved cell by cell next to it
            "validation_checks": {"sheet_names": ["Validation Checks"],
                                  "columns": "I:P", "header_row": 2,   # the workbook's own check log
                                  "cells": "A:G"},                     # left-hand check blocks, cell by cell

            # ---- help / lookup sheets: deliberately not extracted ----
            "instructions_guide": {"sheet_names": ["Instructions Guide"], "skip": True},
            "remarks_dictionary": {"sheet_names": ["Remarks Dictionary"], "skip": True},
        },
    },
}


# =============================================================================
# HOW: the flow
# =============================================================================
def run(survey):
    run = Run(DELIVERABLE, survey)          # input: survey's raw folder, output: its bronze folder

    if settings.EXTRACT_ALL:
        extract_everything(run, run.input_folder)
        return run.finish()

    # ---- step 0: is the workbook there? ----
    files = find_workbooks(run, run.input_folder, WORKBOOKS)
    if files is None:
        return run.finish(status="rejected_missing_workbook")

    # ---- block 1: Comparison workbook (with defect-column / lane report) ----
    extract_workbook(run, files, "comparison", WORKBOOKS, check_nulls=True)

    # ---- Excel files that are not in the config: every sheet saved as found ----
    extract_unmatched_files(run, check_nulls=True)

    return run.finish()


if __name__ == "__main__":                 # python deliverables/d2.py [--zone A ...] = python main.py d2 [...]
    from main import main
    sys.exit(main(["d2", *sys.argv[1:]]))
