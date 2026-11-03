"""
log.py - one logger for the whole project.

Every module does:   logger = get_logger(__name__)      -> names like "nsv.excel", "nsv.pipeline"
All of them write to the console. Each Run (see run.py) also captures the lines of its own run
and saves them as run.log next to the results.
"""
import logging
import sys

FORMAT = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-13s | %(message)s", "%Y-%m-%d %H:%M:%S")


def get_logger(name="nsv"):
    root = logging.getLogger("nsv")
    if not root.handlers:                         # set up only once
        root.setLevel(logging.INFO)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(FORMAT)
        root.addHandler(console)
        root.propagate = False                    # don't print twice through Python's root logger
    return logging.getLogger(name if name.startswith("nsv") else f"nsv.{name}")
