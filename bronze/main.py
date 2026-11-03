"""
main.py - extract one or many surveys, one after another.

    python main.py                                   every deliverable of every survey found
    python main.py d2 d4                             only these deliverables
    python main.py --zone A                          only zone A   (filters can be combined)
    python main.py --zone A B --phase 1 --cycle 2
    python main.py --upc N/08004/03001/HR            one survey ('/' or '-' both accepted)
    python main.py --list                            only show what would run, extract nothing

How the loop works:
    1. find every survey in the raw folders:   nsv/zone=?/phase=?/cycle=?/<UPC>/<d1..d4>/
    2. for each survey, for each deliverable:  extract it -> its own bronze run folder, manifest and log
         - deliverable folder not there      -> "not_delivered" (not an error)
         - a crash in one survey             -> logged with traceback, the loop continues with the next
    3. a batch summary of everything:          bronze/nsv/_batches/batch=<timestamp>.json
       and a simple report, one row per sheet:  bronze/nsv/_batches/batch=<timestamp>_report.csv
       (problems - rejected / missing / errors - are also printed at the end)
    4. with d2: the surveys' rows are added to the master table  master/reporting_intervals.parquet / .csv
       (see nsv/intervals.py; only that step:  python run_intervals.py)
Exit code: 0 = everything completed, 2 = something had problems / crashed (see the batch summary).
"""
import argparse
import sys
from datetime import datetime

import settings
from deliverables import d1, d2, d3, d4
import pandas as pd

from nsv.intervals import update_master
from nsv.log import get_logger
from nsv.report import COLUMNS, PROBLEMS, report_rows
from nsv.survey import discover_surveys
from nsv.writer import csv_bytes, json_bytes

DELIVERABLE_MODULES = {"d1": d1, "d2": d2, "d3": d3, "d4": d4}
OK_STATUSES = {"completed", "not_delivered"}

logger = get_logger("nsv")


def parse_args(args):
    parser = argparse.ArgumentParser(description="NSV extraction: raw -> bronze",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("deliverables", nargs="*", help="d1 d2 d3 d4 (default: all)")
    parser.add_argument("--zone", nargs="+", help=f"one or more of {settings.ZONES}")
    parser.add_argument("--phase", nargs="+", help=f"one or more of {settings.PHASES}")
    parser.add_argument("--cycle", nargs="+", help=f"one or more of {settings.CYCLES}")
    parser.add_argument("--upc", nargs="+", help="one or more UPC codes, e.g. N/08004/03001/HR")
    parser.add_argument("--list", action="store_true", help="show the surveys that would run, extract nothing")
    parsed = parser.parse_args(args)
    unknown = [d for d in parsed.deliverables if d not in DELIVERABLE_MODULES]
    if unknown:
        parser.error(f"unknown deliverable(s) {unknown}, choose from {list(DELIVERABLE_MODULES)}")
    return parsed


def main(args):
    options = parse_args(args)
    wanted = options.deliverables or list(DELIVERABLE_MODULES)

    # ---- 1. which surveys? ----
    surveys = discover_surveys(zones=options.zone, phases=options.phase, cycles=options.cycle, upcs=options.upc)
    if not surveys:
        logger.error("no survey found - check the raw folder layout (settings.RAW_LAYOUT) and the filters")
        return 1
    for survey, present in surveys.items():
        logger.info(f"  {survey.label:35} deliverables present: {present}")
    if options.list:
        return 0
    problem = settings.OUTPUT.check() if hasattr(settings.OUTPUT, "check") else None
    if problem:
        logger.error(f"output {settings.OUTPUT.describe()}: {problem}")
        return 1

    # ---- 2. the loop: one survey after another, one deliverable after another ----
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    results, report = [], []
    for number, (survey, present) in enumerate(surveys.items(), start=1):
        logger.info(f"################ survey {number}/{len(surveys)}: {survey.label} ################")
        for name in wanted:
            row = {**survey.as_dict(), "deliverable": name}
            if name not in present:
                logger.info(f"[{survey.label}] {name}: no '{name}' folder - not delivered")
                results.append({**row, "status": "not_delivered"})
                report += report_rows(survey, name, "not_delivered")
                continue
            try:
                manifest = DELIVERABLE_MODULES[name].run(survey)
                results.append({**row, "status": manifest["status"], "run_id": manifest["run_id"],
                                "output": manifest["output"], "summary": manifest["summary"]})
                report += report_rows(survey, name, manifest["status"], manifest)
            except Exception as e:
                logger.exception(f"[{survey.label}] {name}: crashed - continuing with the next one")
                results.append({**row, "status": "crashed", "error": str(e)})
                report += report_rows(survey, name, "crashed", error=str(e))

    # ---- 3. batch summary ----
    totals = {}
    for r in results:
        totals[r["status"]] = totals.get(r["status"], 0) + 1
    batch = {"batch_id": batch_id, "finished_at": datetime.now().isoformat(timespec="seconds"),
             "filters": {"deliverables": wanted, "zone": options.zone, "phase": options.phase,
                         "cycle": options.cycle, "upc": options.upc},
             "surveys": len(surveys), "totals": totals, "results": results}
    path = f"{settings.BATCH_FOLDER}/batch={batch_id}.json"
    settings.OUTPUT.write_bytes(path, json_bytes(batch))
    report_path = f"{settings.BATCH_FOLDER}/batch={batch_id}_report.csv"
    settings.OUTPUT.write_bytes(report_path, csv_bytes(pd.DataFrame(report, columns=COLUMNS)))

    logger.info("################ batch summary ################")
    for r in results:
        logger.info(f"  {r['zone']}/{r['phase']}/{r['cycle']}/{r['upc_folder']:20} {r['deliverable']}  {r['status']}")
    logger.info(f"  totals: {totals}")

    problems = [r for r in report if r["status"] in PROBLEMS]
    logger.info(f"################ problems: {len(problems)} ################")
    for r in problems:
        where = f"zone {r['zone']} phase {r['phase']} cycle {r['cycle']} UPC {r['upc']} {r['deliverable']}"
        what = " / ".join(x for x in (r.get("workbook"), r.get("file"), r.get("sheet")) if x)
        logger.warning(f"  {r['status']:17} {where}  {what}  -> {r.get('reason')}")
    optional = [r for r in report if r["status"] == "optional_missing"]
    if optional:
        logger.info(f"################ optional folders not delivered: {len(optional)} ################")
        for r in optional:
            where = f"zone {r['zone']} phase {r['phase']} cycle {r['cycle']} UPC {r['upc']} {r['deliverable']}"
            logger.warning(f"  {where}  {r.get('sheet')}  -> not delivered")

    differ = [r for r in report if r["status"] == "saved_headers_differ"]
    if differ:
        logger.info(f"################ saved, but headers differ from the schema: {len(differ)} ################")
        for r in differ:
            where = f"zone {r['zone']} phase {r['phase']} cycle {r['cycle']} UPC {r['upc']} {r['deliverable']}"
            logger.warning(f"  {where}  {r.get('file')} / {r.get('sheet')}  -> {r.get('reason')}")
    logger.info(f"  full report (one row per sheet, open in Excel): {settings.OUTPUT.describe()}/{report_path}")
    logger.info(f"  batch summary: {settings.OUTPUT.describe()}/{path}")

    # ---- 4. master table of reporting intervals (one row per UPC, from the d1 / d2 just saved) ----
    if "d2" in wanted and settings.MASTER_INTERVALS:
        try:
            update_master([(s.zone, s.phase, s.cycle, s.upc_folder) for s in surveys])
        except Exception:
            logger.exception("master table of reporting intervals could not be updated")
    return 0 if all(r["status"] in OK_STATUSES for r in results) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
