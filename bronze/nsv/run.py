"""
run.py - one run = one deliverable of one survey -> one timestamped bronze folder.

    bronze/nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2/
        run=20261020_101500/
            d2_comparison_comparison_ca.parquet + .csv      data exactly as delivered (all text)
            d2_comparison_potholes.parquet + .csv
            meta/d2_comparison_profile.json                 what each column really contains
            meta/d2_comparison_formulas.json                how calculated columns are calculated
            meta/d2_comparison_data_dictionary.json         legends / code meanings from header rows
            meta/d2_comparison_summary_cells.json           report sheets / side tables, cell by cell
            d2_manifest.json                                what was found, checked, saved, rejected
            d2_run.log                                      the log lines of this run
        d2_latest.json                                      points to the newest run

Every file name = <deliverable>_<workbook key>_<sheet key>: stable keys, not the vendor's file name
(that one changes every month and is recorded in the manifest). Nothing is ever deleted or overwritten.
"""
import io
import logging
from datetime import datetime

import settings
from nsv.log import FORMAT, get_logger
from nsv.matching import safe_name
from nsv.writer import csv_bytes, json_bytes, parquet_bytes

logger = get_logger("nsv.run")


class Run:
    def __init__(self, deliverable, survey):
        # ---- identity of this run ----
        self.deliverable = deliverable
        self.survey = survey
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.input_folder = survey.raw_path(deliverable)                  # where the raw files are
        self.bronze_folder = survey.bronze_path(deliverable)              # .../d2
        self.folder = f"{self.bronze_folder}/{settings.RUN_FOLDER.format(run_id=self.run_id)}"   # .../d2/run=...

        # ---- capture this run's log lines (saved as <d>_run.log at the end) ----
        self._log_text = io.StringIO()
        self._log_handler = logging.StreamHandler(self._log_text)
        self._log_handler.setFormatter(FORMAT)
        logging.getLogger("nsv").addHandler(self._log_handler)

        # ---- the manifest: filled in while the run goes, written at the end ----
        self.manifest = {
            "deliverable": deliverable,
            "survey": survey.as_dict(),
            "run_id": self.run_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "status": "running",
            "input": f"{settings.INPUT.describe()}/{self.input_folder}",
            "output": f"{settings.OUTPUT.describe()}/{self.folder}",
            "settings": {
                "CHECK_HEADERS": settings.CHECK_HEADERS,
                "STOP_IF_WORKBOOK_MISSING": settings.STOP_IF_WORKBOOK_MISSING,
                "EXTRACT_ALL": settings.EXTRACT_ALL,
                "FUZZY_THRESHOLD": settings.FUZZY_THRESHOLD,
                "NULL_LIMIT": settings.NULL_LIMIT,
            },
            "workbooks": {},        # filled by pipeline.find_workbooks / extract_workbook
            "csv_files": {},        # filled by pipeline.extract_csv_folder (D3)
            "unexpected_files": [],
            "summary": {},
        }
        get_logger("nsv").info(f"========== {survey.label} {deliverable}: run {self.run_id} started ==========")

    # -------------------------------------------------------------------------
    # writing helpers - every output goes through settings.OUTPUT (local or S3)
    # -------------------------------------------------------------------------
    def file_name(self, *parts):
        """('comparison', 'comparison_ca') -> 'd2_comparison_comparison_ca'"""
        return "_".join(safe_name(p) for p in (self.deliverable, *parts))

    def save_table(self, df, *parts):
        """Write a table as <run folder>/d2_<parts>.parquet and .csv; returns the two paths."""
        base = f"{self.folder}/{self.file_name(*parts)}"
        settings.OUTPUT.write_bytes(base + ".parquet", parquet_bytes(df))
        settings.OUTPUT.write_bytes(base + ".csv", csv_bytes(df))
        return [base + ".parquet", base + ".csv"]

    def save_json(self, obj, *parts):
        """Write <run folder>/meta/d2_<parts>.json; returns the path."""
        path = f"{self.folder}/meta/{self.file_name(*parts)}.json"
        settings.OUTPUT.write_bytes(path, json_bytes(obj))
        return path

    # -------------------------------------------------------------------------
    # finish: summary + manifest + run log + latest pointer
    # -------------------------------------------------------------------------
    def finish(self, status=None):
        sheets = [s for wb in self.manifest["workbooks"].values() for s in wb.get("sheets", {}).values()]
        csvs = list(self.manifest["csv_files"].values())
        count = lambda items, st: sum(1 for i in items if i.get("status") == st)   # noqa: E731
        summary = {
            "sheets_saved": count(sheets, "saved") + count(sheets, "saved_cells"),
            "sheets_rejected": count(sheets, "rejected_headers"),
            "sheets_missing": count(sheets, "missing"),
            "sheets_read_error": count(sheets, "read_error"),
            "sheets_skipped_by_config": count(sheets, "skipped_by_config"),
            "workbooks_missing": count(list(self.manifest["workbooks"].values()), "missing"),
            "csv_saved": count(csvs, "saved"),
            "csv_read_error": count(csvs, "read_error"),
            "image_folders_saved": count(list(self.manifest.get("image_folders", {}).values()), "saved"),
            "image_folders_missing": count(list(self.manifest.get("image_folders", {}).values()), "missing"),
            "images": sum(f.get("images", 0) for f in self.manifest.get("image_folders", {}).values()),
        }
        self.manifest["summary"] = summary
        required_missing = sum(1 for f in self.manifest.get("image_folders", {}).values()
                               if f.get("status") == "missing" and f.get("required"))
        problems = (summary["sheets_rejected"] + summary["sheets_missing"] + summary["workbooks_missing"]
                    + summary["sheets_read_error"] + summary["csv_read_error"] + required_missing)
        self.manifest["status"] = status or ("completed_with_problems" if problems else "completed")
        self.manifest["finished_at"] = datetime.now().isoformat(timespec="seconds")

        log = get_logger("nsv")
        log.info(f"========== {self.survey.label} {self.deliverable}: {self.manifest['status']} - {summary} ==========")
        log.info(f"results: {settings.OUTPUT.describe()}/{self.folder}")

        settings.OUTPUT.write_bytes(f"{self.folder}/{self.deliverable}_manifest.json", json_bytes(self.manifest))
        settings.OUTPUT.write_bytes(f"{self.bronze_folder}/{self.deliverable}_latest.json",
                                    json_bytes({"run_id": self.run_id, "folder": self.folder,
                                                "status": self.manifest["status"]}))
        logging.getLogger("nsv").removeHandler(self._log_handler)
        settings.OUTPUT.write_bytes(f"{self.folder}/{self.deliverable}_run.log",
                                    self._log_text.getvalue().encode("utf-8"))
        return self.manifest
