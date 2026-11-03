"""
settings.py - the ONE file you edit to change how the pipeline behaves.

What each deliverable contains (workbooks, sheets, header layout) lives in deliverables/d1..d4.py.
Expected column headers live in schemas/<deliverable>/<workbook>/<sheet>.csv.
"""
import os
from pathlib import Path

from nsv.storage import LocalStorage, S3Storage  # noqa: F401  (S3Storage is used when you switch to S3)

PROJECT_ROOT = Path(__file__).resolve().parent


# =============================================================================
# 1. WHERE FILES COME FROM AND WHERE RESULTS GO
#    Raw data and bronze output live under the same root today (local folder "data").
#    To use S3, comment the Local lines and uncomment the S3 lines.
# =============================================================================
INPUT = LocalStorage(PROJECT_ROOT / "data")
# INPUT = S3Storage(bucket="your-nhai-bucket", prefix="", region_name="ap-south-1")

OUTPUT = LocalStorage(PROJECT_ROOT / "data")
# OUTPUT = S3Storage(bucket="your-nhai-bucket", prefix="", region_name="ap-south-1")

# Or without editing this file:  export NSV_OUTPUT=s3://bucket/prefix   (or a local folder)
if os.environ.get("NSV_OUTPUT", "").startswith("s3://"):
    _bucket, _, _prefix = os.environ["NSV_OUTPUT"][len("s3://"):].partition("/")
    OUTPUT = S3Storage(bucket=_bucket, prefix=_prefix)
elif os.environ.get("NSV_OUTPUT"):
    OUTPUT = LocalStorage(Path(os.environ["NSV_OUTPUT"]))

SCHEMAS_DIR = PROJECT_ROOT / "schemas"          # expected headers (part of the code, always local)


# =============================================================================
# 2. FOLDER LAYOUT
#    {zone} {phase} {cycle} {upc} {deliverable} {run_id} are filled in per survey and run.
#    {upc} is the UPC as used in folder names: '/' replaced by '-'  (N/08004/03001/HR -> N-08004-03001-HR)
#
#    raw:     nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2/<files as downloaded>
#    bronze:  bronze/nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2/run=20261020_101500/<extracted files>
#
#    If your real folders look different (e.g. "upc=N-08..." or "Deliverable 2"), change only these lines.
# =============================================================================
RAW_LAYOUT = "nsv/zone={zone}/phase={phase}/cycle={cycle}/{upc}/{deliverable}"
BRONZE_LAYOUT = "bronze/nsv/zone={zone}/phase={phase}/cycle={cycle}/{upc}/{deliverable}"
RUN_FOLDER = "run={run_id}"                      # one folder per extraction run, inside the bronze folder
BATCH_FOLDER = "bronze/nsv/_batches"             # summary of every loop over several surveys
MASTER_FOLDER = "master"                         # master tables over all UPCs (master/reporting_intervals.parquet)

ZONES = ["A", "B", "C", "D", "E"]                # allowed values - other folders are reported and ignored
PHASES = ["1", "2", "3"]
CYCLES = ["1", "2", "3"]          # Cycle_3 exists in the download (e.g. Zone_C/Phase_1/Cycle_3)
DELIVERABLES = ["d1", "d2", "d3", "d4"]          # deliverable folder names inside a UPC folder


# =============================================================================
# 3. SWITCHES
# =============================================================================
CHECK_HEADERS = True             # True = a sheet whose headers differ from its schema CSV is rejected (not saved)
if os.environ.get("NSV_CHECK_HEADERS", "").lower() in ("0", "false", "no"):
    CHECK_HEADERS = False              # export NSV_CHECK_HEADERS=0 : saved anyway, with the vendor's headers;
                                       # the missing ones are listed in the report (saved_headers_missing)
IGNORE_FILES = ["failed_files*.xlsx"]   # vendor tool output, not survey data (file name patterns, * = anything)
EXTRACT_UNMATCHED = True         # True = sheets / Excel files that are not in the deliverable config are saved too
                                 #        (header found automatically; report status saved_not_in_config)
if os.environ.get("NSV_EXTRACT_UNMATCHED", "").lower() in ("0", "false", "no"):
    EXTRACT_UNMATCHED = False
STOP_IF_WORKBOOK_MISSING = True  # True = if an expected workbook is not found, the deliverable is not extracted
if os.environ.get("NSV_STOP_IF_WORKBOOK_MISSING", "").lower() in ("0", "false", "no"):
    STOP_IF_WORKBOOK_MISSING = False   # export NSV_STOP_IF_WORKBOOK_MISSING=0 : extract the workbooks that are there
EXTRACT_ALL = False              # True = ignore the deliverable config: read every workbook and every sheet,
                                 #        header found automatically, no header check (for a new vendor layout)
if os.environ.get("NSV_EXTRACT_ALL", "").lower() in ("1", "true", "yes"):
    EXTRACT_ALL = True                 # export NSV_EXTRACT_ALL=1 : for one run, e.g. python run_download.py d2 ...
MASTER_INTERVALS = True          # True = after a run with d2, its UPCs are added to master/reporting_intervals
if os.environ.get("NSV_MASTER_INTERVALS", "").lower() in ("0", "false", "no"):
    MASTER_INTERVALS = False           # run_status_csv.py sets this for its parallel runs and updates once at the end


# =============================================================================
# 4. MATCHING FILE / SHEET NAMES
# =============================================================================
FUZZY_THRESHOLD = 0.85           # 0-1: how similar a file or sheet name must be to an expected name
HEADER_SCAN_ROWS = 20            # automatic header detection looks at this many top rows


# =============================================================================
# 5. DEFECT-COLUMN NULL REPORT (D2, D4) - REPORT ONLY, a sheet is never skipped
#    A defect column = its name contains one of DEFECT_KEYWORDS.
#    It is reported when completely empty, or more than NULL_LIMIT % empty.
# =============================================================================
NULL_LIMIT = 25
DEFECT_KEYWORDS = [
    "roughness", "rut", "crack", "ravelling", "raveling", "distress", "depth", "area", "severity",
    "chainage", "pothole", "defect", "lane", "carriageway", "image", "latitude", "longitude",
    "pci", "condition", "bi ", "iri",
]
NULL_IGNORE = ["remark", "structure", "limitation"]   # naturally sparse text / contract limits: not reported


# =============================================================================
# 6. PROFILE JSON
# =============================================================================
PROFILE_EXAMPLES = 3             # example values stored per column
PROFILE_MAX_DISTINCT = 1000      # distinct values are counted up to this number


# =============================================================================
# 8. SURVEY IMAGES (e.g. d4/crack_images_DHMS/L1/*.jpg)
#    Text printed on the images is read with OCR (tesseract). Tesseract is a separate program:
#      Windows: install from https://github.com/UB-Mannheim/tesseract/wiki and set TESSERACT_CMD below
#      Linux:   apt install tesseract-ocr
#    Without it, images are still listed (file name, path, lane, EXIF) and the report says OCR is missing.
# =============================================================================
import os as _os

OCR_IMAGES = True
IMAGE_OCR = "when_no_gps"        # "when_no_gps" = tesseract only for images without usable EXIF GPS lat/long
                                 # "always"      = tesseract on every image (banner fields: survey id, speed, ...)
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe" if _os.name == "nt" else None   # None = found on PATH (Linux)
IMAGE_WORKERS = _os.cpu_count() or 1        # CPU cores used in parallel (~0.6 s per image per core)
if _os.environ.get("NSV_IMAGE_WORKERS"):    # export NSV_IMAGE_WORKERS=40 (run_status_csv.py sets it per UPC)
    IMAGE_WORKERS = int(_os.environ["NSV_IMAGE_WORKERS"])
# one thread per tesseract: with many workers, tesseract's own threads make OCR many times slower
_os.environ.setdefault("OMP_THREAD_LIMIT", "1")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")

# Where the text is, as a fraction of the image height: (name, from, to). Add e.g. ("bottom", 0.9, 1.0)
# for a vendor with a bottom banner. Text drawn on top of the pavement picture is not reliable to read.
IMAGE_TEXT_REGIONS = [("top banner", 0.0, 0.17)]

# Image location: chainage from the file name, lat/long from EXIF GPS / Defect Report / OCR (see nsv/images.py).
# OCR lat/long further than this from the chosen location -> flagged in 'issues' (correct reads agree to < 1 m).
IMAGE_LOCATION_AGREE_M = 10

# Standard name <- labels as printed by vendors (case / spaces / symbols ignored) + check.
# Labels not listed here are still extracted, as columns 'label_<their name>'.
IMAGE_FIELDS = {
    "survey_id":    {"labels": ["Survey ID", "SurveyID", "Survey Id"]},
    "description":  {"labels": ["Description", "Section", "Road"]},
    "direction":    {"labels": ["Direction"]},
    "date":         {"labels": ["Date", "Survey Date", "Captured On"], "type": "date"},
    "survey_start": {"labels": ["Survey Start", "Start"], "type": "number"},
    "frame_no":     {"labels": ["Frame No", "Frame", "Frame Number"], "type": "number"},
    "latitude":     {"labels": ["Latitude", "Lat", "GPS Lat"], "type": "number", "range": (6, 38)},
    "longitude":    {"labels": ["Longitude", "Long", "Lon", "GPS Long"], "type": "number", "range": (68, 98)},
    "altitude":     {"labels": ["Altitude", "Alt"], "type": "number"},
    "distance":     {"labels": ["Distance"], "type": "number"},
    "chainage":     {"labels": ["Chainage KM", "Chainage", "Chainage (m)", "Ch"], "type": "number"},
    "speed":        {"labels": ["Speed Km/hr", "Speed", "Speed (km/h)"], "type": "number", "range": (0, 150)},
}
