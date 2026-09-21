"""Proposed changes the user has to confirm before anything is written.

The rule comes from career-ops (`modes/titles.md`): the agent proposes, nothing
is written silently. Until now that rule lived in a prompt and in our heads —
`_load_or_build_profile` generated a search profile and wrote it to disk in the
same breath.

The shape is borrowed from DeepSeek Harness, where approval is its own plugin
(`interaction/`) instead of being spread across the code that happens to need
it: one place owns "this is pending", "this is what changes", "this was
accepted". Here that is a small dataclass plus a file, because there is no
database — in the product it becomes a table with the same three states.

A proposal is never applied by creating it. `accept()` returns the new value;
the caller writes it. That split is the whole point: generating and writing are
two separate acts, and the user stands between them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"

# Fields of a search profile worth showing as a diff. Order matters: this is
# the order the user reads them in.
DIFF_FIELDS = (
    "queries",
    "positive",
    "negative",
    "divisions_negative",
    "min_qualification",
)

# Guard lists are added to, never replaced. The shipped template knows the
# classic traps (frontend, mobile, QA); the model knows this particular CV.
# Replacing one with the other loses half the knowledge — which is exactly what
# happened on 2026-09-14: an accepted proposal dropped the template's frontend
# and mobile stop-words, and Android/iOS/React vacancies walked into the results.
#
# Everything else (queries, positive keywords, minimum grade) IS replaced: there
# the model read the CV and should win.
UNION_FIELDS = ("negative", "divisions_negative")


def merge_profile(current: dict, proposed: dict) -> dict:
    """Apply a proposal: union the guard lists, replace the rest."""
    merged = dict(current)
    for key, value in proposed.items():
        if key in UNION_FIELDS:
            existing = current.get(key) or []
            incoming = value or []
            # Keep the template's order first, append what the model adds.
            seen = {str(v).casefold() for v in existing}
            merged[key] = list(existing) + [
                v for v in incoming if str(v).casefold() not in seen
            ]
        else:
            merged[key] = value
    return merged


def _as_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(v) for v in value}
    return {str(value)} if value else set()


@dataclass
class FieldDiff:
    """What changes in one field, in the user's terms."""

    field: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed

    def as_dict(self) -> dict:
        return {"field": self.field, "added": self.added, "removed": self.removed}

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append("+ " + ", ".join(self.added))
        if self.removed:
            parts.append("− " + ", ".join(self.removed))
        return f"{self.field}: " + "; ".join(parts)


def diff(current: dict, proposed: dict, fields: tuple[str, ...] = DIFF_FIELDS) -> list[FieldDiff]:
    """What the proposal would change. Only fields the proposal actually sets.

    Shows what accepting really does, so a guard list reports additions only —
    promising a removal that merge_profile will not perform would be a lie.
    """
    out: list[FieldDiff] = []
    for name in fields:
        if name not in proposed:
            continue
        before, after = _as_set(current.get(name)), _as_set(proposed.get(name))
        removed = [] if name in UNION_FIELDS else sorted(before - after)
        d = FieldDiff(name, sorted(after - before), removed)
        if not d.empty:
            out.append(d)
    return out


@dataclass
class Proposal:
    """One change waiting for a yes or a no."""

    kind: str
    current: dict
    proposed: dict
    rationale: str = ""
    status: str = PENDING
    created_at: str = ""
    decided_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat(timespec="seconds")

    @property
    def changes(self) -> list[FieldDiff]:
        return diff(self.current, self.proposed)

    @property
    def is_noop(self) -> bool:
        """Nothing to confirm — the proposal matches what is already there."""
        return not self.changes

    def accept(self) -> dict:
        """Mark accepted and return the value to write. Writing is the caller's job."""
        self.status = ACCEPTED
        self.decided_at = datetime.now(UTC).isoformat(timespec="seconds")
        return merge_profile(self.current, self.proposed)

    def reject(self) -> dict:
        """Mark rejected and return the value unchanged."""
        self.status = REJECTED
        self.decided_at = datetime.now(UTC).isoformat(timespec="seconds")
        return dict(self.current)

    def describe(self) -> str:
        """Plain-text summary for the CLI."""
        if self.is_noop:
            return "Изменений нет — предлагаемый профиль совпадает с текущим."
        lines = [d.describe() for d in self.changes]
        if self.rationale:
            lines.append(f"Почему: {self.rationale}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "status": self.status,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "changes": [d.as_dict() for d in self.changes],
            "current": self.current,
            "proposed": self.proposed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Proposal:
        return cls(
            kind=d.get("kind", ""),
            current=d.get("current", {}),
            proposed=d.get("proposed", {}),
            rationale=d.get("rationale", ""),
            status=d.get("status", PENDING),
            created_at=d.get("created_at", ""),
            decided_at=d.get("decided_at", ""),
        )


def save(proposal: Proposal, path: Path) -> Path:
    """Park a proposal next to the thing it would change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(proposal.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load(path: Path) -> Proposal | None:
    if not path.exists():
        return None
    return Proposal.from_dict(json.loads(path.read_text(encoding="utf-8")))
