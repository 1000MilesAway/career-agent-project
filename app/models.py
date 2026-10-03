"""The data model, following docs/03-architecture.md.

Two things carried over from the file-backed demo that preceded this schema,
because they were learned the expensive way and a fresh schema is exactly
where they get lost:

  - What the user paid for is never silently overwritten. In the demo a rescan
    refreshed listing fields but left score, report and artifacts alone. Here
    that separation is structural: listing data lives on `postings`,
    everything bought lives on `evaluations` and `artifacts`, and refreshing
    one cannot touch the other.
  - A rejection is never silent. `vacancies.screen_reason` holds why a posting
    was dropped by the free filters or triage, and the row stays. Hiding a
    vacancy without an explanation is the worst bug this pipeline can have.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base, TimestampMixin


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base, TimestampMixin):
    """An account: made by the admin CLI, by a confirmed email signup, or by a
    first login with Telegram.

    A Telegram sign-up has no email or password — Telegram is its only way in,
    which is why such an account cannot unlink it (see api/auth.py).
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(email IS NOT NULL AND password_hash IS NOT NULL) OR telegram_id IS NOT NULL",
            name="has_a_login",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # When the email was confirmed by a link from a letter. A self-signup
    # cannot log in until it is set; accounts made by the admin CLI are born
    # confirmed. Null for a Telegram-only account, which has no email.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the last confirmation letter went out: repeat signups are throttled
    # by it, because Resend's free plan is 100 letters a day for everyone.
    verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # What the cabinet calls the user instead of the login email. Optional:
    # the cabinet falls back to the email, or to the Telegram name for an
    # account without one, until the user names themselves.
    name: Mapped[str | None] = mapped_column(String(80))

    # Consent to process personal data. A resume IS personal data, so the
    # timestamp is evidence, not a UI flag: it records that this specific
    # account agreed, and when.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # What this account has cost the owner, in roubles at the real (peak
    # included) price. The owner's accounting only: users never see roubles,
    # they see a share of their quota below.
    spent_rub: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)

    # The plan: paid while this is in the future, free otherwise. A date
    # rather than a flag, so a lapsed subscription turns itself off.
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Quota windows, in USD at the off-peak price (`app/services/spend.py`).
    # Each counter belongs to the period its date names — the Moscow day, the
    # Monday of the Moscow week; a counter from an older period is stale and
    # read as reset, so nothing has to run at midnight to clear them.
    quota_day: Mapped[date | None] = mapped_column(Date)
    quota_day_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    quota_week: Mapped[date | None] = mapped_column(Date)
    quota_week_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)

    # A Telegram account linked from the cabinet, or the one the account was
    # created by on its first Telegram login; either way it then logs in by
    # itself. The name is for display
    # ("@handle" or the first name when there is no handle) and is refreshed on
    # every Telegram login; the id is what identifies.
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_name: Mapped[str | None] = mapped_column(String(128))

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


class Posting(Base, TimestampMixin):
    """A vacancy as a source published it. One row for everyone.

    The shared half of what used to be one per-user table: listing data, the
    posting's text and whether it is still open are facts about the posting,
    not about whoever found it, so they are fetched, checked and stored once.
    What a user decided or bought lives on `vacancies`, which points here —
    a refresh of this row cannot reach any of it.

    `(source, external_id)` is the source's own id (Habr's numeric id, a
    Telegram post id), which is what makes a second sighting an update rather
    than a second row. See docs/decisions.md, 2026-10-03 (shared pool).
    """

    __tablename__ = "postings"

    id: Mapped[uuid.UUID] = _uuid_pk()

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # habr | telegram | ...
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Which part of a source it came from, where the user chooses parts: an
    # employer board's group ("ru" / "global"), a Telegram channel's group
    # ("core" / "adjacent"). Matching reads only the parts this scan asked for.
    segment: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(1024))

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    qualification: Mapped[str | None] = mapped_column(String(32))
    salary_text: Mapped[str | None] = mapped_column(String(255))
    # The band as numbers, so the free filters can judge a stored posting
    # again without the source: a profile change re-matches the pool, and the
    # pool has no page to re-read. Same meaning as `Posting.salary_*` in core.
    salary_from: Mapped[int | None] = mapped_column(Integer)
    salary_to: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    location: Mapped[str | None] = mapped_column(String(255))
    remote: Mapped[bool | None] = mapped_column(Boolean)
    # Russian IT accreditation as the source states it; None = not stated.
    accredited: Mapped[bool | None] = mapped_column(Boolean)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    divisions: Mapped[list[str] | None] = mapped_column(JSONB)
    skills: Mapped[list[str] | None] = mapped_column(JSONB)

    # The posting's own text. For Telegram this is the ONLY copy: t.me has no
    # page to re-fetch, so a lost text means an unevaluatable vacancy. Never
    # overwritten by a later sighting — dedup there runs on the text itself,
    # so different text is a different posting, not an update to this one.
    # The one write after creation is `needs_text` below being served.
    raw_text: Mapped[str | None] = mapped_column(Text)
    # The text still lacks the description the posting links to: a Telegram
    # teaser whose page was not read yet, a board that lists no descriptions.
    # Fetched once a user's free filters keep the posting — someone else's
    # bandwidth is spent on survivors only — and then for everyone.
    needs_text: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Where that description lives, for a Telegram teaser.
    description_url: Mapped[str | None] = mapped_column(String(1024))

    # The same vacancy seen elsewhere first (core/dedup.py). Points at the
    # root of the cluster, never at another copy. Who sees which copy is
    # decided per user at match time: a copy whose original that user's
    # filters drop is judged on its own, so it is never hidden for good.
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("postings.id", ondelete="SET NULL")
    )
    # company|title, and a SimHash of the text for Telegram reposts.
    dedup_key: Mapped[str | None] = mapped_column(String(255))
    text_fp: Mapped[str | None] = mapped_column(String(16))

    # When a fetch last returned it. Matching reads postings seen recently;
    # one that dropped off every listing ages out of new matches.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Whether the posting is still open (core/liveness.py). `closed_at` is set
    # only on positive evidence — a 404/410 or the page's archive banner; an
    # unreachable page leaves it alone. Checked once for everyone.
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(String(255))

    # An employer's feed read whole without this posting in it: how many
    # such reads in a row, and since when. Enough of them, long enough, is
    # the company taking the posting down (services/pool.py `mark_absent`);
    # a sighting resets both. Only feeds count — see employers.FEED_KINDS.
    missed_reads: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_postings_source_ext"),
        Index("ix_postings_source_seen", "source", "last_seen_at"),
        Index("ix_postings_dedup_key", "dedup_key"),
    )

    @property
    def cluster_id(self) -> uuid.UUID:
        """The original this posting copies, or itself."""
        return self.duplicate_of_id or self.id


class SourceFetch(Base):
    """When one listing was last read from a source, for everyone.

    The cache that makes the pool save requests: a scan asks only for the
    listings nobody read recently and matches the rest from `postings`. `key`
    names the listing (a Habr query with its parameters, an employer group
    with its queries, a Telegram channel group); `extra` keeps what the fetch
    reported — channels read, boards that failed — so a scan answered from
    the cache reports the same problems as the scan that paid for the fetch.
    """

    __tablename__ = "source_fetches"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Vacancy(Base, TimestampMixin):
    """One user's view of a posting: how far it got for them, and their calls.

    Nothing about the posting itself is stored here — it is read through
    `posting`, which is shared. Everything bought hangs off this row
    (`evaluations`, `application`), so it stays the user's alone.

    A row exists only for a posting that passed this user's free filters, or
    that the user acted on. What the free filters dropped is not stored per
    user: the filters are deterministic and free, so the reason is computed
    from the pool when asked (`GET /api/vacancies/screened`) — the discard log
    without a copy of the pool per user. `screened_out` survives only on rows
    the user acted on before their profile stopped matching them.
    """

    __tablename__ = "vacancies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Joined on every load: almost every reader shows the posting's fields,
    # and a lazy load in async code is an error, not a slow query.
    posting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("postings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # How far the posting got, and why it stopped. `screen_reason` is the
    # product's version of career-ops' discard.log: a dropped vacancy stays
    # visible and carries its reason in one line.
    stage: Mapped[str] = mapped_column(String(32), default="collected", nullable=False)
    screen_reason: Mapped[str | None] = mapped_column(String(512))
    triage_verdict: Mapped[str | None] = mapped_column(String(16))  # worth | maybe | skip

    # "Not interested" from the user. A person's call, like `is_saved`, and
    # like every drop it is reversible and visible: the list folds hidden rows
    # under their own toggle instead of deleting them.
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The user's own shortlist. Only a person moves a vacancy across this line —
    # no scan and no model does.
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="vacancies")
    posting: Mapped[Posting] = relationship(lazy="joined", innerjoin=True)
    evaluations: Mapped[list[Evaluation]] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan"
    )
    application: Mapped[Application | None] = relationship(
        back_populates="vacancy", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_vacancies_user_posting"),
        Index("ix_vacancies_user_stage", "user_id", "stage"),
    )

    # The posting's fields, read through. Read-only on purpose: a write to
    # one of these from user code would change the posting for everyone, so
    # code that means to do that says `vacancy.posting.<field>`.
    source = property(lambda self: self.posting.source)
    external_id = property(lambda self: self.posting.external_id)
    url = property(lambda self: self.posting.url)
    title = property(lambda self: self.posting.title)
    company = property(lambda self: self.posting.company)
    qualification = property(lambda self: self.posting.qualification)
    salary_text = property(lambda self: self.posting.salary_text)
    location = property(lambda self: self.posting.location)
    remote = property(lambda self: self.posting.remote)
    accredited = property(lambda self: self.posting.accredited)
    published = property(lambda self: self.posting.published)
    divisions = property(lambda self: self.posting.divisions)
    skills = property(lambda self: self.posting.skills)
    raw_text = property(lambda self: self.posting.raw_text)
    checked_at = property(lambda self: self.posting.checked_at)
    closed_at = property(lambda self: self.posting.closed_at)
    closed_reason = property(lambda self: self.posting.closed_reason)


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
