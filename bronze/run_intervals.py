"""
run_intervals.py - build / update the master table of reporting intervals from what is ALREADY extracted
(no extraction, only reads the d1 / d2 results in the output). How the values are found: nsv/intervals.py

    python run_intervals.py                                  every UPC with a d2 extraction in the output
    python run_intervals.py --upc N/02007/11001/HR           only these UPCs ('/' or '-' both fine)
    python run_intervals.py --zone A --phase 2 --cycle 1     filters can be combined
    python run_intervals.py --list                           only show the UPCs, write nothing

Output: <output>/master/reporting_intervals.parquet + .csv     (output = NSV_OUTPUT, or data/ locally)
A UPC already in the table is replaced by its new row, every other row stays.
"""
import argparse
import sys

import settings
from nsv.intervals import extracted_surveys, update_master
from nsv.log import get_logger

logger = get_logger("nsv.intervals")


def main(args):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone", nargs="+")
    parser.add_argument("--phase", nargs="+")
    parser.add_argument("--cycle", nargs="+")
    parser.add_argument("--upc", nargs="+", help="one or more UPC codes, e.g. N/02007/11001/HR")
    parser.add_argument("--list", action="store_true", help="only show the UPCs, write nothing")
    o = parser.parse_args(args)

    upcs = {u.strip().upper().replace("/", "-").replace("_", "-") for u in o.upc or []}
    surveys = [s for s in extracted_surveys(settings.OUTPUT)
               if (not o.zone or s[0] in o.zone) and (not o.phase or s[1] in o.phase)
               and (not o.cycle or s[2] in o.cycle) and (not upcs or s[3].upper() in upcs)]
    logger.info(f"{len(surveys)} UPC(s) with a d2 extraction in {settings.OUTPUT.describe()}")
    for s in surveys:
        logger.info(f"  {'/'.join(s)}")
    if o.list or not surveys:
        return 0 if surveys else 1
    rows = update_master(surveys)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
