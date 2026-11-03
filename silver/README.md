# NSV Silver Layer — New Structure (deliverables only)

**Version 7** — changes since v6 (all in the code, branch `main`):
- No D4 row is dropped because a value is blank; blanks stay NULL (metric_value NULL, breach_flag 0, road_condition NULL).
- Roughness BI = 630 × IRI^1.12; IRI blank → D4 `Roughness BI @ 10 mtr | <lane>`; both blank → NULL (never IRI in its place).
- Roughness threshold from D2 in BI mm/km; a D2 value given as IRI (unit m/km or < 100) is converted to BI with a warning.
- Ravelling: D4 *Visual Condition - Ravelling (CA)/(IRC)* used for severity / ratings at 10 m and never mistaken for the area column;
  100 m / 1 km % = total sq m × 100 ÷ 350 / 3,500.
- PCI: roughness input IRI; blanks / chainage gaps not counted (100 m / 1 km = mean of the 10 m PCI values that exist).
- Blank end chainage → start + 10 m with a warning, never a crash.
- Matched to D2: roughness 100 m / 1 km = 630 × (average IRI)^1.12 (`ROUGHNESS_AGGREGATION = 'iri_formula'`);
  1 km averages = average of all 10 m values in the km (`PARENT_AGGREGATION = 'from_10m'`). Both are switches in config.py
  (the Core Columns sheet wording is `'mean_bi'` / `'from_100m'`).
- Pothole: breach_flag = 0 for severity Low or Delamination (`config.POTHOLE_NO_BREACH_SEVERITY`; severity column only),
  else 1; road_condition only from breach_flag (Bad / Good, `config.POTHOLE_ROAD_CONDITION`); affected_length = the
  pothole area as read, whatever breach_flag is. The pothole DQ check leaves Delamination out on the silver side too (as it does for D2).
- D4 Visual Condition given as a number (e.g. Cracks / Pothole (IRC) 1–5) → label: 5 Very Good, 4 Good, 3 Fair,
  2 Poor, 1 Very Poor (`config.D4_VC_NUMBER_LABELS`).
- A D4 lane with values in fewer than 1 % of the rows (`config.MIN_LANE_FILL`) is skipped with a warning
  (e.g. a single stray L4 cell no longer creates 2,810 empty L4 rows).
- Cracking (defect report): no row is dropped for a blank %; blank % → metric NULL, breach_flag 0, and the
  breach fallback of road_condition gives NULL (a D4 label is still used when there is one).
- Cracking at three levels, named like the other defects: `master_cracking` (10 m), `master_cracking_child_100m`
  (100 m) and `master_cracking_parent` (1 km). 100 m / 1 km are built from the same 10 m cracking rows:
  cracking % = average of the 10 m % (1 km per `config.PARENT_AGGREGATION`), area_sqm = total; breach, severity
  and ratings from the aggregated %; road_condition = worst D4 label in the block → breach.
- DQ: roughness BI tolerance 1 mm/km (`config.DQ_TOLERANCE_BY_DEFECT`, forgives D2 rounding, still reports real
  differences); cracking compared with the D2 100 m crack % three ways — master_cracking 10 m rows averaged per
  100 m, master_cracking_child_100m directly, master_cracking_parent vs the average of the D2 100 m values of the
  km; the CA cracking breach flags are compared with master_cracking_child_100m.
- Images (cracking): ONLY from the D4 crack image list `d4_images_crack_images` (parquet preferred over csv):
  file_name → image_name, image_path → image_urls; lane from the file name (`..._L1_...`). Each image goes to ONE
  10 m cracking row of its lane, first step that finds a row wins: 1) `chainage` inside the row's range [start, end);
  2) `latitude` / `longitude` (EXIF) within 5 m of the row's segment (`config.CRACK_IMAGE_TOLERANCE_M`, nearest row);
  3) `ocr_chainage` inside the row's range (plain numbers only); 4) `ocr_latitude` / `ocr_longitude` within 5 m
  (≥ 5 decimals; 'Lat:' inside ocr_longitude is read too). Images left over are only counted in the run log.
  100 m and 1 km cracking rows list every image of their 10 m rows. Several
  images → comma separated (`config.IMAGE_SEPARATOR`). No image list in D4 → cracking images empty (the NSV Cracks
  sheet is no longer used for images).
- Images (pothole, unchanged): NHAI One 'Image Name'; all distinct names of the potholes on the same lane + chainage,
  comma separated.
- Thresholds ONLY from D2 — `threshold_defaults` removed from defect_rules.json and config. No D2 threshold →
  threshold_value, breach_flag, road_condition and calculated severity / ratings NULL (a D4 Visual Condition label is
  still used), and the new DQ `threshold` check FAILs for that defect + standard. The '>=' operator is still used when
  D2 gives a threshold without an operator.
- master_pothole: NHAI One sheet only (defect-report fallback removed); rating_ca / rating_irc from the D4 defect
  report 'Visual Condition - Pothole (CA)' / '(IRC)' of the lane + 10 m segment (NULL when absent, never the
  severity); threshold_value NULL (D2 has no pothole threshold).
- IDs aligned with the DLM notebook: parent_id = 1 km block at every level; defect_id date = first NHAI One
  `Datetime`; cracking 100 m / 1 km defect_id empty; display_defect_id = the notebook's hierarchical ids
  (common/display_ids.py, run after all tables; the counter continues after other UPCs in the output folder).
- New table `master_reporting_interval`: one row per D2 comparison sheet that exists (standard CA = 'Comparison as
  per CA', IRC = 'Comparison as per IRC'): overall / roughness / rutting / cracking / ravelling reporting interval
  from the CURRENT CYCLE columns only (most common value of each column; no column or no value → NULL).
- Cracking severity now follows the same rule as rutting / roughness / ravelling (finalize.apply_severity): 10 m =
  D4 'Visual Condition - Cracks' of the row's standard (CA row → CA label, IRC row → IRC label), else calculated;
  100 m / 1 km = calculated from the aggregated % with the row's standard (severity_ranges.cracking × that
  standard's D2 threshold). Before, every cracking row (CA and IRC) got the label calculated with the CA threshold.
- DQ: new `failing_lanes` check for CA and IRC (D2 'Failing Lanes' Yes / No vs any lane breaching in the silver
  100 m / 1 km table of that standard) — the D2 IRC sheet has no per-lane breach flags, so this is the IRC breach
  check. A missing D2 comparison sheet now shows as a `d2_sheet` NOT RUN row.
- DQ: IRC breach per lane — derived from the D2 IRC lane value vs the D2 IRC threshold / operator (the D2 IRC sheet
  has lane values but no per-lane 0/1 flags) and compared with the silver IRC breach_flag, like the CA flag check.
- New table `dlm_threshold_mapping` (one row per defect): CA / IRC thresholds from D2 with their source
  ('D2 Comparison as per CA (…)' / 'D2 Comparison as per IRC (…)', no IRC fallback), IRC bands as the code classifies
  (rating_ranges IRC × D2 IRC threshold), D4 Visual Condition labels (CA and IRC; D3 is not used),
  CA and IRC reporting interval from the D2 comparison sheets (current cycle), number_of_lanes and total_length_km
  from D1 LRMS SectionMaster (else D4), lane_length_km from D4 (surveyed 10 m rows summed over lanes), pavement type
  (tracker, else D1), tracker_matched, deliverable_path (S3 bronze path when `config.BRONZE_S3_ROOT` is set) and
  updated_at.
- Tracker read by COLUMN NAME (sheet 'Zone <zone>', header row 2), not by position: UPC = 'UPC code as per O&M
  master' (else 'UPC as per survey conducted', exact match, a cell may list several UPCs), Phase, Cycle, Actual Survey
  Start / End Date, Type of pavement, PCI, Number of lanes, Total Length (km), Lane Length (km), and per defect the
  'Reporting as per CA/IRC' column just before 'Ravelling' / 'Rutting' / 'Cracks' / 'Roughness'. The tracker file is
  found automatically: the newest NSV_Activity_Tracker*.xlsx in the code folder.
- All reporting intervals from D2 (current cycle, per row's standard); the tracker interval is no longer used.
- dlm_threshold_mapping: number_of_lanes / total_length_km / lane_length_km from the tracker first, then D1, then D4.
- D3 (GIS Mapping) removed: it was only the 2nd step of the cracking road_condition (after D4, before the breach
  fallback). The d3 folder is no longer read.
- Comments, README and rule checks updated to these rules.
- Checked on N-02017-03001-RJ: all 47,544 D4 lane-rows kept in every rutting / roughness / ravelling 10 m table
  (v6 dropped 2,522 / 2,646 / 2,518); rule checks 181/181 PASS; DQ vs D2 28/28 PASS (v6: 16/28) —
  roughness value 98.3–99.0 %, roughness breach 99.9–100 %, CA rutting 1 km 98.3–99.2 %, IRC rutting 99.8–100 %,
  potholes 100 %. The remaining differences are blocks where D4 has blank 10 m values but D2 has a value
  (D2 is not built from those D4 rows there), plus a few hand-edited D2 blocks (bridges, diversions).

Builds the NSV **silver tables** for one project (UPC) from the deliverable folders **D1–D4**,
the **NSV Activity Tracker**, and (optionally) the **schema workbook**.
The old pipeline also needed backup lane files; this one doesn't, because all 10 m values now come from **D4**.

Each table has its own script. `main.py` runs them all in the right order.

---

## 1. Folder structure

```
nsv_silver/
├── main.py                  ← runs every table + the DQ check, for one UPC or many (--root)
├── config.py                ← paths, source order, DQ limits (edit this first)
├── defect_rules.json        ← Good / Fair / Poor ranges per defect, CA and IRC (thresholds only from D2)
├── requirements.txt
├── common/                  ← shared logic (8 files)
│   ├── utils.py             ← small helpers: column names, numbers, chainage '677+100', NH number
│   ├── project.py           ← one UPC: finds files (by name, else by columns), reads D1–D4, tracker,
│   │                          D2 threshold JSON, thresholds, survey date, reporting interval
│   ├── road.py              ← nh_number, section_code, pavement_type, carriageway from D1
│   ├── defects.py           ← WHERE each defect comes from (+ fallbacks) and the Good/Fair/Poor labels
│   ├── segments.py          ← 10 m / 100 m / 1 km rows, ids, parent block, CA + IRC twin rows
│   ├── finalize.py          ← PCI, road_condition, rating_ca / rating_irc, schema, column order
│   ├── display_ids.py       ← display_defect_id hierarchy over all tables of a UPC (DLM notebook)
│   └── output.py            ← write parquet / Excel / CSV, run a table, command-line options
├── tables/                  ← ONE FILE PER OUTPUT TABLE (16)
│   ├── master_rutting_child_10m.py     master_rutting_child_100m.py     master_rutting_parent.py
│   ├── master_roughness_child_10m.py   master_roughness_child_100m.py   master_roughness_parent.py
│   ├── master_ravelling_child_10m.py   master_ravelling_child_100m.py   master_ravelling_parent.py
│   ├── master_cracking.py   master_cracking_child_100m.py   master_cracking_parent.py
│   ├── master_pothole.py    master_chainage.py   master_reporting_interval.py
│   ├── dlm_threshold_mapping.py
├── validation/
│   ├── dq_checks.py         ← DQ: D2 thresholds present + how many D2 rows the silver output reproduces (PASS ≥ 98 %)
│   └── rule_checks.py       ← every generated value recomputed independently from D4 and compared
└── docs/
    └── NSV_Silver_Column_Lineage.xlsx  ← every column: meaning, source file, source column, calculation
```

## 2. Setup (once)

```bash
cd nsv_silver
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
```

Python 3.10 or newer is required.

---

## 3. Set your paths in `config.py`

```python
PROJECT_DIR  = r"D:\NSV\New_Structure_Data\zone=B\zone=B\phase=3\cycle=1\N-02017-03001-RJ"
TRACKER_PATH = <newest NSV_Activity_Tracker*.xlsx in the code folder>   # else r"D:\NSV\Tracker\..."
OUTPUT_ROOT  = r"D:\NSV\New_Structure_Data\New_Structure_Output"
SCHEMA_WORKBOOK = None   # optional: path to nsv_defect_schema_master 1.xlsx
```

**Zone, phase, cycle and UPC are read from `PROJECT_DIR`** (`zone=B`, `phase=3`, `cycle=1`, last folder = UPC).
Inside `PROJECT_DIR\d1 … d4`, the latest `run=YYYYMMDD_HHMMSS` folder is used automatically.
`PROJECT_DIR` can point straight at the bronze output of the NSV extractor, for example
`...\data\bronze\nsv\zone=A\phase=3\cycle=1\N-02017-03001-RJ`.

---

## 4. Run

```bash
python main.py                         # one UPC (config.PROJECT_DIR), parquet + Excel
python main.py --no-excel              # parquet only (fast, ~10 s per UPC)
python main.py --tables master_rutting_child_10m,master_pothole
python main.py --project "D:\...\N-xxxx" --tracker "D:\...\tracker.xlsx" --out "D:\...\Output"

# MANY UPCs: every folder under --root that has d1..d4 sub-folders
python main.py --root "D:\NSV\New_Structure_Data" --no-excel
python main.py --root "D:\...\bronze\nsv" --upc RJ --no-excel      # only UPCs whose name contains RJ
```

**To run one table only** (the options are the same as above):

```bash
python tables/master_rutting_child_10m.py --no-excel
python validation/dq_checks.py         # DQ only, after the tables have been written
```

### Run order used by `main.py`

1. Rutting, roughness and ravelling: each at 10 m → 100 m → 1 km.
2. Cracking.
3. Potholes.
4. Chainage.
5. DQ against D2.

If one table or one UPC fails, the rest still run. The error is written to the log and to the batch report.

## 5. Output

```
<OUTPUT_ROOT>\zone=B\phase=3\cycle=1\N-02017-03001-RJ\
    dlm_kpi\<table>.parquet + .xlsx        ← all rows
    dlm_postgres\<table>.parquet + .xlsx   ← only breach_flag = 1 rows
    dq\dq_summary.csv                       ← one row per DQ check: D2 rows, matched, match %, PASS/FAIL
    dq\dq_mismatches.csv                    ← every D2 row that did not match (bin, D2 value, silver value, reason)
    run.log                                 ← everything printed for this UPC, including errors and the files used
<OUTPUT_ROOT>\_batches\batch=<time>.csv    ← one row per UPC × table + DQ result          ← START HERE
```

- `master_chainage` is written only to `dlm_kpi`, because it has no breach flag.
- Writing Excel takes about 1.5 minutes for each 90,000-row table. Use `--no-excel` while testing.
- `pip install xlsxwriter` makes the Excel writing much faster.

## 6. Input files and where each defect comes from

The code looks for each file **by words in its name** first. If no file name matches, it looks **by the columns inside the file**, so a renamed defect report is still found. Parquet is preferred, then csv, then xlsx. `run.log` lists every file that was used and how it was found.

| Folder | File (name contains) | Used for |
|---|---|---|
| d1 | `lrpmaster` | nh_number, section_code |
| d1 | `pavementtype`, `serviceroad` | pavement_type |
| d1 | `carriagewaytype` | carriageway |
| d2 | `comparison_ca` / `comparison_irc` | reporting interval column, DQ |
| d2 | `potholes` | pothole DQ |
| d2\run=…\meta | `inputs_thresholds_cells.json` | **thresholds**: value, operator (`>`), unit, reporting interval |
| d2\run=…\meta | `data_dictionary.json` | thresholds from row 2 of *Comparison as per CA / IRC* (backup) |
| d4 | `defect` + `report` (found by columns if renamed) | all 10 m values |
| d4 | `nsv_cracks` | cracking values **only if the defect report has no cracking** |
| d4 | `images` + `crack` (`d4_images_crack_images`) | **cracking images** (10 m / 100 m / 1 km): chainage → lat / long (5 m) → OCR chainage → OCR lat / long (5 m) |
| d4 | `nhai_one` + `pothole` | **master_pothole** (one row per pothole, with image) and the survey date |

**Source order** (set in `config.py`): the first source that has data is used.

| Defect | 1st source | 2nd source (fallback) |
|---|---|---|
| rutting, roughness, ravelling | defect report | — |
| cracking | defect report `Cracking Lane Area (%)` | NSV cracks `Crack Area \| Total` (%); sq m = % × 35 / 100 |
| potholes | NHAI One sheet: one POINT row per pothole, as in the old notebook (start = end, length 0, `image_urls`) | — (no fallback; no NHAI One sheet → master_pothole empty) |

NHAI One may hold only the Moderate and High potholes (RJ) or every severity (some GJ projects also have Low and Delamination). The pothole DQ check compares silver with the D2 *Potholes* rows of the severities silver has, Delamination left out on both sides (RJ: 100 %).
In master_pothole, breach_flag = 0 when the severity is Low / Delamination (`config.POTHOLE_NO_BREACH_SEVERITY`; type of defect is not used) and 1 for every other pothole (blank severity included); road_condition = Bad when breach_flag = 1, Good when 0 — nothing else (no D4 / D3). rating_ca / rating_irc = D4 defect report 'Visual Condition - Pothole (CA)' / '(IRC)' of the same lane + 10 m segment (NULL when D4 has none); threshold_value NULL (no pothole threshold in D2). affected_length(_ca/_irc) and total_length = the pothole area as read. PCI uses the same cracking and pothole rows as the tables.

## 6a. DQ checks (silver vs D2)

These checks run for every UPC and show how many D2 rows the silver output reproduces. **A check passes when at least 98 % of D2 rows match** (`config.DQ_PASS_PCT`).

| Check | D2 | Silver | Match when |
|---|---|---|---|
| threshold | Inputs (Thresholds) / header json / limit column, per standard + defect | — | D2 has the threshold (no defaults; missing → FAIL, and that defect's threshold_value / breach_flag / road_condition are NULL) |
| value | rut depth, roughness BI, cracking % per lane, at D2's interval (100 m or 1 km) | the 100 m or 1 km table; cracking three ways (10 m rows averaged per 100 m, the 100 m table, the 1 km table vs the D2 100 m average of the km) | the difference is 0.01 or less (roughness BI: 1 mm/km) |
| breach | CA: flag per lane (`Current Cycle \| L1 Rut Depth`, …). IRC: the IRC sheet has lane values but no flag columns, so the D2 breach is derived — D2 IRC lane value vs the D2 IRC threshold and operator | breach_flag of the same lane + block + standard | the flags are equal |
| failing_lanes | CA **and** IRC: `Current Cycle \| Roughness / Rutting / Cracking \| Failing Lanes` (Yes / No per block) | `Yes` when any lane of the block has breach_flag = 1 in the 100 m / 1 km table of that standard | equal |
| d2_sheet | a missing `Comparison as per CA` / `as per IRC` sheet | — | listed as NOT RUN: that standard's rows are not compared (the other sheet is never used instead) |
| pothole_area | *Potholes* sheet area per lane per 100 m (Delamination excluded) | master_pothole area | the difference is 0.01 sq m or less |

N-02017-03001-RJ: value 98.3–100 %, breach 99.9–100 %, pothole 100 %; the two `threshold` checks for ravelling (CA, IRC) FAIL because that D2 has no ravelling threshold (34/36).

## 7. Logic in short

Full column-by-column detail is in `docs/NSV_Silver_Column_Lineage.xlsx`.

- **CA + IRC twin rows.** Every rutting, roughness, ravelling and cracking segment is written twice: once `As per CA` and once `As per IRC`.
  The IRC row's id has `_irc_` in it.
- **Levels.**
  - 10 m = D4 rows as they are.
  - 100 m = **aggregate of the 10 m rows** of the block.
  - 1 km = aggregate of **all the 10 m rows** of the km (`config.PARENT_AGGREGATION = 'from_10m'`, as D2), or of the 100 m rows (`'from_100m'`). It is never built from the first child.

  Each level's own value is then compared with the threshold to get breach_flag, severity, rating_ca, rating_irc and road_condition.
- **Rutting**: 100 m = average of the 10 m rut depths; 1 km = average of all the 10 m rut depths in the km (`'from_100m'`: average of the 100 m values).
- **Roughness BI**
  - 10 m: `BI = 630 × IRI^1.12`, from the D4 IRI; IRI blank → D4 `Roughness BI @ 10 mtr | <lane>`; both blank → NULL.
  - 100 m / 1 km: `630 × (average IRI)^1.12` (`config.ROUGHNESS_AGGREGATION = 'iri_formula'`), which is how D2 is built. A block with no IRI but D4 BI values keeps the average of those BI values.
  - `'mean_bi'` (average of the BI values) is the other option; it comes out about 1 % above D2, so the DQ roughness *value* checks FAIL with it.
- **Ravelling %** = area (sq m) × 100 ÷ 35 at 10 m; 100 m = total sq m × 100 ÷ 350; 1 km = total sq m × 100 ÷ 3,500 (not the mean of the 10 m percentages).
- **No row is dropped for a blank value.** Every D4 lane-row becomes a 10 m row in each rutting / roughness / ravelling table. A blank value stays blank (metric_value NULL, breach_flag 0, road_condition NULL); the row's D4 Visual Condition is still used. With no D2 threshold, breach_flag is NULL as well. Roughness: BI = 630 × IRI^1.12, or the D4 BI when IRI is blank.
- **IDs (same as the DLM notebook)**
  | Column | Rule |
  |---|---|
  | id | `{UPC}_{table}_{nnnnnn}`; IRC twin: `{UPC}_{table}_irc_{nnnnnn}` |
  | record_id / source_defect_id | `{defect}_{level}_{nnnnnn}` (cracking 10 m: `cracking_{nnnnnn}`, pothole: `pothole_{nnnnnn}`); IRC twin: + `_irc` |
  | parent_id | `{UPC}_{lane}_{km start}_{km end}` — the **1 km block at every level** (10 m incl. cracking and pothole, 100 m, 1 km); `parent_start_chainage` / `parent_end_chainage` are the same block (`config.PARENT_BLOCK_BY_LEVEL`) |
  | defect_id | `D/{ddmmyy}/N/{UPC}/{start_chainage}/{Defect}/{nnnn}` for breach rows only (pothole: every row; **cracking 100 m / 1 km: never**). ddmmyy = first `Datetime` of the NHAI One pothole sheet, else the run date |
  | display_defect_id | Set after all tables are built (`common/display_ids.py`). 1 km tables + master_pothole: breach rows → `D/{ddmmyy}/N/{nnnnn}` (running counter). 100 m / 10 m tables: breach rows whose 1 km parent (same parent_id + threshold_name) has an id → `{parent id}/{nnnn}`; other breach rows → their own `D/{ddmmyy}/N/{nnnnn}`. Non-breach → NULL. ddmmyy = tracker survey end date, else the defect_id date. The counter continues after the highest counter of OTHER UPCs with the same date in the output folder. |
- **Rutting, roughness and ravelling: five SEPARATE fields.** None of them is copied from another, and none is forced to match another.

  | Field | Rule (every level: 10 m, 100 m, 1 km) |
  |---|---|
  | threshold_value | ONLY the D2 threshold for the row's standard (Inputs (Thresholds) json → D2 header json → D2 limit column). No defaults: not in D2 → threshold_value, breach_flag and road_condition NULL, and the DQ `threshold` check FAILs. CA and IRC are independent. |
  | breach_flag | `metric_value` vs `threshold_value`. The operator comes from Inputs (Thresholds) column B, or `>=` if there is none. This is independent of the label boundaries. |
  | road_condition | `Poor` if breach_flag = 1, else `Good`; NULL when metric_value is blank or D2 has no threshold. |
  | severity | 10 m: the D4 *Visual Condition* of the row's standard, as it is. If missing: calculated. 100 m / 1 km: calculated from the **aggregated** value (never the first or worst 10 m label). The `severity_source` column says which. |
  | rating_ca / rating_irc | **10 m:** the D4 *Visual Condition* (the CA label goes to rating_ca, the IRC label to rating_irc), as it is. If D4 has no label, it's calculated. **100 m:** calculated from the aggregated 10 m values. **1 km:** calculated from the aggregated 1 km value. "Calculated" means the value is compared with that standard's D2 threshold (no D2 threshold → NULL) using `rating_ranges`, giving old-notebook labels such as `Good (<10mm)`, `Fair (10-20mm)` or `Poor (>20mm)`. The columns `rating_ca_source` and `rating_irc_source` show which was used. This applies to **rutting, roughness, ravelling and cracking**; for cracking the 5 bands are Very Good / Good / Fair / Poor / Very Poor. |

- **Boundaries** follow the old notebook (including its rounding, e.g. `round(0.5 × T)`).
  - Rutting and roughness: Fair includes T; Poor is > T.
  - Ravelling: Fair is < T; Poor is ≥ T.
  - IRC roughness: Good < **2000** BI mm/km, from the D4 Visual Condition.
- **Ravelling**: `ravelling_area_sqm` keeps the original area in m² (100 m / 1 km: the sum of the block). `ravelling_area` and `metric_value` = area × 100 / 35 (%) at 10 m, total area × 100 / 350 at 100 m, ÷ 3,500 at 1 km. D4 *Visual Condition - Ravelling (CA)/(IRC)* is read (`vc_rav_ca` / `vc_rav_irc`) and never mistaken for the area column.
- **`defect_rules.json`** holds every number and the label texts: `severity_ranges`, `rating_ranges` and `project_overrides` (per UPC). Each section has a `_source` note.
- **Rule checks** (`validation/rule_checks.py`, which runs automatically and writes `dq/rule_checks.csv`):
  - Every value in the generated tables is recomputed independently from D4 and the old notebook formulas, then compared row by row: source value, threshold, breach, road_condition, severity, ratings, twin rows, and no missing value turned into 0.
  - It also runs exact-boundary tests.
  - Result: N-02017-03001-RJ **179/179**, N-08015-02001-RJ **183/183** and N-04003-11002-RJ **183/183 PASS** (this includes a check that every 1 km value is rebuilt from the generated 10 m rows — or 100 m rows with `'from_100m'`; ravelling: total sq m ÷ 3,500). Checks also confirm no D4 row is dropped for a blank value. The checker also catches 6 out of 6 deliberately introduced errors.
- **Cracking unit.** If the Inputs (Thresholds) sheet gives the cracking limit in sq. m. instead of %, the breach check uses the cracked area (sq m).
- **Reporting interval** (`reporting_interval_m`): ONLY from D2, current cycle — `Project.d2_interval`: CA rows from 'Comparison as per CA', IRC rows from 'Comparison as per IRC', column '<defect> | Reporting Interval' (most common value). No D2 column (ravelling, pothole) or no sheet → NULL. The tracker interval is never used; master_reporting_interval and dlm_threshold_mapping use the same function.
- **road_condition** for **cracking** only (rutting, roughness, ravelling and pothole use breach_flag, see above), in this order:
  1. The **D4** *Visual Condition* label for the defect: the CA label on CA rows and the IRC label on IRC rows, for the same lane and 10 m. 100 m and 1 km rows take the **worst** 10 m label in their block.
  2. If D4 has no label: CA rows get `Bad` if breached, else `Good`; IRC rows are left blank. NULL when the % is blank or D2 has no cracking threshold. (D3 is not used.)

  The run log prints how many rows came from each source.
- **rating_ca / rating_irc** are written on both twin rows.
- **Cracking** comes from the defect report first, with a fallback to the NSV Cracks sheet. **Potholes** come ONLY from the NHAI One sheet (no fallback).
- **PCI** per 10 m = 100 − weighted defect score. The weights are in `config.PCI_WEIGHTS`; the roughness input is IRI (m/km). Blanks are not counted: only 10 m slots with at least one real defect value get a PCI, and 100 m / 1 km PCI is the average of those 10 m values (chainage gaps and all-blank rows are left out, never counted as PCI 100).
- **Roughness BI** = 630 × IRI^1.12; if IRI is blank, the D4 *Roughness BI* value; if both are blank, NULL (never IRI in its place). The roughness threshold comes from D2 in BI mm/km; a D2 value given as IRI (unit m/km, or below 100) is converted with the same formula and a warning is printed.
- **Blank end chainage** (D4, NSV cracks) → start + 10 m with a warning; it never stops the run.
- **Survey date**, in this order: D4 pothole `Datetime` → D1 LRP `Survey Date` → tracker.

---

## 8. Adding a new table

1. Copy a file in `tables/`, for example `master_pothole.py`.
2. Set `TABLE`, and write `build(prj)` so it returns a DataFrame. Use `prj.d4_long`, `prj.road.lookup(...)`, `prj.thr`, and so on.
3. Add the module to `PIPELINE` in `main.py`.

---

## 9. Troubleshooting

| Message | Meaning |
|---|---|
| `tracker not loaded` | Tracker path is wrong, or there is no `Zone <X>` sheet. Survey dates then come from the data. |
| `D4 10m defect report not found` | No file in `d4\run=…` has `defect` + `report` (or `data_sheet`) in its name. |
| `master_cracking empty` | Neither the defect report nor the NSV cracks sheet has cracking data. This is normal for some projects. |
| DQ check FAIL | Open `dq\\dq_mismatches.csv`. `reason` says whether there is no silver row for that bin or the value differs. |
| A table FAILED in the batch | Open that UPC's `run.log`; the full error is there. |
| Excel very slow | Use `--no-excel`; the parquet files are complete. |
