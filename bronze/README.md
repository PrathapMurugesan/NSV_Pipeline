# NSV extraction

Extracts the NSV survey deliverables (D1-D4) **exactly as delivered** into parquet + csv, and writes
JSON files describing the data (profile, formulas, legends, report sheets). Rules: see `DECISIONS.md`.

## Setup
```
pip install -r requirements.txt
```
Raw data goes under `data/` in this layout (defined in `settings.py`, `RAW_LAYOUT`):
```
data/nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d1/   VisualCondition.xlsx, LRMS..., RIS...
                                                 /d2/   Comparison as per CA ....xlsm
                                                 /d3/   <any sub-folders>/*.csv
                                                 /d4/   CRRI_PCI..., Defect Report..., ...
```
zone A-E, phase 1-3, cycle 1-2, UPC with '/' written as '-'. Other folders are reported and ignored.

## Run
```
python main.py                                  every deliverable of every survey found
python main.py d2 d4                            only these deliverables
python main.py --zone A                         only zone A (filters combine: --zone A B --phase 1 --cycle 2)
python main.py --upc N/08004/03001/HR           one survey
python main.py --list                           show what would run, extract nothing
```
Surveys run one after another; a crash in one is logged and the loop continues.
A deliverable folder that is not there = "not_delivered" (not an error).
Exit code 0 = all completed, 2 = something rejected / missing / crashed (see the batch summary).

## Folder structure
```
main.py                 entry point
settings.py             storage (local / S3) + switches + thresholds      <- edit here
deliverables/d1..d4.py  WHAT to extract (workbooks, sheets, header layout) + one block per workbook
schemas/                expected headers per sheet (csv) = the header check
nsv/                    the engine
    storage.py          local disk / S3, same 3 methods
    excel.py            read sheets: header detection, merged cells, column ranges, formulas
    matching.py         find workbooks / sheets by name (exact, normalized, contained, fuzzy)
    headers.py          header matching (exact / normalized / unit / alias) + saved names without units
    checks.py           header check (gate) + empty / defect-column / lane reports
    profiler.py         profile.json
    formulas.py         formulas.json
    pipeline.py         the steps, in order
    survey.py           zone/phase/cycle/UPC -> raw and bronze paths; find all surveys
    images.py           survey images: EXIF + OCR by label + checks, in parallel
    intervals.py        master table of reporting intervals, one row per UPC (run_intervals.py runs only this)
    run.py              one run = one output folder + manifest + run.log
    writer.py           parquet / csv / json bytes
tools/make_schema.py    create schemas/ from a template file
data/nsv/               raw input (local mode)
data/bronze/nsv/        extracted output (local mode)
```

## Output
Bronze mirrors the raw path, one timestamped folder per run (nothing is ever overwritten):
```
data/bronze/nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2/
    run=20261021_093122/
        d2_comparison_comparison_ca.parquet + .csv     data exactly as delivered (all text) + source_row
        meta/d2_comparison_schema.json                 per column: saved name, vendor header, match type, units
        meta/d2_comparison_profile.json                per column: types found, empties, min/max, examples
        meta/d2_comparison_formulas.json               per calculated column: formula, readable form, deviations
        meta/d2_comparison_data_dictionary.json        legends / code meanings from the header rows
        meta/d2_comparison_<sheet>_cells.json          report sheets / side tables, cell by cell
        d2_manifest.json                               survey, files (sha256), sheets found / missing / rejected, checks
        d2_run.log
    d2_latest.json                                     newest run of this deliverable
data/bronze/nsv/_batches/batch=<timestamp>.json        one line per survey x deliverable of a loop
data/bronze/nsv/_batches/batch=<timestamp>_report.csv  ONE ROW PER SHEET: status, reason, notes  <- start here
data/master/reporting_intervals.parquet + .csv         ONE ROW PER UPC: Rutting / Roughness / Ravelling / Cracking
                                                       reporting interval (m) as per IRC and CA (nsv/intervals.py)
```
The master table is updated after every run with d2 (a rerun UPC replaces its row). Only that step, from what is
already extracted: `python run_intervals.py` (or `--upc N/02007/11001/HR`). Switch off: `export NSV_MASTER_INTERVALS=0`.

### How headers are checked
Every sheet's headers are compared with `schemas/<dN>/<workbook>/<sheet>.csv`, strictest first:

| match | example (file -> schema) | accepted |
|---|---|---|
| exact | `Chainage from (km)` -> `Chainage from (km)` | yes |
| normalized | `Scheme Name` -> `SchemeName` (case, spaces, `_ - .` ignored) | yes, warning |
| unit | `Chainage from (m)` -> `Chainage from (km)` (same name, same kind of unit, or no unit) | yes, warning |
| alias | `NH No.` -> `NH Number` (listed in the schema csv column `aliases`, separated by `\|`) | yes, warning |
| position | column CC `Raveling Lane Area (sq. m) \| L4` -> `Raveling Lane Area (%) @ 10 mtr \| L4` (schema column CC): header differs, but same place in the sheet **and** same kind of values (number / text / date) | yes, WARNING "expected X, got Y"; report status `saved_headers_differ` |
| similar only | `Chainage Frm` -> `Chainage from` | **no** - hint in the log; add an alias if it is the same column |

The position match needs `excel_column` and `value_type` in the schema csv - `tools/make_schema.py` writes both
(the D1 schemas were converted from the old config and have neither: regenerate them from a real D1 delivery with
`python tools/make_schema.py d1 --force` to enable it; aliases you added are kept).

Saved column names = the schema name **without the unit**: `Chainage from`, `Length`, `Current Cycle | Roughness BI | L1`.
Units (in the file / in the schema), the vendor's original header and how it was matched are in
`meta/<dN>_<workbook>_schema.json`. If two columns would get the same name without units, they keep their unit.

### Survey images (D4)
Image folders inside `d4/` are optional and found by keyword (`deliverables/d4.py`, `IMAGE_FOLDERS`):
a folder with "crack" in its name = crack images, "pothole" = pothole images; lanes from sub-folders `L1`..`R4`.
One row per image in `d4_images_<group>.parquet + .csv`:

| columns | content |
|---|---|
| `image_folder`, `lane`, `file_name`, `image_path` | where the image is (`image_path` opens it directly) |
| `status` | `ok` / `partial` (some fields missing or a check failed) / `exif_only` / `no_text_found` / `unreadable_file` |
| `issues` | why: e.g. "chainage 345700 differs from file name (345710)", "folder lane L2 but text says L1", lat/long outside India |
| `survey_id` ... `speed` | standard fields read from the text on the image (`IMAGE_FIELDS` in `settings.py`) |
| `label_<name>` | any other "Label: value" a vendor prints |
| `exif_*` | EXIF date / GPS / camera, if the vendor embedded it |
| `ocr_text` | the raw text read, for checking |

Text is read with OCR (tesseract), by **label** - not by position - so other vendors' layouts work too; new
label wordings go in `IMAGE_FIELDS`. ~0.6 s per image per CPU core, all cores in parallel.
**Windows:** install tesseract from https://github.com/UB-Mannheim/tesseract/wiki and set `TESSERACT_CMD`
in `settings.py`. Without it, images are still listed (path, lane, EXIF) and the report says OCR is missing.
A missing optional image folder is listed under "optional folders not delivered" at the end of every run.

### Which files / sheets were rejected?
Open `batch=<timestamp>_report.csv` (Excel) and filter the `status` column. Upper-case = a problem:

| status | meaning |
|---|---|
| `REJECTED_HEADERS` | headers differ from the schema - NOT saved; `reason` lists the missing headers |
| `SHEET_MISSING` | expected sheet not in the workbook |
| `WORKBOOK_MISSING` | expected workbook not in the folder (the other workbooks of that deliverable show `not_extracted`) |
| `READ_ERROR` / `CRASHED` | file could not be read / run stopped with an error |
| `saved_headers_differ` | saved; some headers differ but the column in the same place holds the same kind of values - `reason` says expected / got |
| `saved` | fine - `notes` may still say e.g. "no data rows", "1000 formula-only rows", "3 defect columns completely empty" |
| `not_delivered` | survey has no folder for this deliverable |
| `images_saved` | an image folder was read; `notes` = status counts (ok / partial / ...) |
| `optional_missing` | an optional folder (e.g. pothole images) was not delivered - highlighted, not an error |
| `IMAGES_MISSING` | a required image folder was not delivered |

The same problems are printed at the end of every run.
File names: `<deliverable>_<workbook key>_<sheet key>` - stable keys from `deliverables/dN.py`, not the
vendor's file name (that changes every month; it is recorded in the manifest).

## Common changes
| Situation | What to do |
|---|---|
| A header is rejected but it is the same column under another name | add the vendor's name to `aliases` in the schema csv |
| A workbook's name changes with every survey (e.g. D2: month + road name) | in `deliverables/dN.py` set `file_keywords` (words the name always contains) and `identify_by_sheets` (sheets it always has) - used when no name matches; D2 already has `["comparison"]` + the two Comparison sheets |
| Vendor renamed a file or sheet and it is not recognised | add the new name to `file_names` / `sheet_names` in `deliverables/dN.py` |
| New sheet to extract | add it to `sheets` in `deliverables/dN.py`, then `python tools/make_schema.py dN` |
| Template changed (new / renamed columns) | `python tools/make_schema.py dN --force`, review the csv files in `schemas/dN/`, commit |
| Multi-row header, side tables, notes rows | sheet options `header_row`, `header_rows`, `info_rows`, `skip_rows`, `columns`, `cells` (see `nsv/pipeline.py`) |
| New vendor layout, just want to see what is in it | `EXTRACT_ALL = True` in `settings.py` |
| Move to S3 | `pip install boto3`; in `settings.py` comment the two `LocalStorage` lines and uncomment the `S3Storage` lines (bucket, prefix, region). Credentials from the usual AWS config / IAM role. |
| Real folder names differ (e.g. `upc=N-08...`, `Deliverable 2`) | change `RAW_LAYOUT` / `BRONZE_LAYOUT` / `DELIVERABLES` in `settings.py` |
| Regenerate schemas from a specific survey | `python tools/make_schema.py d2 --upc N-08004-03001-HR --force` |
