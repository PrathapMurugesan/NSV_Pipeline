"""Runtime settings, read from code/.env (real environment variables take precedence)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

CODE_DIR = Path(__file__).resolve().parents[1]


def _path(value):
    """Resolve a path from .env; relative paths are relative to the code/ folder."""
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (CODE_DIR / p).resolve()


def _list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    input_dir: Path
    output_dir: Path

    # Filters
    keep_upcs: list               # '/' form (N/02015/03002/GJ); empty -> keep all UPCs
    keep_pavement_types: list
    use_breach_flag: bool

    # External reference data (not part of the silver layer)
    filter_by_project_master: bool  # keep only UPCs present in the project master
    cm_project_master_path: Path    # contract-module project master (.csv / .xlsx)
    threshold_mapping_table: str    # file name (no extension) in input_dir, e.g. dlm_threshold_mapping

    log_level: str


def load_settings(env_file=None):
    load_dotenv(env_file or CODE_DIR / ".env")
    env = os.environ.get

    return Settings(
        input_dir=_path(env("INPUT_DIR", "../Input_silver_data_new")),
        output_dir=_path(env("OUTPUT_DIR", "../Output_KPI_data")),
        keep_upcs=_list(env("KEEP_UPCS")),
        keep_pavement_types=_list(env("KEEP_PAVEMENT_TYPES", "Asphalt,Cement Concrete,Hybrid")),
        use_breach_flag=_bool(env("USE_BREACH_FLAG", "false")),
        filter_by_project_master=_bool(env("FILTER_BY_PROJECT_MASTER", "true")),
        cm_project_master_path=_path(env("CM_PROJECT_MASTER_PATH")),
        threshold_mapping_table=env("THRESHOLD_MAPPING_TABLE", "dlm_threshold_mapping").strip(),
        log_level=env("LOG_LEVEL", "INFO"),
    )
