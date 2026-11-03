# Affected Length — Intermediate Logic

Modular, local (pandas) version of `code_notebooks/Affected length intermediate logic v3 in_use.ipynb`.

## Run

```bash
cd code_intermediate_logic
pip install -r requirements.txt
python main.py
```

Outputs, written to `OUTPUT_DIR` (default `../Output_KPI_data`):

| File | Notebook equivalent |
|---|---|
| `final_intermediate.csv` | `nhai_dhms.<OUTPUT_SCHEMA>.final_intermediate` (10m spine × flags × affected lengths) |

## Inputs (all from `INPUT_DIR`)

| Source | Used for |
|---|---|
| `master_{roughness,rutting,cracking}_parent` | Windows where the reporting interval is 1000 m (as-is) |
| `master_{roughness,rutting}_child_10m`, `CRACKING_TABLE` | Windows for other intervals (e.g. 50 m), averaged into the interval; fallback when the parent / child 100m has no rows |
| `master_{roughness,rutting,cracking}_child_100m` | Windows where the reporting interval is 100 m (as-is) |
| `CRACKING_TABLE` (default `master_cracking`) | Cracking 10m table |
| `master_chainage` | 10m spine |
| `REPORTING_INTERVALS_TABLE` (default `master_reporting_interval`) | Interval per upc/phase/cycle/standard (CA, IRC) and defect; replaces the Databricks `upc_reporting_intervals` table. A missing standard or empty interval uses `DEFAULT_INTERVAL` |

## Layout

```
code_intermediate_logic/
├── main.py                    # pipeline entry point
├── .env                       # settings
└── utils/
    ├── config.py              # .env -> Settings
    ├── io_utils.py            # silver table reads, CSV writes
    ├── reporting_intervals.py # master_reporting_interval -> interval per defect / standard
    ├── defect_report.py       # source routing + window averages + defect_flag
    ├── spine_flags.py         # defected windows -> 10m spine flags and affected lengths
    └── helpers.py             # UPC format, Spark-style casts
```
