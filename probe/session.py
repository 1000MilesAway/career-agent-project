"""The parsed resume, kept across restarts.

Parsing a CV costs money (~0.05 RUB) and the probe re-parsed it every time the
server restarted. Separate from found.py because the lifecycles differ: there is
one resume and many vacancies, and replacing the resume invalidates nothing on
the found list.

Single-user by design, like the rest of the probe. The product stores this per
user in Postgres.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import paths

ROOT = paths.ROOT


def _session() -> Path:
    """Resolved per call so PROBE_OUT always wins — see paths.py."""
    return paths.out_file("resume-state.json")


def load(path: Path | None = None) -> dict | None:
    """The stored resume, or None. Never raises on a damaged file."""
    path = path or _session()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("profile") else None


def save(
    profile: dict,
    resume_text: str,
    resume_name: str,
    path: Path | None = None,
) -> Path:
    path = path or _session()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep what this function does not own: a confirmed search profile was
    # accepted by the user and must not be dropped by a new CV upload.
    payload = load(path) or {}
    payload.update(
        profile=profile,
        resume_text=resume_text,
        resume_name=resume_name,
        saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def clear(path: Path | None = None) -> None:
    path = path or _session()
    path.unlink(missing_ok=True)


def save_search_profile(profile: dict, path: Path | None = None) -> None:
    """Store the accepted search profile alongside the resume."""
    path = path or _session()
    current = load(path) or {}
    current["search_profile"] = profile
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def save_archetypes(archetypes: dict, path: Path | None = None) -> None:
    """Store the candidate's target roles, derived once from the CV.

    Kept next to the resume because they are derived from it: a new CV makes
    them stale, and triage should not screen against the wrong person.
    """
    path = path or _session()
    current = load(path) or {}
    current["archetypes"] = archetypes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
