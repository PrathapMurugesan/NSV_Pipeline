"""Runtime settings, read from code_ris_view/.env (real environment variables take precedence)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

CODE_DIR = Path(__file__).resolve().parents[1]


def _path(value):
    """Resolve a path from .env; relative paths are relative to the code_ris_view/ folder."""
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (CODE_DIR / p).resolve()


def _bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    input_dir: Path
    output_dir: Path
    cracking_table: str             # file name (no extension) of the cracking source in input_dir
    project_master_path: Path       # contract-module project master (.csv / .xlsx)
    filter_by_project_master: bool  # keep only UPCs present in the project master
    intermediate_path: Path         # final_intermediate.csv from code_intermediate_logic
    log_level: str


def load_settings(env_file=None):
    load_dotenv(env_file or CODE_DIR / ".env")
    env = os.environ.get

    return Settings(
        input_dir=_path(env("INPUT_DIR", "../Input_silver_data_new")),
        output_dir=_path(env("OUTPUT_DIR", "../Output_KPI_data")),
        cracking_table=env("CRACKING_TABLE", "master_cracking").strip(),
        project_master_path=_path(env("PROJECT_MASTER_PATH", "../project_master.xlsx")),
        filter_by_project_master=_bool(env("FILTER_BY_PROJECT_MASTER", "true")),
        intermediate_path=_path(env("INTERMEDIATE_PATH", "../Output_KPI_data/final_intermediate.csv")),
        log_level=env("LOG_LEVEL", "INFO"),
    )
