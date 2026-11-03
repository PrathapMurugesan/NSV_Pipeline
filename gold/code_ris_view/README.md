# RIS View

Modular, local (pandas) version of `code_notebooks/RIS View Pipeline_v2 in_use.ipynb`.

## Run

Run `code_intermediate_logic` first; this pipeline reads its `final_intermediate.csv`.

```bash
cd code_ris_view
pip install -r requirements.txt
python main.py
```

Outputs, written to `OUTPUT_DIR` (default `../Output_KPI_data`):

| File | Notebook equivalent |
|---|---|
| `ris_view_dw.csv` | `<OUTPUT_SCHEMA>.ris_view_dw` |

DB id columns (`UPC_ID`, `Survey_ID`, `Survey_Source_ID`, `Defect_Type_ID`) are not created
here; they are added when the output is pushed to Postgres. The lookup keys they need are kept,
including `Defect_Asset_Type` + `Defect_Name` (the `master_defect_type` keys for `Defect_Type_ID`) and
`Source_System` (for `survey_source_id`).

## Inputs

| Source | Setting | Notebook equivalent |
|---|---|---|
| `master_pothole`, `master_{ravelling,roughness,rutting}_parent` | `INPUT_DIR` | `nhai_dhms.dlm_kpi.*` |
| Cracking (default `master_cracking`) | `CRACKING_TABLE` | `nhai_dhms.dlm_kpi.master_cracking` (10m) |
| `project_master.xlsx` | `PROJECT_MASTER_PATH`, `FILTER_BY_PROJECT_MASTER` | `nhai_dhms.contract_module.project_master_onetime` |
| `final_intermediate.csv` | `INTERMEDIATE_PATH` | `<OUTPUT_SCHEMA>.final_intermediate` |

Project-master columns used (see `utils/project_master.py`): `Length-km` → UPC Length(in km),
`Lane-km` → UPC Lane Length(in km), `State Name` → State, `RO`, `PIU`, `NH No.` → NH,
`AE/IE Name` → AE/IE, `Contractor Name` → Contractor.

## Layout

```
code_ris_view/
├── main.py                 # pipeline entry point
├── .env                    # settings
└── utils/
    ├── config.py           # .env -> Settings
    ├── io_utils.py         # silver / intermediate reads, CSV writes
    ├── project_master.py   # project details + project-master UPC filter
    ├── defect_counts.py    # defect rows -> breached-defect counts per grain
    ├── affected_length.py  # affected lengths per defect + 'Total' rows
    ├── output.py           # ris_view_dw
    └── helpers.py          # key / date normalisation
```
