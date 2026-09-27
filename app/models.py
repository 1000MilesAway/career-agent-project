"""The data model, following docs/03-architecture.md.

Two things carried over from the file-backed demo that preceded this schema,
because they were learned the expensive way and a fresh schema is exactly
where they get lost:

  - What the user paid for is never silently overwritten. In the demo a rescan
    refreshed listing fields but left score, report and artifacts alone. Here
    that separation is structural: listing data lives on `vacancies`,
    everything bought lives on `evaluations` and `artifacts`, and refreshing
    one cannot touch the other.
  - A rejection is never silent. `vacancies.screen_reason` holds why a posting
    was dropped by the free filters or triage, and the row stays. Hiding a
    vacancy without an explanation is the worst bug this pipeline can have.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base, TimestampMixin


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base, TimestampMixin):
    """An account. Created by the admin CLI — there is no public registration."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Consent to process personal data. A resume IS personal data, so the
    # timestamp is evidence, not a UI flag: it records that this specific
    # account agreed, and when.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Spend accounting. No limit is enforced yet — the user asked to open the
    # service to testers without one — but the number has to exist before a
    # limit can ever be switched on, and it answers "what did this account
    # cost me" while testing.
    spent_rub: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)

    resumes: Mapped[list[Resume]] = relationship(back_populates="user", cascade="all, delete-orphan")
    vacancies: Mapped[list[Vacancy]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Resume(Base, TimestampMixin):
    """A CV and the structured profile parsed out of it.

    Parsing costs money, so the profile is stored rather than recomputed —
    the same reason the demo kept a parsed session on disk.
    """

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Derived from the CV, so a new CV makes them stale: triage must not screen
    # against the wrong person.
    archetypes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The search profile the user accepted. Separate from `profile` because the
    # user confirmed it and a re-parse of the CV must not silently replace it.
    search_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # An accepted chat edit makes a new row rather than rewriting this one: a
    # report bought against the old profile must keep matching the profile it
    # read, and a new resume_id is what lets the same vacancy be scored again.
    # See docs/decisions.md, 2026-09-22 (variant B).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    user: Mapped[User] = relationship(back_populates="resumes")

    __table_args__ = (
        # One active CV per user: the pipeline has to know which profile to
        # evaluate against without being told each time.
        Index(
            "uq_resumes_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=is_active.is_(True),
        ),
    )


class Vacancy(Base, TimestampMixin):
    """A posting, as collected. Holds listing data only — nothing bought.

    `external_id` is the source's own id (Habr's numeric id, a Telegram post
    id). Unique per user, which is what makes a rescan an update instead of a
    duplicate, and why the demo's `found.merge()` keyed on it.
    """

    __tablename__ = "vacancies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # habr | telegram | text | url
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    qualification: Mapped[str | None] = mapped_column(String(32))
    salary_text: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    remote: Mapped[bool | None] = mapped_column(Boolean)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    divisions: Mapped[list[str] | None] = mapped_column(JSONB)
    skills: Mapped[list[str] | None] = mapped_column(JSONB)

    # The posting's own text. For Telegram this is the ONLY copy: t.me has no
    # page to re-fetch, so a lost text means an unevaluatable vacancy. It is
    # never overwritten by a rescan — dedup there runs on the text itself, so
    # different text is a different vacancy, not an update to this one.
    raw_text: Mapped[str | None] = mapped_column(Text)

    # How far the posting got, and why it stopped. `screen_reason` is the
    # product's version of career-ops' discard.log: a dropped vacancy stays
    # visible and carries its reason in one line.
    stage: Mapped[str] = mapped_column(String(32), default="collected", nullable=False)
    screen_reason: Mapped[str | None] = mapped_column(String(512))
    triage_verdict: Mapped[str | None] = mapped_column(String(16))  # worth | maybe | skip

    # The same vacancy seen elsewhere first (core/dedup.py). A duplicate is
    # stored screened out with the original named in `screen_reason`, so it
    # costs no triage and no evaluation but stays visible — never dropped.
    # `dedup_key` is company|title; `text_fp` a SimHash of the text, for
    # Telegram reposts whose wrappers differ.
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vacancies.id", ondelete="SET NULL")
    )
    dedup_key: Mapped[str | None] = mapped_column(String(255))
    text_fp: Mapped[str | None] = mapped_column(String(16))

    # Whether the posting is still open (core/liveness.py). `closed_at` is set
    # only on positive evidence — a 404/410 or the page's archive banner;
    # an unreachable page leaves it alone. A closed row keeps everything the
    # user bought for it and stays visible, marked.
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(String(255))

    # "Not interested" from the user. A person's call, like `is_saved`, and
    # like every drop it is reversible and visible: the list folds hidden rows
    # under their own toggle instead of deleting them.
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The user's own shortlist. Only a person moves a vacancy across this line —
    # no scan and no model does.
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="vacancies")
    evaluations: Mapped[list[Evaluation]] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan"
    )
    application: Mapped[Application | None] = relationship(
        back_populates="vacancy", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_vacancies_user_source_ext"),
        Index("ix_vacancies_user_stage", "user_id", "stage"),
        Index("ix_vacancies_user_dedup_key", "user_id", "dedup_key"),
    )


class Evaluation(Base, TimestampMixin):
    """A paid judgement of one vacancy against one resume.

    Keyed by (vacancy, resume): re-uploading a CV is a new resume row, so a
    fresh evaluation is a new row too rather than an overwrite of the old one.
    The user paid for both and both stay readable.
    """

    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(32))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_rub: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)

    vacancy: Mapped[Vacancy] = relationship(back_populates="evaluations")
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("vacancy_id", "resume_id", name="uq_evaluations_vacancy_resume"),
    )


class Artifact(Base, TimestampMixin):
    """Tailored resume, cover letter or interview prep, built on an evaluation.

    One row per (evaluation, kind): buying the letter twice returns the stored
    one rather than paying again, which is what the demo meant by `cached`.
    An empty result is never written — in the demo that made a row count as
    "has materials" and hid the failure behind a label.
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # tailor | letter | interview
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cost_rub: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="artifacts")

    __table_args__ = (UniqueConstraint("evaluation_id", "kind", name="uq_artifacts_eval_kind"),)


class Application(Base, TimestampMixin):
    """Where a shortlisted vacancy stands. The tracker.

    `status` answers "where is this now", `status_log` answers "when did it get
    there". The demo kept these apart deliberately and so does career-ops
    (its tracker table vs status-log.tsv); transitions are appended, never
    overwritten.
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    vacancy: Mapped[Vacancy] = relationship(back_populates="application")


class Job(Base, TimestampMixin):
    """A queued LLM task. The queue is a table; the worker polls it.

    Redis would add an service to run for a workload measured in tens of jobs
    a day. Postgres already has the durability and the visibility — a stuck job
    can be read with SQL.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The worker's only query: oldest queued job first.
        Index("ix_jobs_status_created", "status", "created_at"),
    )


class ChatMessage(Base, TimestampMixin):
    """One turn of a conversation, stored verbatim.

    Verbatim matters: the API requires a tool result to follow the assistant
    message that asked for it, so an assistant message carrying `tool_calls`
    cannot be summarised or rewritten on the way in. `payload` therefore holds
    the message as the model returned it, and `core.chat` reads it back
    unchanged — that is why `parse_tool_calls` has to accept a plain dict as
    well as the SDK's object.

    History is per user, not per resume: the conversation survives a new CV.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # user | assistant | tool — the roles core.chat works with.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Position in the conversation. `created_at` cannot order these: every
    # message of one exchange is written in the same transaction and shares a
    # timestamp to the microsecond, so sorting by it returned the answer before
    # the question. The API requires a tool result to follow the message that
    # asked for it, so a scrambled history is not cosmetic — it is unreplayable.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # What this turn cost. Zero for a user message and for a tool result;
    # only the assistant's own replies are billed. Kept per message so
    # "why did this conversation cost that much" stays answerable.
    cost_rub: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)

    __table_args__ = (
        # The only query: this user's conversation, in order.
        Index("ix_chat_messages_user_seq", "user_id", "seq"),
    )


class Proposal(Base, TimestampMixin):
    """A change the chat prepared, waiting for the user to accept it.

    The product rule — the agent proposes, the user accepts — already exists as
    `core/proposals.py`, which deliberately separates "generate" from "write":
    `accept()` returns the new value and the caller writes it. This table is
    that rule given a place to live, because a chat produces proposals in
    batches and the user does not decide immediately.

    `current` and `proposed` are stored side by side so the diff shown on
    screen is built from what was true when the proposal was made. Silence is
    refusal: a proposal nobody accepts changes nothing, ever.
    """

    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which message proposed it, so the chat can show the decision in place.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), index=True
    )

    # Which tool asked for it: propose_profile_patch, set_vacancy_status, ...
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending | accepted | rejected — the three states from core/proposals.py.
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    # The arguments the model supplied, kept as given. A proposal that is
    # rewritten before storage cannot be audited against what was shown.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    current: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    proposed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # What the screen asks for: this user's undecided proposals.
        Index("ix_proposals_user_status", "user_id", "status"),
    )
