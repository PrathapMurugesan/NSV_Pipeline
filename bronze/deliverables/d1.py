"""
d1.py - Deliverable 1: Data Lake Format
    VisualCondition.xlsx, LRMS_DataTemplate v1.0.xlsx, RIS_DataTemplate v1.0.xlsx

Run:    python main.py d1                       every survey that has a d1 folder
        python main.py d1 --upc N-08004-03001-HR  one survey

Top part  = WHAT to extract (edit this when a vendor renames a file or sheet: add the new name).
Bottom    = run(): one block per workbook, in order.
Expected headers: schemas/d1/<workbook>/<sheet>.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # allows: python deliverables/d1.py ...

import settings
from nsv.pipeline import extract_everything, extract_unmatched_files, extract_workbook, find_workbooks
from nsv.run import Run

DELIVERABLE = "d1"


# =============================================================================
# WHAT to extract
#   workbook key -> known file names + its sheets
#   sheet key    -> known sheet names (+ header options, see nsv/pipeline.py)
#   All D1 sheets have a single header row, found automatically (also below a merged title row).
# =============================================================================
def _sheets(*names):
    """'NHMaster', 'Input Parameters' -> {'nhmaster': {...}, 'input_parameters': {...}}"""
    return {name.lower().replace(" ", "_"): {"sheet_names": [name]} for name in names}


WORKBOOKS = {
    "visual_condition": {
        "file_names": ["VisualCondition.xlsx", "Visual Condition.xlsx"],
        "sheets": {
            "visual_condition": {"sheet_names": ["VisualCondition", "Visual Condition", "Sheet1"]},
        },
    },
    "lrms": {
        "file_names": ["LRMS_DataTemplate v1.0.xlsx", "LRMS Data Template.xlsx"],
        "sheets": {
            "table_list": {"sheet_names": ["Table List"], "skip": True},     # description sheet of the template
            **_sheets("NHMaster", "SectionMaster", "LinkMaster", "LRPMaster", "GPS",
                      "RegionalOffice", "PIU", "Input Parameters"),
        },
    },
    "ris": {
        "file_names": ["RIS_DataTemplate v1.0.xlsx", "RIS Data Template.xlsx"],
        "sheets": {
            "table_list": {"sheet_names": ["Table List"], "skip": True},     # description sheet of the template
            **_sheets("CarriagewayType", "RoadType", "PavementType", "PavementWidth", "ShoulderType",
                      "ShoulderWidth", "Topography", "CrossSection", "DrainType", "Geometry", "ServiceRoad",
                      "MedianOpening", "RightOfWay", "VisualCondition", "Roughness", "RutDepth",
                      "TextureDepth", "SkidResistence", "FWD", "FWDRigid", "PavementComposition",
                      "CarriagewayFurniture", "StructureDetails", "WaysideAmenities", "Utilities",
                      "FRLData", "LandUse"),
        },
    },
}


# =============================================================================
# HOW: the flow
# =============================================================================
def run(survey):
    run = Run(DELIVERABLE, survey)          # input: survey's raw folder, output: its bronze folder

    # ---- switch: read everything without config (settings.EXTRACT_ALL) ----
    if settings.EXTRACT_ALL:
        extract_everything(run, run.input_folder)
        return run.finish()

    # ---- step 0: are the expected workbooks there? ----
    files = find_workbooks(run, run.input_folder, WORKBOOKS)
    if files is None:
        return run.finish(status="rejected_missing_workbook")

    # ---- block 1: Visual Condition ----
    extract_workbook(run, files, "visual_condition", WORKBOOKS)

    # ---- block 2: LRMS data template ----
    extract_workbook(run, files, "lrms", WORKBOOKS)

    # ---- block 3: RIS data template ----
    extract_workbook(run, files, "ris", WORKBOOKS)

    # ---- Excel files that are not in the config: every sheet saved as found ----
    extract_unmatched_files(run)

    return run.finish()


if __name__ == "__main__":                 # python deliverables/d1.py [--zone A ...] = python main.py d1 [...]
    from main import main
    sys.exit(main(["d1", *sys.argv[1:]]))
