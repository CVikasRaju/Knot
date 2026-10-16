#!/usr/bin/env python3
"""Convenience entry point: run a ChronosGraph demo scenario.

Kept as a thin shim so ``python scripts/run_scenario.py`` works exactly as the
README documents, without the reader needing to know about ``python -m``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scenario import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
