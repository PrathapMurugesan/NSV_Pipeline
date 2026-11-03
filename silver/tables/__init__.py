"""
One module per silver table. Each has:
    TABLE        output table name
    build(prj)   returns the DataFrame (before PCI / ratings / schema)
    run(prj)     build + finalize + write
Run one table:  python tables/master_rutting_child_10m.py [--no-excel]
"""
