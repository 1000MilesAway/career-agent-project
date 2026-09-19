"""What the scan found, kept across restarts.

Separate from history.py on purpose. `scan-history.tsv` answers "did we already
show this one" and its whole job is to hide a posting from the next scan.
This file answers "what is on the user's screen", so it must survive a reload
with the scores already paid for — otherwise reopening the page silently asks
the user to buy the same evaluations again.

Rows accumulate across scans and merge by vacancy id: a second scan refreshes
salary and grade but never drops a score that was already paid for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths

ROOT = paths.ROOT


def _found() -> Path:
    """The live list. Resolved per call so PROBE_OUT always wins — see paths.py."""
    return paths.out_file("found.json")


# Fields a fresh scan is allowed to refresh on an existing row. Everything the
# model produced (score, verdict, report) is left alone.
LISTING_FIELDS = (
    "url", "title", "company", "qualification", "remote",
    "location", "salary_text", "divisions", "skills", "published", "source",
)

# `text` is deliberately NOT refreshed. Only Telegram rows carry it, and there
# the dedup runs on the text itself — a different text is a different vacancy,
# not an update to this one. Overwriting it would silently re-point a stored
# report at a posting it never judged.

# What the apply steps produce, stored beside the report so re-opening a
# vacancy shows them without paying again.
ARTIFACT_KEYS = ("tailored_resume", "cover_letter", "interview_prep")

# Tracker states. career-ops ships nine (templates/states.yml); this is seven —
# its `evaluated` is implied by a stored score, and `skip` duplicates what the
# triage verdict already records, so neither earns a state of its own here.
# Terminal states end the story and outrank the rest; the order below is the
# lifecycle order for the others.
STATUSES: dict[str, dict] = {
    "none": {"title": "не решено", "terminal": False},
    "applied": {"title": "откликнулся", "terminal": False},
    "responded": {"title": "ответили", "terminal": False},
    "interview": {"title": "интервью", "terminal": False},
    "offer": {"title": "оффер", "terminal": True},
    "rejected": {"title": "отказ", "terminal": True},
    "discarded": {"title": "отложено", "terminal": True},
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load(path: Path | None = None) -> list[dict]:
    """Every vacancy found so far, newest scan first. Never raises."""
    path = path or _found()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A half-written file must not take the page down with it.
        return []
    rows = data.get("rows") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("id")]


def save(rows: list[dict], path: Path | None = None) -> Path:
    path = path or _found()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": _now(), "rows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge(postings: list[dict], path: Path | None = None) -> list[dict]:
    """Add a scan's results to the stored list, refreshing what the listing knows.

    Returns the full accumulated list, newest first.
    """
    stored = load(path)
    by_id = {r["id"]: dict(r) for r in stored}
    order = [r["id"] for r in stored]

    for p in postings:
        vid = str(p.get("id") or "")
        if not vid:
            continue
        if vid in by_id:
            # Keep the score; refresh only what the listing restates.
            row = by_id[vid]
            for key in LISTING_FIELDS:
                if p.get(key) is not None:
                    row[key] = p[key]
            row["last_seen"] = _now()
        else:
            by_id[vid] = {**p, "id": vid, "first_seen": _now(), "last_seen": _now()}
            order.insert(0, vid)

    rows = [by_id[i] for i in order]
    save(rows, path)
    return rows


def set_score(
    vacancy_id: str,
    report: dict[str, Any],
    usage: dict | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Attach an evaluation to a stored vacancy, so a reload keeps it."""
    rows = load(path)
    for row in rows:
        if row.get("id") == str(vacancy_id):
            row["score"] = report.get("score")
            row["verdict"] = report.get("verdict")
            row["report"] = report
            row["scored_at"] = _now()
            if usage:
                row["rub"] = round(usage.get("total_rub", 0.0), 4)
            break
    else:
        return rows
    save(rows, path)
    return rows


def set_artifacts(
    vacancy_id: str,
    results: dict[str, Any],
    usage: dict | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Attach tailored resume / cover letter / interview prep to a stored vacancy.

    Kept beside the report and under the same rule: what the user paid for is
    shown again for free. Costs accumulate — a second run that adds the letter
    to an already-evaluated vacancy must not erase what the evaluation cost.
    """
    rows = load(path)
    for row in rows:
        if row.get("id") != str(vacancy_id):
            continue
        stored = dict(row.get("artifacts") or {})
        for key in ARTIFACT_KEYS:
            if results.get(key):
                stored[key] = results[key]
        if not stored:
            # Nothing usable came back. Writing an empty dict would make the row
            # count as "has materials" and hide the failure behind a label.
            return rows
        row["artifacts"] = stored
        row["artifacts_at"] = _now()
        if usage:
            row["rub"] = round(row.get("rub", 0.0) + usage.get("total_rub", 0.0), 4)
        break
    else:
        return rows
    save(rows, path)
    return rows


def set_status(
    vacancy_id: str,
    status: str,
    note: str = "",
    path: Path | None = None,
) -> list[dict]:
    """Move a vacancy along the tracker. Unknown status is refused, not stored.

    Transitions are appended, never overwritten: the current state answers
    "where is this now", the log answers "when did it get there" — the same
    split career-ops keeps between its tracker table and status-log.tsv.
    """
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")

    rows = load(path)
    for row in rows:
        if row.get("id") != str(vacancy_id):
            continue
        previous = row.get("status")
        if previous == status and not note:
            return rows
        row["status"] = status
        row["status_at"] = _now()
        log = list(row.get("status_log") or [])
        log.append({"at": _now(), "from": previous, "to": status, "note": note})
        row["status_log"] = log
        break
    else:
        return rows
    save(rows, path)
    return rows


def set_saved(vacancy_id: str, saved: bool = True, path: Path | None = None) -> list[dict]:
    """Put a vacancy on the shortlist, or take it off.

    The shortlist is the boundary between the two halves of the product: search
    throws away most of what it finds, the shortlist is what the user decided to
    act on. Only the user moves a vacancy across it — no scan and no model does.
    """
    rows = load(path)
    for row in rows:
        if row.get("id") != str(vacancy_id):
            continue
        if bool(row.get("saved")) == saved:
            return rows
        row["saved"] = saved
        row["saved_at"] = _now() if saved else None
        break
    else:
        return rows
    save(rows, path)
    return rows


def saved(path: Path | None = None) -> list[dict]:
    """The shortlist, newest addition first."""
    rows = [r for r in load(path) if r.get("saved")]
    return sorted(rows, key=lambda r: r.get("saved_at") or "", reverse=True)


def find(vacancy_id: str, path: Path | None = None) -> dict | None:
    for row in load(path):
        if row.get("id") == str(vacancy_id):
            return row
    return None


def spend(path: Path | None = None) -> dict:
    """What the user has actually bought, counted off the stored rows.

    Deliberately not the CLI's `cost` summary: that totals every run file in
    out/, including experiments. This answers the question the screen asks —
    what did this session's list cost, and what is still free to look at.
    """
    rows = load(path)
    scored = [r for r in rows if r.get("score") is not None]
    with_artifacts = [r for r in rows if r.get("artifacts")]
    return {
        "found": len(rows),
        "scored": len(scored),
        "with_artifacts": len(with_artifacts),
        "rub": round(sum(float(r.get("rub") or 0) for r in rows), 3),
        # Free to reopen: a report or artifact already paid for.
        "free_to_reopen": len(scored) + len(with_artifacts),
    }


def clear(path: Path | None = None) -> None:
    """Forget the search results — but never the shortlist.

    "Очистить" lives on the search page and means "this batch was not useful".
    A vacancy the user deliberately put on the shortlist is the opposite of
    that, and it carries reports and materials they paid for. Dropping those
    from a button labelled "clear the list of results" would be a data loss
    the label never warned about.
    """
    save([r for r in load(path) if r.get("saved")], path)
