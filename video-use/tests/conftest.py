from __future__ import annotations

import sys
from pathlib import Path


HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))
