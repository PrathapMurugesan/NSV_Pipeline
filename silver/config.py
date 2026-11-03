"""
Project settings. Edit the paths here, or override them on the command line:
    python main.py --project <dir> --tracker <xlsx> --out <dir>
"""

# ── Input / output paths ─────────────────────────────────────────────────────
PROJECT_DIR     = r"D:\NSV\New_Structure_Data\zone=B\zone=B\phase=3\cycle=1\N-02017-03001-RJ"
# NSV Activity Tracker: the newest 'NSV_Activity_Tracker*.xlsx' in this code folder (any name suffix, e.g.
# 'NSV_Activity_Tracker_ZoneWise_Standardized 4 (1) (1).xlsx'); else the path below. --tracker overrides both.
import glob as _glob
import os as _os
_TRACKERS = sorted(_glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'NSV_Activity_Tracker*.xls*')),
                   key=_os.path.getmtime)
TRACKER_PATH    = _TRACKERS[-1] if _TRACKERS else r"D:\NSV\Tracker\NSV_Activity_Tracker_ZoneWise_Standardized.xlsx"
OUTPUT_ROOT     = r"D:\NSV\New_Structure_Data\New_Structure_Output"
SCHEMA_WORKBOOK = None   # r"D:\NSV\Schema\nsv_defect_schema_master 1.xlsx"  (optional)

# ── Output formats ───────────────────────────────────────────────────────────
WRITE_PARQUET = True
WRITE_EXCEL   = False    # ~1.5 min per 90k-row table; use --no-excel while testing
WRITE_CSV     = True

# ── Segment sizes ────────────────────────────────────────────────────────────
PARENT_LENGTH_M = 1000   # 1 km parent table
CHILD_100M      = 100
CHILD_10M       = 10

# parent_id / parent_start_chainage / parent_end_chainage block per level — as the DLM notebook:
#   every level (10 m incl. cracking and pothole, 100 m, 1 km) → its 1 km block: {UPC}_{lane}_{km start}_{km end}
#   (display_defect_id links a 10 m / 100 m row to its 1 km parent through this parent_id)
PARENT_BLOCK_BY_LEVEL = {'child_10m': 1000, 'child_100m': 1000, 'parent': 1000}

# display_defect_id (DLM notebook post-process): output tables whose rows get a top-level id, and child → parent
DISPLAY_ID_BASE_TABLES = ['master_roughness_parent', 'master_rutting_parent', 'master_cracking_parent',
                          'master_ravelling_parent', 'master_pothole']
DISPLAY_ID_CHILD_PARENT = {
    'master_cracking_child_100m': 'master_cracking_parent',
    'master_cracking': 'master_cracking_parent',
    'master_roughness_child_100m': 'master_roughness_parent',
    'master_roughness_child_10m': 'master_roughness_parent',
    'master_rutting_child_100m': 'master_rutting_parent',
    'master_rutting_child_10m': 'master_rutting_parent',
    'master_ravelling_child_100m': 'master_ravelling_parent',
    'master_ravelling_child_10m': 'master_ravelling_parent',
}

# ── Fixed values ─────────────────────────────────────────────────────────────
SOURCE_SYSTEM = "NSV"
LANE_AREA_SQM = 35.0     # 10 m × 3.5 m lane, used for ravelling % and cracking JSON

# Good / Fair / Poor ranges per defect → defect_rules.json (thresholds come ONLY from D2, no defaults)
import json as _json
import os as _os
DEFECT_RULES_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'defect_rules.json')
with open(DEFECT_RULES_PATH, encoding='utf-8') as _f:
    DEFECT_RULES = _json.load(_f)
# Defects that take a threshold — ONLY from D2 (no defaults anywhere). No D2 threshold → threshold_value,
# breach_flag and road_condition NULL, calculated severity / ratings NULL, and the DQ 'threshold' check FAILs.
THRESHOLD_DEFECTS = ['roughness', 'rutting', 'cracking', 'ravelling']

# PCI weights (sum = 1.0). Roughness input = IRI (m/km). Blank inputs are not counted: only 10 m slots
# with at least one real value get a PCI; 100 m / 1 km PCI = mean of those 10 m PCI values.
PCI_WEIGHTS = {'cracking_pct': 0.12, 'ravelling_area': 0.10, 'pothole_area': 0.16,
               'patching_area': 0.08, 'rutting_mm': 0.14, 'roughness_iri': 0.40}

LANE_TO_DIRECTION = {**{f'L{i}': 'Increasing' for i in range(1, 9)},
                     **{f'R{i}': 'Decreasing' for i in range(1, 9)}}

# ── Source order for point/area defects (first source that HAS data is used) ──
#   'defect_report' = D4 defect report 10 m columns
#   'nsv_cracks'    = D4 standalone NSV cracks sheet  (d4_nsv_cracks_cracks)
CRACKING_SOURCES = ['defect_report', 'nsv_cracks']
# Potholes: ONLY the D4 NHAI One pothole sheet (one POINT row per pothole; start = end chainage, length 0).
# No fallback: without an NHAI One sheet master_pothole is empty. rating_ca / rating_irc come from the
# D4 defect report 'Visual Condition - Pothole (CA)' / '(IRC)' of the pothole's lane + 10 m segment.

# ── D4 reading ───────────────────────────────────────────────────────────────
#   Some D4s give 'Visual Condition - Cracks (IRC)' / 'Pothole (IRC)' as a number 1–5 instead of text.
#   Such numbers are turned into these labels when D4 is read (text labels are kept as they are).
D4_VC_NUMBER_LABELS = {5: 'Very Good', 4: 'Good', 3: 'Fair', 2: 'Poor', 1: 'Very Poor'}
#   A D4 lane is used only when at least this share of the D4 rows has a value in that lane
#   (a lane with a few stray cells, e.g. 1 value in 2,810 rows, is skipped with a warning).
MIN_LANE_FILL = 0.01

# ── image names ──────────────────────────────────────────────────────────────
#   Cracking: ONLY the D4 crack image list (d4_images_crack_images.parquet; lane from the file name, e.g. '..._L1_...').
#   Each image goes to ONE 10 m cracking row of its lane — the first step that finds a row wins:
#     1) chainage (col H) inside the row's chainage range [start, end)
#     2) latitude / longitude (cols I, J = EXIF) within CRACK_IMAGE_TOLERANCE_M of the row's segment (nearest row)
#     3) ocr_chainage (col O) inside the row's range — only a plain number ('3:' is ignored)
#     4) ocr_latitude / ocr_longitude (cols P, Q; 'Lat:' inside ocr_longitude is read too) within the tolerance —
#        only values with ≥ 5 decimals ('28.57' is ignored)
#   Images left after the 4 steps are only counted in the run log (not a DQ / rule check).
#   image_name = file names, image_urls = image paths. 100 m / 1 km rows: all images of their 10 m rows.
#   Pothole: all distinct image names of the potholes on the same lane + chainage (NHAI One sheet).
#   Several names in one cell are joined with IMAGE_SEPARATOR; image_urls holds them as a list.
IMAGE_SEPARATOR = ', '
CRACK_IMAGE_TOLERANCE_M = 5.0

# ── master_pothole breach_flag / road_condition ──────────────────────────────
#   breach_flag = 0 when the pothole's SEVERITY starts with one of these words (case-insensitive), else 1.
#   Only the severity column is used (not type of defect). Blank severity → 1 (a recorded pothole).
#   road_condition comes ONLY from breach_flag: 1 → POTHOLE_ROAD_CONDITION[1], 0 → POTHOLE_ROAD_CONDITION[0].
POTHOLE_NO_BREACH_SEVERITY = ['low', 'delamination']
POTHOLE_ROAD_CONDITION = {1: 'Bad', 0: 'Good'}

# ── DQ checks against D2 ─────────────────────────────────────────────────────
DQ_PASS_PCT       = 98.0    # a check passes when at least this % of D2 rows match
DQ_TOLERANCE      = 0.01    # |D2 − silver| allowed for rut depth (mm), BI (mm/km), cracking (%)
# per-defect tolerance where D2 rounds: roughness BI is in mm/km (1,000–5,000), so 1 mm/km (< 0.1 %) forgives
# D2's rounding to whole numbers but still reports a real difference; every other defect uses DQ_TOLERANCE
DQ_TOLERANCE_BY_DEFECT = {'roughness': 1.0}
DQ_AREA_TOLERANCE = 0.01    # sq m, pothole area per lane per 100 m
DQ_POTHOLE_EXCLUDE_SEVERITY = ['delamination']   # D2 pothole rows not counted as potholes

# ── Roughness BI ─────────────────────────────────────────────────────────────
#   10 m : BI = 630 × IRI^1.12; IRI blank → D4 'Roughness BI' as it is; both blank → NULL (never IRI)
#   Threshold is BI mm/km (D2, else defect_rules.json). A D2 value in IRI m/km is converted to BI.
#   100 m / 1 km:
#     'iri_formula' → average the IRI, then 630 × IRI^1.12  (how D2 is built — matches D2; current choice)
#     'mean_bi'     → average of the BI values               (Core Columns sheet wording)
#   A block with no IRI at all but D4 BI values keeps the average of those BI values.
ROUGHNESS_AGGREGATION = 'iri_formula'

# ── 1 km (parent) averages ───────────────────────────────────────────────────
#   'from_10m'  → average of ALL the 10 m values in the km   (how D2 is built — matches D2; current choice)
#   'from_100m' → average of the 100 m averages in the km    (Core Columns sheet wording)
#   Only averages change (rut depth, IRI, BI). Ravelling sq m is a sum and gives the same result either way.
PARENT_AGGREGATION = 'from_10m'

# ── road_condition ───────────────────────────────────────────────────────────
#   Rutting / roughness / ravelling (every level, CA and IRC rows): ONLY from breach_flag —
#     1 → 'Poor', 0 → 'Good', blank metric_value or no D2 threshold → NULL. No D4 / severity / rating fallback.
#   Pothole: ONLY from breach_flag (see POTHOLE_ROAD_CONDITION).
#   Cracking only:
#     1) D4 'Visual Condition - <defect> (CA)/(IRC)' (CA rows CA label, IRC rows IRC label);
#        100 m / 1 km blocks take the WORST 10 m label
#     2) no D4 label: CA rows Good/Bad from breach, IRC rows blank (NULL when the % is blank or no D2 threshold)
#     (D3 is not used anywhere)
#   Order used to pick the worst label (D4 cracking / pothole) is below.
CONDITION_SEVERITY_ORDER = ['very poor', 'poor', 'fair', 'good', 'very good']   # worst first

# ── dlm_threshold_mapping.deliverable_path ───────────────────────────────────
# S3 root of the bronze NSV output. deliverable_path = <root>/zone=<z>/phase=<p>/cycle=<c>/<UPC>
# (e.g. s3://dhms-new-stgae/nsv-extraction/bronze/nsv/zone=A/phase=2/cycle=1/N-02007-11001-HR).
# Set to None to write the project folder the run read instead.
BRONZE_S3_ROOT = 's3://dhms-new-stgae/nsv-extraction/bronze/nsv'
# S3 location of the silver output (for reference). The code writes to OUTPUT_ROOT on the local / mounted disk;
# that folder is what goes to this S3 prefix (same zone=/phase=/cycle=/<UPC> layout).
SILVER_S3_ROOT = 's3://dhms-new-stgae/nsv-silver'
