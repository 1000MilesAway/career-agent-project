"""Scan history — so a repeat scan never pays to evaluate the same vacancy twice.

career-ops keeps data/scan-history.tsv for exactly this. In the product this
becomes a table; here a TSV file is enough and stays readable by hand.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "out" / "scan-history.tsv"
COLUMNS = ["id", "first_seen", "source", "url", "title", "company", "status", "score"]


def load(path: Path | None = None) -> dict[str, dict]:
    """Everything seen before, keyed by vacancy id."""
    path = path or HISTORY
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f, delimiter="\t") if row.get("id")}


def append(rows: list[dict], path: Path | None = None) -> int:
    """Add rows for vacancies not recorded yet. Returns how many were written."""
    path = path or HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    known = load(path)
    fresh = [r for r in rows if r.get("id") and r["id"] not in known]
    if not fresh:
        return 0

    is_new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t", extrasaction="ignore")
        if is_new_file:
            w.writeheader()
        today = date.today().isoformat()
        for r in fresh:
            w.writerow({**{"first_seen": today, "source": "habr"}, **r})
    return len(fresh)


def split_seen(postings: list, path: Path | None = None) -> tuple[list, list]:
    """Split into (not seen before, already seen)."""
    known = load(path)
    new = [p for p in postings if p.id not in known]
    old = [p for p in postings if p.id in known]
    return new, old
