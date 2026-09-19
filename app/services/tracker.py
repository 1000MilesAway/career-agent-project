"""Where a shortlisted vacancy stands.

Seven states, carried over from the demo. career-ops ships nine
(templates/states.yml); `evaluated` is implied here by a stored evaluation and
`skip` duplicates the triage verdict, so neither earns a state of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models import Application, Vacancy

STATUSES: dict[str, dict] = {
    "none": {"title": "не решено", "terminal": False},
    "applied": {"title": "откликнулся", "terminal": False},
    "responded": {"title": "ответили", "terminal": False},
    "interview": {"title": "интервью", "terminal": False},
    "offer": {"title": "оффер", "terminal": True},
    "rejected": {"title": "отказ", "terminal": True},
    "discarded": {"title": "отложено", "terminal": True},
}


def set_status(vacancy: Vacancy, status: str, note: str = "") -> Application:
    """Move a vacancy along the tracker. An unknown status is refused, not stored.

    The transition is appended to `status_log` rather than replacing it: the
    column answers "where is this now", the log answers "when did it get
    there". career-ops keeps the same split between its tracker table and
    status-log.tsv.
    """
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")

    now = datetime.now(UTC)
    application = vacancy.application
    if application is None:
        application = Application(vacancy_id=vacancy.id, status_log=[])
        vacancy.application = application

    previous = application.status
    if previous == status and not note:
        return application

    application.status = status
    application.status_at = now
    application.note = note or application.note
    # Reassigned rather than appended in place: SQLAlchemy does not track
    # mutation of a JSONB list, so an in-place append would never be written.
    application.status_log = [
        *(application.status_log or []),
        {"at": now.isoformat(), "from": previous, "to": status, "note": note},
    ]
    return application
