"""RIS View pipeline (modular version of code_notebooks/RIS View Pipeline_v2 in_use.ipynb).

Reads the silver defect tables from INPUT_DIR, the project master and the final_intermediate
output of code_intermediate_logic, and writes ris_view_dw.csv to OUTPUT_DIR.
DB id columns are not created here; they are added when the output is pushed to Postgres.
All settings live in code_ris_view/.env.

    python main.py
"""
import logging

from utils.affected_length import attach_defect_lengths, build_total_rows
from utils.config import load_settings
from utils.defect_counts import combine_defects, count_defects
from utils.io_utils import load_intermediate, load_table, save_csv
from utils.output import build_ris_view_dw
from utils.project_master import filter_project_upcs, load_project_master

log = logging.getLogger("ris_view")

DEFECT_TABLES = ["master_pothole", "master_ravelling_parent", "master_roughness_parent", "master_rutting_parent"]


def run(settings):
    # --- 1. Load sources ---
    names = [settings.cracking_table] + DEFECT_TABLES
    tables = {name: load_table(settings.input_dir, name) for name in names}
    project_info = load_project_master(settings.project_master_path)
    intermediate = load_intermediate(settings.intermediate_path)

    # --- 2. Keep only project-master UPCs ---
    tables = filter_project_upcs(tables, project_info, settings.filter_by_project_master)

    # --- 3. Defect rows + project details -> breached-defect counts per grain ---
    counts = count_defects(combine_defects(tables.values()), project_info)

    # --- 4. Affected lengths from final_intermediate (per defect + 'Total' rows) ---
    defect_rows = attach_defect_lengths(counts, intermediate)
    total_rows = build_total_rows(intermediate, defect_rows)

    # --- 5. Final table -> CSV ---
    ris_view_dw = build_ris_view_dw(defect_rows, total_rows)
    log.info("ris_view_dw — %s rows × %s cols", f"{len(ris_view_dw):,}", ris_view_dw.shape[1])

    save_csv(ris_view_dw, settings.output_dir, "ris_view_dw")
    return ris_view_dw


def main():
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    run(settings)


if __name__ == "__main__":
    main()
