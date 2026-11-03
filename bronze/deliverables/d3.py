"""
d3.py - Deliverable 3: GIS Mapping
    csv files in any sub-folder, e.g. 'GIS Mapping -As Per CA/...' and 'GIS Mapping -As per IRC/...'

Run:    python main.py d3                       every survey that has a d3 folder
        python main.py d3 --upc N-08004-03001-HR  one survey
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # allows: python deliverables/d3.py ...

from nsv.pipeline import extract_csv_folder
from nsv.run import Run

DELIVERABLE = "d3"


# =============================================================================
# WHAT to extract
#   Every csv under the folder. If its folder or file name contains one of the phrases (whole words),
#   it is saved under raw/<group>/, otherwise under raw/ungrouped/.
# =============================================================================
GROUPS = {
    "ca":  ["as per ca", "ca"],
    "irc": ["irc"],
}


# =============================================================================
# HOW: the flow
# =============================================================================
def run(survey):
    run = Run(DELIVERABLE, survey)          # input: survey's raw folder, output: its bronze folder

    # ---- block 1: all csv files ----
    extract_csv_folder(run, run.input_folder, GROUPS)

    return run.finish()


if __name__ == "__main__":                 # python deliverables/d3.py [--zone A ...] = python main.py d3 [...]
    from main import main
    sys.exit(main(["d3", *sys.argv[1:]]))
