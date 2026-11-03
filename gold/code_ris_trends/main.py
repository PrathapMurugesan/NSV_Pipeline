"""RIS Trends KPI pipeline (modular version of code_notebooks/RIS_Trends_v6 in_use.ipynb).

Reads the silver tables from INPUT_DIR and writes ris_trends_dw.csv to OUTPUT_DIR. All settings live in code_ris_trends/.env.
DB id columns (UPC_ID, Survey_ID, Survey_Source_ID) are not created here; they are added on push.

    python main.py
"""
import logging

from utils.categories import build_category_table, merge_categories
from utils.config import load_settings
from utils.filters import filter_contract_upcs, filter_pavement_types, filter_upcs, log_table_summary
from utils.io_utils import load_contract_upcs, load_silver_tables, load_threshold_mapping, save_csv
from utils.kpi_metrics import build_ris_trends
from utils.output import finalize_ris_trends
from utils.pci import attach_pci

log = logging.getLogger("ris_trends")


def run(settings):
    # --- 1. Load & filter silver tables ---
    tables = load_silver_tables(settings.input_dir)
    tables = filter_upcs(tables, settings.keep_upcs)
    tables = filter_pavement_types(tables, settings.keep_pavement_types)
    log_table_summary(tables)

    # --- 2. Reference data ---
    cm_upcs = load_contract_upcs(settings.cm_project_master_path, settings.filter_by_project_master)
    thresholds = load_threshold_mapping(settings.input_dir, settings.threshold_mapping_table)

    # --- 3. Roughness_Average / Average_Rutting_Depth per grain ---
    ris_trends_df = build_ris_trends(
        tables["master_roughness_parent"], tables["master_rutting_parent"], settings.use_breach_flag
    )

    # --- 4. PCI_Value from 1 km chainage buckets ---
    ris_trends_df = attach_pci(
        ris_trends_df,
        tables["master_chainage"],
        [tables["master_roughness_parent"], tables["master_rutting_parent"]],
    )

    # --- 5. Project-master filter ---
    ris_trends_df = filter_contract_upcs(ris_trends_df, cm_upcs)

    # --- 6. CA / IRC condition categories (3 rows per grain) ---
    cat_df = build_category_table(tables, thresholds)
    ris_trends_df = merge_categories(ris_trends_df, cat_df, cm_upcs)

    # --- 7. Final table -> CSV ---
    ris_trends_dw = finalize_ris_trends(ris_trends_df)
    log.info("ris_trends_dw finalised — %s rows × %s cols", f"{len(ris_trends_dw):,}", ris_trends_dw.shape[1])

    save_csv(ris_trends_dw, settings.output_dir, "ris_trends_dw")
    return ris_trends_dw


def main():
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    run(settings)


if __name__ == "__main__":
    main()
