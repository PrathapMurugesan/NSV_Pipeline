# Decisions

Why the code behaves the way it does. Add a line whenever a rule changes.

| # | Topic | Decision |
|---|---|---|
| 1 | Goal | Extract every sheet **exactly as delivered**. Everything we learn about the data goes into JSON files for later reference. Nothing is recalculated, converted or "fixed". |
| 2 | Raw values | Saved as text (parquet schema: all `string`), with `source_row` = Excel row number. Types / units are described in `profile.json`, not applied. |
| 3 | Calculated columns | Values extracted as the workbook shows them. Formulas documented in `formulas.json` (pattern, readable form, rows where the formula differs). |
| 4 | Header check | The ONLY gate: a sheet missing an expected header is rejected (not saved), because a different layout means wrong columns. Expected headers: `schemas/<deliverable>/<workbook>/<sheet>.csv`, generated from a template with `tools/make_schema.py`. Line breaks / spaces ignored; spelling and case must match. |
| 4a | Header matching | Tiers: exact, normalized (case/spaces/punctuation), unit (same name, same kind of unit or none), alias (schema csv), position (header differs, but same Excel column as the template and same kind of values -> accepted with WARNING 'expected X, got Y', report status saved_headers_differ; different kind of values -> rejected). Similar-only names are never accepted (near-twins like Chainage from / To) - hint in the log only. Every non-exact match is a warning and is recorded. A unit of a different kind (e.g. % vs m) is not a match. |
| 4b | Saved column names | Schema name without its unit (`Chainage from`, `Length`); columns not in the schema keep the vendor name without unit. Units in file / schema, vendor header and match type go to `<dN>_<workbook>_schema.json`. Values are never converted. If names collide without units, the unit stays in the name. |
| 5 | Empty sheets, empty / sparse defect columns, unsurveyed lanes | **Reported, never skipped** (log + manifest). The sheet is saved anyway. |
| 6 | Missing data | If it is not there, it is not there - no filling, no defaults. |
| 7 | Multi-row headers | Only cells inside a real merged range are filled (left-right and top-down). Blank, non-merged cells stay blank. |
| 8 | Legend / description rows in headers | (`info_rows`) not part of column names; saved to `data_dictionary.json`. E.g. D2 thresholds row, Defect Report `Good/Poor` row, NHAI One code descriptions. |
| 9 | Side tables / dropdown lists next to a table | Excluded with `columns: "A:G"`; side areas saved cell by cell with `cells: "I:M"` when they carry information. |
| 10 | Report sheets (D2 Summary, Inputs (Thresholds)) | Saved cell by cell as JSON (`kind: "cells"`). |
| 11 | D2 Validation Checks | Extracted as a table (`I:P`) + left blocks as cells (`A:G`). No action taken on its PASS/FAIL. |
| 12 | D2 Instructions Guide, Remarks Dictionary; D1 Table List | Not extracted (`skip: True`). |
| 13 | CRRI formula-only rows (unfilled template rows) | Saved as-is; counted in `profile.json` (`formula_only_rows`) and warned in the log. |
| 13a | Workbook identification | Files directly in the deliverable folder first, then files in its sub-folders. 1) By file name: exact / normalized / one name contains the other (e.g. 'Jun-25 Comparison as per CA &lt;road&gt;') / fuzzy. 2) `file_keywords` + `identify_by_sheets`: the one file whose name contains the keywords AND has those sheets. 3) The one left-over file that has all the workbook's sheets. 4) The one left-over file whose name contains the `file_keywords` (sheets that differ then show as SHEET_MISSING). Two candidates in a step = ambiguous, nothing guessed. How it was identified is in the manifest (`identified_by`) and the summary notes; a missing workbook's reason names the closest file. |
| 14 | Missing workbook | Deliverable not extracted (`STOP_IF_WORKBOOK_MISSING = True`). |
| 15 | Output layout | Bronze mirrors raw: `bronze/nsv/zone=/phase=/cycle=/<UPC>/<dN>/run=<timestamp>/`. Nothing deleted or overwritten; `<dN>_latest.json` points to the newest run. |
| 15a | Raw layout | `nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2/` (Hive-style zone/phase/cycle; UPC '/' -> '-'). Zones A-E, phases 1-3, cycles 1-2; other folders reported and ignored. |
| 15b | File names | `<deliverable>_<workbook key>_<sheet key>` (stable keys, not the vendor file name). |
| 15c | Many surveys | Run one after another; a crash or missing workbook affects only that survey/deliverable; missing deliverable folder = not_delivered; batch summary per loop. |
| 16 | Storage | All reads / writes through `nsv/storage.py`. Local today; S3 by switching two lines in `settings.py`. |
| 17 | Survey images | Optional folders in d4 found by keyword (crack / pothole); lane from L1..R4 sub-folders. One row per image: path, lane, EXIF (if any), every "Label: value" read by OCR from the top banner (by label, not position), checks (ranges, date, file name vs text, lane folder vs text). Nothing guessed: unread = empty + raw OCR text kept. Text drawn over the pavement picture (e.g. rutting) is not read - not reliable. Missing optional folder = highlighted in the summary, not an error. |
| 17a | Image location | `chainage` = number in the file name (`<run>_lcms_<n>`), never OCR (OCR only when the name has none). `latitude/longitude` = EXIF GPS, else Defect Report row of that lane + chainage (R2 delivered in reverse order is reversed for this lookup only), else OCR banner. OCR values kept as `ocr_*`; OCR lat/long more than `IMAGE_LOCATION_AGREE_M` from the chosen point is flagged (OCR misreads digits, ~10% of images, up to 466 m off). No NSV Cracks offset on the lookup: checked on N-02017-03001-HR, the Defect Report matches the file number to < 1 m and the offset would put every image 140-200 m off. It is kept as `nsv_cracks_chainage` to join with NSV Cracks. |
