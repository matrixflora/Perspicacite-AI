"""Perspicacité v2 web app entry point (legacy shim).

Kept as a backward-compat target for `cli.py`'s loader and for users who
still run `python web_app_full.py`. New code should import directly from
`perspicacite.web`.

Task 10 of the web-app-split refactor will delete this file and update
cli.py to import from perspicacite.web directly.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from perspicacite.web import app  # noqa: F401  (re-export for cli.py)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("perspicacite.web.app:app", host="0.0.0.0", port=8000, reload=False)
