"""Runtime settings, read from code_intermediate_logic/.env (real environment variables take precedence)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

CODE_DIR = Path(__file__).resolve().parents[1]


def _path(value):
    """Resolve a path from .env; relative paths are relative to the code_intermediate_logic/ folder."""
    p = Path(value)
    return p if p.is_absolute() else (CODE_DIR / p).resolve()


@dataclass(frozen=True)
class Settings:
    input_dir: Path
    output_dir: Path
    reporting_intervals_table: str  # file name (no extension) in input_dir
    default_interval: int           # used where a reporting interval is empty
    cracking_table: str             # file name (no extension) of the cracking source in input_dir
    log_level: str


def load_settings(env_file=None):
    load_dotenv(env_file or CODE_DIR / ".env")
    env = os.environ.get

    return Settings(
        input_dir=_path(env("INPUT_DIR", "../Input_silver_data_new")),
        output_dir=_path(env("OUTPUT_DIR", "../Output_KPI_data")),
        reporting_intervals_table=env("REPORTING_INTERVALS_TABLE", "master_reporting_interval").strip(),
        default_interval=int(env("DEFAULT_INTERVAL", "100")),
        cracking_table=env("CRACKING_TABLE", "master_cracking").strip(),
        log_level=env("LOG_LEVEL", "INFO"),
    )
