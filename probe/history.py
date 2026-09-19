"""Scan history — so a repeat scan never pays to evaluate the same vacancy twice.

career-ops keeps data/scan-history.tsv for exactly this. In the product this
becomes a table; here a TSV file is enough and stays readable by hand.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from . import paths

ROOT = paths.ROOT


COLUMNS = ["id", "first_seen", "source", "url", "title", "company", "status", "score"]


def _history() -> Path:
    """Resolved per call so PROBE_OUT always wins — see paths.py."""
    return paths.out_file("scan-history.tsv")


def load(path: Path | None = None) -> dict[str, dict]:
    """Everything seen before, keyed by vacancy id."""
    path = path or _history()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f, delimiter="\t") if row.get("id")}


def append(rows: list[dict], path: Path | None = None) -> int:
    """Add rows for vacancies not recorded yet. Returns how many were written."""
    path = path or _history()
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


def clear(keep_ids: list[str] | None = None, path: Path | None = None) -> None:
    """Forget what was already shown, except for the ids handed in.

    Only ever called together with clearing the found list. The two answer
    different questions — "what is on screen" and "what have we shown before" —
    but dropping one without the other strands the vacancies in between:
    history calls them seen, and there is no list left to see them in.

    `keep_ids` is the shortlist, which survives the clear. Forgetting those
    would send them back through the next scan as new finds, and the user
    would be looking at a duplicate of a vacancy they already saved.
    """
    path = path or _history()
    keep = {str(i) for i in keep_ids or []}
    if not keep:
        path.unlink(missing_ok=True)
        return

    known = load(path)
    kept = [row for vid, row in known.items() if vid in keep]
    path.unlink(missing_ok=True)
    if kept:
        append(kept, path)


def split_seen_rows(rows: list[dict], path: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Same split for plain rows. Telegram has no Posting object to hand over."""
    known = load(path)
    new = [r for r in rows if str(r.get("id")) not in known]
    old = [r for r in rows if str(r.get("id")) in known]
    return new, old
