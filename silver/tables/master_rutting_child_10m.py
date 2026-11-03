"""
master_rutting_child_10m
  one row per 10 m per lane (D4 rows as they are). parent_* = its 1 km block (DLM notebook).
  metric = rut depth (mm); 100 m / 1 km = average of the 10 m rut depths.
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_rutting_child_10m.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.segments import build_linear_table
from common.output import run_table, standalone

TABLE = 'master_rutting_child_10m'
DEFECT = 'rutting'
LEVEL = 'child_10m'


def build(prj):
    return build_linear_table(prj, DEFECT, LEVEL)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
