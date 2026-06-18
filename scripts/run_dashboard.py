#!/usr/bin/env python3
"""Launch the paper-live monitoring dashboard (FastAPI, :8765).

Documentación: ``docs/dashboard.md``. Export estático del mismo payload:
``scripts/export_dashboard_payload.py``.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.server import main

if __name__ == "__main__":
    main()
