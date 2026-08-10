"""Loader for the shipped calibration sets.

Two files ship with the project and both land here:

    data/calibration_set_123k.json.gz   123,361 sessions, columnar, gzipped
    data/calibration_set.json           13,849 sessions, one object per row

The larger one is columnar (a `columns` list plus a list of lists) purely for
size: one object per row costs three times the bytes for the same numbers.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FULL = DATA / "calibration_set_123k.json.gz"
SMALL = DATA / "calibration_set.json"


def _read(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def load(path: Path | None = None) -> tuple[list[dict], dict]:
    """Return (rows as dicts, metadata). Accepts either shipped format."""
    if path is None:
        path = FULL if FULL.exists() else SMALL
    raw = _read(Path(path))
    meta = raw.get("_meta", {})
    if "columns" in raw:                       # columnar
        cols = raw["columns"]
        rows = [dict(zip(cols, r)) for r in raw["rows"]]
    else:                                      # one object per row
        rows = raw["rows"]
    return rows, meta


def describe(meta: dict) -> str:
    return (f"{meta.get('n', '?')} sessions · captured {meta.get('captured_utc', '?')}\n"
            f"{meta.get('privacy', '')}")
