"""
d4.py - Deliverable 4: NHAI One / defect reports
    CRRI_PCI(IRC82-2023).xlsm, Defect Report_DHMS.xlsx, Inspection Sathi Format.xlsx,
    NSV Cracks_DHMS.xlsx, NSV NHAI ONE Format.xlsx  - each with its own layout

Run:    python main.py d4                       every survey that has a d4 folder
        python main.py d4 --upc N-08004-03001-HR  one survey
Expected headers: schemas/d4/<workbook>/<sheet>.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # allows: python deliverables/d4.py ...

import settings
from nsv.pipeline import extract_everything, extract_unmatched_files, extract_image_folders, extract_workbook, find_workbooks
from nsv.run import Run

DELIVERABLE = "d4"


# =============================================================================
# WHAT to extract
# =============================================================================
CRRI_TABLE = {"columns": "A:Y"}     # A:Y = the PCI table; column AA holds the lane dropdown list (not data)

WORKBOOKS = {
    "crri_pci": {
        "file_names": ["CRRI_PCI(IRC82-2023).xlsm"],
        "sheets": {
            "rural_roads": {"sheet_names": ["RURAL ROADS (CRRI.PCI-PK)"], **CRRI_TABLE, "optional": True},  # road class
            "mdr":         {"sheet_names": ["MDR (CRRI.PCI-PK)"], **CRRI_TABLE, "optional": True},          # sheets: only
            "urban_roads": {"sheet_names": ["UrbanRoads (CRRI.PCI-PK)"], **CRRI_TABLE, "optional": True},  # the road's class
            "highways":    {"sheet_names": ["HIGHWAYS(CRRI.PCI-PK)"], **CRRI_TABLE},
        },
    },
    "defect_report": {
        "file_names": ["Defect Report_DHMS.xlsx"],
        "sheets": {
            "data_sheet": {"sheet_names": ["Data Sheet"],
                           "header_row": 1, "header_rows": 3,
                           "info_rows": [3]},     # row 3 = allowed values ('Good/Poor', 'Rating (1-5)')
        },
    },
    "inspection_sathi": {
        "file_names": ["Inspection Sathi Format.xlsx"],
        "sheets": {
            "inspection_sathi": {"sheet_names": ["app_AUG_26_Comparison_as_per_CA"],
                                 "header_row": 1, "header_rows": 3},
        },
    },
    "nsv_cracks": {
        "file_names": ["NSV Cracks_DHMS.xlsx"],
        "sheets": {
            "cracks": {"sheet_names": ["Cracks"], "header_row": 1, "header_rows": 2},
        },
    },
    "nhai_one": {
        "file_names": ["NSV NHAI ONE Format.xlsx"],
        "sheets": {
            "potholes": {"sheet_names": ["Potholes"],
                         "header_row": 1, "header_rows": 2,
                         "info_rows": [2]},       # row 2 = field descriptions ('1 for Flexible, 2 for Rigid')
        },
    },
}


# Image folders inside the d4 folder, e.g. d4/crack_images_DHMS/L1/*.jpg.
# A sub-folder whose name contains all keywords belongs to the group (any case / spaces / _ ignored).
# required False = optional: if it is not delivered, it is highlighted in the summary but nothing fails.
IMAGE_FOLDERS = {
    "crack_images":   {"keywords": ["crack"],   "required": False},
    "pothole_images": {"keywords": ["pothole"], "required": False},
}

# Workbooks (keys of WORKBOOKS) that locate the images: lane lat/long per chainage + NSV Cracks run offsets.
IMAGE_LOCATION = {"defect_report": "defect_report", "sheet": "Data Sheet", "nsv_cracks": "nsv_cracks"}


# =============================================================================
# HOW: the flow
# =============================================================================
def run(survey):
    run = Run(DELIVERABLE, survey)          # input: survey's raw folder, output: its bronze folder

    if settings.EXTRACT_ALL:
        extract_everything(run, run.input_folder)
        return run.finish()

    # ---- step 0: are the expected workbooks there? ----
    files = find_workbooks(run, run.input_folder, WORKBOOKS)
    if files is None:
        return run.finish(status="rejected_missing_workbook")

    # ---- block 1: CRRI PCI (4 road classes) ----
    extract_workbook(run, files, "crri_pci", WORKBOOKS, check_nulls=True)

    # ---- block 2: Defect Report (DHMS) ----
    extract_workbook(run, files, "defect_report", WORKBOOKS, check_nulls=True)

    # ---- block 3: Inspection Sathi ----
    extract_workbook(run, files, "inspection_sathi", WORKBOOKS, check_nulls=True)

    # ---- block 4: NSV Cracks (DHMS) ----
    extract_workbook(run, files, "nsv_cracks", WORKBOOKS, check_nulls=True)

    # ---- block 5: NHAI One potholes ----
    extract_workbook(run, files, "nhai_one", WORKBOOKS, check_nulls=True)

    # ---- block 6: survey images (optional folders) -> one row per image: OCR text + chainage + lat/long ----
    extract_image_folders(run, run.input_folder, IMAGE_FOLDERS, files, IMAGE_LOCATION)

    # ---- Excel files that are not in the config: every sheet saved as found ----
    extract_unmatched_files(run, check_nulls=True)

    return run.finish()


if __name__ == "__main__":                 # python deliverables/d4.py [--zone A ...] = python main.py d4 [...]
    from main import main
    sys.exit(main(["d4", *sys.argv[1:]]))
