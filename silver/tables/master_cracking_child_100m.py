"""
master_cracking_child_100m
  one row per 100 m per lane. parent_* = its 1 km block.
  Built from the same 10 m cracking rows as master_cracking (D4 defect report, else NSV cracks sheet):
  cracking % = average of the 10 m cracking % in the 100 m block; area_sqm = total of the 10 m sq m.
  breach / severity / ratings use the aggregated %. road_condition: worst D4 label in the block → breach.
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_cracking_child_100m.py [--no-excel]
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.master_cracking import build_level
from common.output import run_table, standalone

TABLE = 'master_cracking_child_100m'
LEVEL = 'child_100m'


def build(prj):
    return build_level(prj, TABLE, LEVEL)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
