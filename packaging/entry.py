"""Frozen entry: spawn workers before importing Qt or the geospatial engine."""
import multiprocessing

if __name__=='__main__':
    multiprocessing.freeze_support()
    from geotrace.launcher import main
    raise SystemExit(main())
