"""Affected-length intermediate pipeline (modular version of
code_notebooks/Affected length intermediate logic v3 in_use.ipynb).

Reads the silver tables and master_reporting_interval from INPUT_DIR and writes
final_intermediate.csv to OUTPUT_DIR.
All settings live in code_intermediate_logic/.env.

    python main.py
"""
import logging

from utils.config import load_settings
from utils.defect_report import SOURCE_TABLES, build_defect_report, prepare_source
from utils.io_utils import load_table, save_csv
from utils.reporting_intervals import load_reporting_intervals
from utils.spine_flags import build_final_table, build_spine, flag_spine

log = logging.getLogger("affected_length")


def run(settings):
    # --- 1. Load sources ---
    table_files = {**SOURCE_TABLES, "cracking": settings.cracking_table}
    sources = {key: prepare_source(load_table(settings.input_dir, name)) for key, name in table_files.items()}
    chainage = load_table(settings.input_dir, "master_chainage")
    ri = load_reporting_intervals(settings.input_dir, settings.reporting_intervals_table, settings.default_interval)

    # --- 2. Metric per reporting-interval window, flagged against the threshold ---
    defect_report = build_defect_report(sources, ri)

    # --- 3. Defected windows mapped onto the 10m chainage spine -> affected lengths ---
    flags = flag_spine(build_spine(chainage), defect_report)
    final = build_final_table(flags, ri)
    save_csv(final, settings.output_dir, "final_intermediate")
    return final


def main():
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    run(settings)


if __name__ == "__main__":
    main()
