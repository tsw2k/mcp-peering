"""Make the src-layout package importable without installing it.

Lets ``pytest`` run straight from a checkout; an editable install
(``pip install -e .``) keeps working as before.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
