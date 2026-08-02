#!/usr/bin/env python3
"""Run the 2020 MEPS adherence workflow from ``2020_clean.ipynb``.

Uses ``clean_meps.run_exports``. Outputs -> ``../output/2020/tables/`` and ``../output/2020/graphs/``.
"""

import sys

from clean_meps import run_exports

YEAR = 2020

if __name__ == "__main__":
    try:
        run_exports(YEAR)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr, flush=True)
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
