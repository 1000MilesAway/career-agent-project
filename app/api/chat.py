"""The chat: send a message, read the history, decide on a proposal.

Synchronous on purpose. A reply takes seconds, not the tens of seconds an
evaluation takes, so it does not need the `jobs` queue — and a chat that
answers through a polling loop stops feeling like a chat. The long steps stay
where they are.

Accepting a proposal is a separate request from making one. That split is the
whole confirmation layer: `core/proposals.py` returns the new value and the
caller writes it, and here the caller is an explicit POST the user makes.
Silence is refusal — a pending proposal nobody touches changes nothing.

A user has several threads; the history, the model's context and "clear"
are all per thread. Proposals stay listed for the whole account, because the
badge in the navigation counts every change waiting for a decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core import chat as chat_core
from core import tools
from core.llm import LLM, Ledger

from ..core.deps import SessionDep, UserDep
from ..models import ChatMessage, ChatThread, Proposal, Resume, Vacancy
from ..services import chat as chat_service
from ..services import revisions
from ..services.spend import charge, ensure_can_spend
from ..services.tracker import set_status

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_MESSAGE_CHARS = 4000


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # The vacancy the message is about — set by "Обсудить" on a saved row.
    vacancy_id: uuid.UUID | None = None


class VacancyRef(BaseModel):
    id: str
    title: str
    company: str | None = None


class VacancyCard(BaseModel):
    """A vacancy offered to pick, as the database has it — never as the
    model described it."""

    id: str
    title: str
    company: str | None = None
    salary_text: str | None = None
    location: str | None = None
    remote: bool | None = None
    score: float | None = None
    saved: bool = False
    url: str | None = None


class ThreadResponse(BaseModel):
    id: uuid.UUID
    title: str
    vacancy: VacancyRef | None = None
    last_message_at: datetime
    # Changes proposed in this thread and still waiting for a decision.
    pending: int = 0


class NewThreadRequest(BaseModel):
    # Set by "Обсудить": the thread of this vacancy, made on first use.
    vacancy_id: uuid.UUID | None = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ProposalResponse(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID | None = None
    kind: str
    status: str
    arguments: dict[str, Any]
    current: dict[str, Any] | None
    proposed: dict[str, Any] | None
    rationale: str | None
    created_at: datetime

    @classmethod
    def of(cls, p: Proposal, thread_id: uuid.UUID | None = None) -> ProposalResponse:
        return cls(
            id=p.id,
            thread_id=thread_id,
            kind=p.kind,
            status=p.status,
            arguments=p.arguments,
            current=p.current,
            proposed=p.proposed,
            rationale=p.rationale,
            created_at=p.created_at,
        )


class MessageResponse(BaseModel):
    """One turn, as the screen needs it."""

    role: str
    text: str
    created_at: datetime
    # Tool names that ran for this turn, so the UI can say what was looked at
    # rather than leaving a silent gap between question and answer.
    tools: list[str] = []
    # Answers to pick when the turn ended on a question (`ask_choice`). The
    # screen offers them as buttons; typing an answer of one's own still works.
    choices: list[str] = []
    # The vacancy a user message was written about, shown on its bubble.
    vacancy: VacancyRef | None = None
    # Vacancies to pick from when the turn ended on `choose_vacancy`.
    vacancies: list[VacancyCard] = []


class ChatResponse(BaseModel):
    thread: ThreadResponse
    reply: str
    messages: list[MessageResponse]
    proposals: list[ProposalResponse]


def _visible(
    message: ChatMessage, cards: dict[str, VacancyCard] | None = None
) -> MessageResponse | None:
    """What of a stored turn the user should see.

    Tool results are not shown: they are the model's working notes, and a
    vacancy's raw text pasted into the conversation would be noise. The names
    of the tools are shown instead, because a chat that silently did four
    things is a chat nobody can audit.
    """
    payload = message.payload or {}
    if message.role == chat_core.TOOL:
        return None

    text = (payload.get("content") or "").strip()
    question = _question(message)
    if question is not None and question["question"] not in text:
        # The model often asks with the tool alone and no words around it; the
        # question is then the whole reply, not a bubble left empty.
        text = f"{text}\n\n{question['question']}".strip()
    if not text:
        return None
    return MessageResponse(
        role=message.role,
        text=text,
        created_at=message.created_at,
        tools=_tool_names(message),
        choices=question["options"] if question else [],
        vacancy=_vacancy_ref(payload),
        vacancies=[
            cards[i] for i in (question["vacancy_ids"] if question else []) if i in (cards or {})
        ],
    )


def _vacancy_ref(payload: dict) -> VacancyRef | None:
    ref = payload.get(chat_core.VACANCY_KEY)
    if not isinstance(ref, dict) or not ref.get("id"):
        return None
    return VacancyRef(
        id=str(ref["id"]), title=str(ref.get("title") or ""), company=ref.get("company")
    )


def _question(message: ChatMessage) -> dict | None:
    """The question a stored assistant turn put to the user, if any."""
    if message.role != chat_core.ASSISTANT:
        return None
    calls = chat_core.parse_tool_calls((message.payload or {}).get("tool_calls"))
    return chat_core.asked(calls)


def _tool_names(message: ChatMessage) -> list[str]:
    names = [
        c.get("function", {}).get("name", "")
        for c in (message.payload or {}).get("tool_calls") or []
    ]
    # A question is the reply itself, not something the chat looked at.
    return [n for n in names if n and not (n in tools.BY_NAME and tools.BY_NAME[n].ends_turn)]


def _as_conversation(
    stored: list[ChatMessage], cards: dict[str, VacancyCard] | None = None
) -> list[MessageResponse]:
    """The turns worth showing, with the tool names folded into them.

    A round where the model only asked for tools carries no text — showing it
    would be an empty bubble on screen. Its tool names are carried forward and
    attached to the reply that followed, so the user still sees what was
    consulted: a chat that silently did four things cannot be audited.
    """
    out: list[MessageResponse] = []
    carried: list[str] = []
    for row in stored:
        carried.extend(_tool_names(row))
        visible = _visible(row, cards)
        if visible is None:
            continue
        # De-duplicated, order kept: two get_profile calls in one exchange are
        # one thing the user needs told about, not two.
        seen: dict[str, None] = {}
        for name in [*carried, *visible.tools]:
            seen.setdefault(name, None)
        visible.tools = list(seen)
        carried = []
        out.append(visible)
    return out


async def _history(session: SessionDep, thread_id: uuid.UUID) -> list[ChatMessage]:
    """The tail of one thread, in order.

    Ordered by `seq`, not by `created_at`: every message of one exchange is
    written in the same transaction and shares a timestamp, so sorting by time
    put the answer before the question.
    """
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.seq.desc())
        .limit(chat_service.HISTORY_LIMIT)
    )
    return list(reversed(list(result.scalars())))


async def _cards(
    session: SessionDep, user_id: uuid.UUID, rows: list[ChatMessage]
) -> dict[str, VacancyCard]:
    """Cards for every vacancy the rows offered to pick, in one query.

    Read from the database, and only this user's: an id the model made up or
    one of another account shows no card, rather than a card with words the
    model wrote. The score is the one judged against the CV in force now.
    """
    wanted: set[uuid.UUID] = set()
    for row in rows:
        question = _question(row)
        for raw in question["vacancy_ids"] if question else []:
            try:
                wanted.add(uuid.UUID(raw))
            except ValueError:
                continue
    if not wanted:
        return {}
    resume_id = (
        await session.execute(
            select(Resume.id).where(Resume.user_id == user_id, Resume.is_active.is_(True))
        )
    ).scalar_one_or_none()
    result = await session.execute(
        select(Vacancy)
        .where(Vacancy.id.in_(wanted), Vacancy.user_id == user_id)
        .options(selectinload(Vacancy.evaluations))
    )
    out: dict[str, VacancyCard] = {}
    for v in result.scalars():
        evaluation = next((e for e in v.evaluations if e.resume_id == resume_id), None)
        out[str(v.id)] = VacancyCard(
            id=str(v.id),
            title=v.title,
            company=v.company,
            salary_text=v.salary_text,
            location=v.location,
            remote=v.remote,
            score=evaluation.score if evaluation else None,
            saved=v.is_saved,
            url=v.url,
        )
    return out


async def _owned_thread(session: SessionDep, user: UserDep, thread_id: uuid.UUID) -> ChatThread:
    thread = await session.get(ChatThread, thread_id)
    if thread is None or thread.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Разговор не найден")
    return thread


async def _thread_vacancy(session: SessionDep, thread: ChatThread) -> VacancyRef | None:
    if thread.vacancy_id is None:
        return None
    vacancy = await session.get(Vacancy, thread.vacancy_id)
    if vacancy is None:
        return None
    return VacancyRef(id=str(vacancy.id), title=vacancy.title, company=vacancy.company)


async def _thread_response(session: SessionDep, thread: ChatThread) -> ThreadResponse:
    pending = await session.execute(
        select(func.count(Proposal.id))
        .join(ChatMessage, Proposal.message_id == ChatMessage.id)
        .where(ChatMessage.thread_id == thread.id, Proposal.status == chat_service.PENDING)
    )
    return ThreadResponse(
        id=thread.id,
        title=thread.title,
        vacancy=await _thread_vacancy(session, thread),
        last_message_at=thread.last_message_at,
        pending=int(pending.scalar_one()),
    )


@router.get("/threads", response_model=list[ThreadResponse])
async def threads(session: SessionDep, user: UserDep) -> list[ThreadResponse]:
    """The user's conversations, the one written in last first. Free."""
    result = await session.execute(
        select(ChatThread)
        .where(ChatThread.user_id == user.id)
        .order_by(ChatThread.last_message_at.desc(), ChatThread.created_at.desc())
    )
    rows = list(result.scalars())
    pending = await session.execute(
        select(ChatMessage.thread_id, func.count(Proposal.id))
        .join(ChatMessage, Proposal.message_id == ChatMessage.id)
        .where(Proposal.user_id == user.id, Proposal.status == chat_service.PENDING)
        .group_by(ChatMessage.thread_id)
    )
    counts = dict(pending.all())
    vacancy_ids = [t.vacancy_id for t in rows if t.vacancy_id is not None]
    refs: dict[uuid.UUID, VacancyRef] = {}
    if vacancy_ids:
        found = await session.execute(select(Vacancy).where(Vacancy.id.in_(vacancy_ids)))
        refs = {
            v.id: VacancyRef(id=str(v.id), title=v.title, company=v.company)
            for v in found.scalars()
        }
    return [
        ThreadResponse(
            id=t.id,
            title=t.title,
            vacancy=refs.get(t.vacancy_id) if t.vacancy_id else None,
            last_message_at=t.last_message_at,
            pending=int(counts.get(t.id, 0)),
        )
        for t in rows
    ]


@router.post("/threads", response_model=ThreadResponse)
async def new_thread(
    body: NewThreadRequest, session: SessionDep, user: UserDep
) -> ThreadResponse:
    """Start a conversation. Free — nothing is said to the model yet.

    With a vacancy, the thread of that vacancy is returned if it exists: one
    vacancy, one conversation, however many times "Обсудить" is pressed.
    """
    vacancy: Vacancy | None = None
    if body.vacancy_id is not None:
        vacancy = await session.get(Vacancy, body.vacancy_id)
        if vacancy is None or vacancy.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
            )
        existing = await session.execute(
            select(ChatThread).where(
                ChatThread.user_id == user.id, ChatThread.vacancy_id == vacancy.id
            )
        )
        thread = existing.scalar_one_or_none()
        if thread is not None:
            return await _thread_response(session, thread)

    thread = ChatThread(
        user_id=user.id,
        vacancy_id=vacancy.id if vacancy else None,
        title=chat_service.thread_title("", vacancy) if vacancy else "",
    )
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return await _thread_response(session, thread)


@router.get("/threads/{thread_id}", response_model=list[MessageResponse])
async def history(
    thread_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[MessageResponse]:
    """One conversation so far. Free."""
    thread = await _owned_thread(session, user, thread_id)
    stored = await _history(session, thread.id)
    return _as_conversation(stored, await _cards(session, user.id, stored))


@router.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def rename(
    thread_id: uuid.UUID, body: RenameRequest, session: SessionDep, user: UserDep
) -> ThreadResponse:
    thread = await _owned_thread(session, user, thread_id)
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустое название")
    thread.title = title
    await session.commit()
    await session.refresh(thread)
    return await _thread_response(session, thread)


@router.get("/proposals", response_model=list[ProposalResponse])
async def pending(session: SessionDep, user: UserDep) -> list[ProposalResponse]:
    """What is waiting for a decision, in every thread. Free."""
    result = await session.execute(
        select(Proposal, ChatMessage.thread_id)
        .outerjoin(ChatMessage, Proposal.message_id == ChatMessage.id)
        .where(Proposal.user_id == user.id, Proposal.status == chat_service.PENDING)
        .order_by(Proposal.created_at)
    )
    return [ProposalResponse.of(p, thread_id) for p, thread_id in result.all()]


@router.post("/threads/{thread_id}/messages", response_model=ChatResponse)
async def send(
    thread_id: uuid.UUID,
    body: MessageRequest,
    session: SessionDep,
    user: UserDep,
) -> ChatResponse:
    """Say something in a thread and get the answer.

    Costs money — a reply is a model call — so the budget is checked before it,
    never after.
    """
    thread = await _owned_thread(session, user, thread_id)
    ensure_can_spend(user)

    text = body.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Пустое сообщение"
        )

    stored = await _history(session, thread.id)
    history_payload = [row.payload for row in stored]

    payload: dict[str, Any] = {"role": chat_core.USER, "content": text}
    # In a vacancy's thread every message is about it unless another one is
    # attached: the first message may have scrolled out of the replayed tail,
    # and the model must not lose what the thread is about.
    vacancy_id = body.vacancy_id or thread.vacancy_id
    vacancy: Vacancy | None = None
    if vacancy_id is not None:
        vacancy = await session.get(Vacancy, vacancy_id)
        if vacancy is None or vacancy.user_id != user.id:
            if body.vacancy_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
                )
            vacancy = None
    if vacancy is not None:
        # Kept apart from the text: the bubble shows what the user typed, and
        # `fold_events` hands the model the reference in an envelope.
        payload[chat_core.VACANCY_KEY] = {
            "id": str(vacancy.id),
            "title": vacancy.title,
            "company": vacancy.company,
        }

    if not thread.title:
        thread.title = chat_service.thread_title(text)
    thread.last_message_at = datetime.now(UTC)

    seq = await chat_service.next_seq(session, user.id)
    question = ChatMessage(
        user_id=user.id,
        thread_id=thread.id,
        role=chat_core.USER,
        payload=payload,
        seq=seq,
    )
    session.add(question)
    await session.flush()

    resume = await session.execute(
        select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    )
    has_resume = resume.scalar_one_or_none() is not None

    runner = chat_service.ToolRunner(session, user, message_id=question.id)
    ledger = Ledger()
    llm = LLM(ledger=ledger)

    # The tool handler touches the database, so it cannot run in a thread the
    # way `evaluate` does: the session belongs to this event loop. The model
    # call inside the loop is what blocks, and it is what `run_in_threadpool`
    # would have been protecting — so the loop runs here and the handler is
    # awaited from it.
    reply, appended, _calls = await chat_core.converse_async(
        llm,
        chat_core.fold_events([*history_payload, question.payload]),
        runner,
        enabled=chat_service.enabled_tools(has_resume),
        threaded=run_in_threadpool,
    )

    rows: list[ChatMessage] = []
    for offset, payload in enumerate(appended, start=1):
        row = ChatMessage(
            user_id=user.id,
            thread_id=thread.id,
            role=payload.get("role", chat_core.ASSISTANT),
            payload=payload,
            seq=seq + offset,
        )
        session.add(row)
        rows.append(row)

    if rows:
        # The whole reply is billed to its last message, so a conversation's
        # cost adds up to the ledger rather than being spread by guesswork.
        rows[-1].cost_rub = round(ledger.rub, 4)

    await charge(session, user, ledger)
    await session.flush()

    # Proposals made during this turn belong to the assistant message that
    # asked for them, not to the user's question.
    assistant_row = next(
        (r for r in rows if r.role == chat_core.ASSISTANT), None
    )
    if assistant_row is not None:
        for proposal in runner.proposals:
            proposal.message_id = assistant_row.id

    await session.commit()
    await session.refresh(thread)

    return ChatResponse(
        thread=await _thread_response(session, thread),
        reply=reply,
        messages=_as_conversation(rows, await _cards(session, user.id, rows)),
        proposals=[ProposalResponse.of(p, thread.id) for p in runner.proposals],
    )


async def _owned_proposal(
    session: SessionDep, user: UserDep, proposal_id: uuid.UUID
) -> Proposal:
    result = await session.execute(
        select(Proposal).where(Proposal.id == proposal_id, Proposal.user_id == user.id)
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Предложение не найдено"
        )
    if proposal.status != chat_service.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Предложение уже {('принято' if proposal.status == chat_service.ACCEPTED else 'отклонено')}",
        )
    return proposal


@router.post("/proposals/{proposal_id}/accept", response_model=ProposalResponse)
async def accept(
    proposal_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
) -> ProposalResponse:
    """Apply what the chat proposed. Free — nothing here calls the model.

    The change is computed against the state as it is NOW, not as it was when
    the proposal was made: the user may have uploaded a new CV in between, and
    writing a stale profile back would undo that silently.
    """
    proposal = await _owned_proposal(session, user, proposal_id)

    if proposal.kind == "profile_patch":
        result = await session.execute(
            select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
        )
        resume = result.scalar_one_or_none()
        if resume is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Резюме не загружено"
            )
        # A new revision, not an edit in place — see app/services/revisions.py.
        revision = await revisions.new_revision(
            session,
            resume,
            profile=chat_service.apply_profile_patch(resume.profile, proposal.arguments),
        )
        proposal.proposed = {"profile": revision.profile}

    elif proposal.kind == "vacancy_status":
        vacancy = await _vacancy_of(session, user, proposal)
        await session.refresh(vacancy, ["application"])
        set_status(vacancy, proposal.arguments["status"])

    elif proposal.kind == "vacancy_saved":
        vacancy = await _vacancy_of(session, user, proposal)
        saved = bool(proposal.arguments.get("saved"))
        vacancy.is_saved = saved
        vacancy.saved_at = datetime.now(UTC) if saved else None

    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестный вид предложения: {proposal.kind}",
        )

    proposal.status = chat_service.ACCEPTED
    proposal.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(proposal)
    return ProposalResponse.of(proposal)


async def _vacancy_of(session: SessionDep, user: UserDep, proposal: Proposal) -> Vacancy:
    vacancy = await session.get(Vacancy, uuid.UUID(proposal.arguments["vacancy_id"]))
    if vacancy is None or vacancy.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
        )
    return vacancy


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalResponse)
async def reject(
    proposal_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
) -> ProposalResponse:
    """Decline. Nothing is written — which is also what happens on silence."""
    proposal = await _owned_proposal(session, user, proposal_id)
    proposal.status = chat_service.REJECTED
    proposal.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(proposal)
    return ProposalResponse.of(proposal)


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: uuid.UUID, session: SessionDep, user: UserDep
) -> dict[str, int]:
    """Forget one conversation. Its pending proposals go with it.

    Leaving them behind would mean a user who deleted the thread still has
    changes waiting from a conversation they can no longer read. Accepted and
    rejected ones stay: they are the record of what changed the CV.
    """
    thread = await _owned_thread(session, user, thread_id)
    proposals = await session.execute(
        select(Proposal)
        .join(ChatMessage, Proposal.message_id == ChatMessage.id)
        .where(ChatMessage.thread_id == thread.id, Proposal.status == chat_service.PENDING)
    )
    dropped = 0
    for proposal in proposals.scalars():
        await session.delete(proposal)
        dropped += 1

    removed = (
        await session.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread.id)
        )
    ).scalar_one()
    # Messages go by the foreign key's cascade.
    await session.delete(thread)
    await session.commit()
    return {"messages": int(removed), "proposals": dropped}
