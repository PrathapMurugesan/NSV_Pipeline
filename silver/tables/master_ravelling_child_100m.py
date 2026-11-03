"""
master_ravelling_child_100m
  one row per 100 m per lane (10 m rows grouped). parent_* = its 1 km block.
  metric = ravelling area × 100 ÷ 35 (% of lane); 100 m / 1 km = average of 10 m %.
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_ravelling_child_100m.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.segments import build_linear_table
from common.output import run_table, standalone

TABLE = 'master_ravelling_child_100m'
DEFECT = 'ravelling'
LEVEL = 'child_100m'


def build(prj):
    return build_linear_table(prj, DEFECT, LEVEL)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
