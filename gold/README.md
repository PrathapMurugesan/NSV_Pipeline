# RIS KPI Pipelines

Local (pandas) versions of the NHAI RIS KPI Databricks notebooks. Three pipelines read the
silver-layer tables and write the KPI output tables as CSV.

| Pipeline | Folder | Output | Depends on |
|---|---|---|---|
| RIS Trends | [code_ris_trends](code_ris_trends/README.md) | `ris_trends_dw.csv` | — |
| Affected length (intermediate) | [code_intermediate_logic](code_intermediate_logic/README.md) | `final_intermediate.csv` | — |
| RIS View | [code_ris_view](code_ris_view/README.md) | `ris_view_dw.csv` | `final_intermediate.csv` |

## Setup

Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Each pipeline reads settings (paths, filters, source tables) from its own `.env` file.
Relative paths in them are resolved from that pipeline's folder; real environment variables
take precedence.

## Data

Data files are not committed. Before running, place them as follows:

```
RIS_KPI_git/
├── project_master.xlsx          # contract-module project master
├── Input_silver_data_new/       # silver tables (.csv / .parquet)
│   ├── master_chainage.csv
│   ├── master_reporting_interval.csv
│   ├── dlm_threshold_mapping.csv
│   ├── master_pothole.csv
│   ├── master_cracking.csv
│   ├── master_{cracking,ravelling,roughness,rutting}_parent.csv
│   ├── master_{cracking,ravelling,roughness,rutting}_child_100m.csv
│   └── master_{ravelling,roughness,rutting}_child_10m.csv
└── Output_KPI_data/             # written by the pipelines
```

## Run

```bash
python run_all.py                              # ris_trends -> intermediate -> ris_view
python run_all.py --only intermediate ris_view
python run_all.py --keep-going                 # continue after a failure (dependents still skipped)
```

Or run a single pipeline from its folder: `cd code_ris_view && python main.py`.
`run_all.py` stops at the first failure, prints a summary, and exits with code 1.

## Layout

```
├── run_all.py                  # runs the pipelines in order
├── requirements.txt            # dependencies for all pipelines
├── code_ris_trends/            # main.py, utils/, .env, README.md
├── code_intermediate_logic/
└── code_ris_view/
```
