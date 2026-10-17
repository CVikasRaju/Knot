#!/usr/bin/env python3
"""Seed the Knot FalkorDB tenants with the demo enterprise."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seed import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
