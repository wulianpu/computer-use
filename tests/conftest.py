"""Shared test setup: make scripts/ importable and expose repo paths."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"

for path in (str(SCRIPTS), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

PYTHON = sys.executable
