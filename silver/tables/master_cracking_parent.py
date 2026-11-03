"""
master_cracking_parent
  one row per 1 km per lane.
  Built from the same 10 m cracking rows as master_cracking (D4 defect report, else NSV cracks sheet):
  cracking % = average of the 10 m cracking % in the km (config.PARENT_AGGREGATION); area_sqm = total of the 10 m sq m.
  breach / severity / ratings use the aggregated %. road_condition: worst D4 label in the block → breach.
  Every segment is written twice: 'As per CA' row + 'As per IRC' twin row.
Run alone:  python tables/master_cracking_parent.py [--no-excel]
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.master_cracking import build_level
from common.output import run_table, standalone

TABLE = 'master_cracking_parent'
LEVEL = 'parent'


def build(prj):
    return build_level(prj, TABLE, LEVEL)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write)


if __name__ == '__main__':
    standalone(run)
