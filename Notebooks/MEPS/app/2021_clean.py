#!/usr/bin/env python3
"""Run the 2021 MEPS adherence workflow from ``2021_clean.ipynb``.

Uses ``clean_meps.run_exports``. Outputs -> ``../output/2021/tables/`` and ``../output/2021/graphs/``.
"""

import sys

from clean_meps import run_exports

YEAR = 2021

if __name__ == "__main__":
    try:
        run_exports(YEAR)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr, flush=True)
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
