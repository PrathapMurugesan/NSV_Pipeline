# RIS Trends KPI

Modular, local (pandas) version of `code_notebooks/RIS_Trends_v6 in_use.ipynb`, which ran on Databricks/Spark.

## Run

```bash
cd code_ris_trends
pip install -r requirements.txt
python main.py
```

Outputs, written to `OUTPUT_DIR` (default `../Output_KPI_data`):

| File                 | Notebook equivalent                                  |
|----------------------|------------------------------------------------------|
| `ris_trends_dw.csv`  | `nhai_dhms.<output_schema>.ris_trends_dw`            |

DB id columns (`UPC_ID`, `Survey_ID`, `Survey_Source_ID`) are not created here; they are added when the data is pushed.

## Inputs

| Source | Setting | Notebook equivalent |
|---|---|---|
| `master_{roughness,rutting}_{parent,child_10m}`, `master_chainage` (`.parquet` or `.csv`) | `INPUT_DIR` | `nhai_dhms.dlm_kpi.*` delta tables |
| Contract-module project master (`project_master.xlsx`, needs a `UPC`/`upc` column) | `FILTER_BY_PROJECT_MASTER` (true/false) + `CM_PROJECT_MASTER_PATH` | `/Workspace/Shared/Contract-module/.../project_master_structure_4.xlsx` |
| Threshold mapping, in `INPUT_DIR` as `<THRESHOLD_MAPPING_TABLE>.(parquet\|csv\|xlsx)` (required) | `THRESHOLD_MAPPING_TABLE` | `nhai_dhms.dlm_kpi.dlm_threshold_mapping_display` (silver: `dlm_threshold_mapping.csv`, snake_case columns accepted) |

## Layout

```
code_ris_trends/
├── main.py              # pipeline entry point (steps 1-7)
├── .env                # settings
└── utils/
    ├── config.py        # .env -> Settings
    ├── io_utils.py      # silver (parquet / csv) / reference file reads, CSV writes
    ├── filters.py       # UPC / pavement_type filters, project-master UPC filter
    ├── kpi_metrics.py   # Roughness_Average, Average_Rutting_Depth
    ├── pci.py           # 1 km chainage buckets -> PCI_Value
    ├── categories.py    # CA / IRC condition categories and lane lengths
    ├── output.py        # final ris_trends_dw table
    └── helpers.py       # Spark-equivalent rounding, null handling, UPC formats
```
