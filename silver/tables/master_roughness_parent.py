"""
master_roughness_parent
  one row per 1 km per lane (10 m rows grouped). parent_* = its 1 km block.
  metric = roughness BI (mm/km); 100 m / 1 km = average of the BI values (config.ROUGHNESS_AGGREGATION).
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_roughness_parent.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.segments import build_linear_table
from common.output import run_table, standalone

TABLE = 'master_roughness_parent'
DEFECT = 'roughness'
LEVEL = 'parent'


def build(prj):
    return build_linear_table(prj, DEFECT, LEVEL)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
