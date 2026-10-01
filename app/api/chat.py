"""The chat: send a message, read the history, decide on a proposal.

Synchronous on purpose. A reply takes seconds, not the tens of seconds an
evaluation takes, so it does not need the `jobs` queue — and a chat that
answers through a polling loop stops feeling like a chat. The long steps stay
where they are.

Accepting a proposal is a separate request from making one. That split is the
whole confirmation layer: `core/proposals.py` returns the new value and the
caller writes it, and here the caller is an explicit POST the user makes.
Silence is refusal — a pending proposal nobody touches changes nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from core import chat as chat_core
from core import tools
from core.llm import LLM, Ledger

from ..core.deps import SessionDep, UserDep
from ..models import ChatMessage, Proposal, Resume, Vacancy
from ..services import chat as chat_service
from ..services import revisions
from ..services.spend import charge, ensure_can_spend
from ..services.tracker import set_status

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_MESSAGE_CHARS = 4000


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ProposalResponse(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    arguments: dict[str, Any]
    current: dict[str, Any] | None
    proposed: dict[str, Any] | None
    rationale: str | None
    created_at: datetime

    @classmethod
    def of(cls, p: Proposal) -> ProposalResponse:
        return cls(
            id=p.id,
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


class ChatResponse(BaseModel):
    reply: str
    messages: list[MessageResponse]
    proposals: list[ProposalResponse]


def _visible(message: ChatMessage) -> MessageResponse | None:
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
    return [n for n in names if n and n != tools.ASK_CHOICE.name]


def _as_conversation(stored: list[ChatMessage]) -> list[MessageResponse]:
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
        visible = _visible(row)
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


async def _history(session: SessionDep, user_id: uuid.UUID) -> list[ChatMessage]:
    """The tail of the conversation, in order.

    Ordered by `seq`, not by `created_at`: every message of one exchange is
    written in the same transaction and shares a timestamp, so sorting by time
    put the answer before the question.
    """
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.seq.desc())
        .limit(chat_service.HISTORY_LIMIT)
    )
    return list(reversed(list(result.scalars())))


@router.get("", response_model=list[MessageResponse])
async def history(session: SessionDep, user: UserDep) -> list[MessageResponse]:
    """The conversation so far. Free."""
    return _as_conversation(await _history(session, user.id))


@router.get("/proposals", response_model=list[ProposalResponse])
async def pending(session: SessionDep, user: UserDep) -> list[ProposalResponse]:
    """What is waiting for a decision. Free."""
    result = await session.execute(
        select(Proposal)
        .where(Proposal.user_id == user.id, Proposal.status == chat_service.PENDING)
        .order_by(Proposal.created_at)
    )
    return [ProposalResponse.of(p) for p in result.scalars()]


@router.post("", response_model=ChatResponse)
async def send(
    body: MessageRequest,
    session: SessionDep,
    user: UserDep,
) -> ChatResponse:
    """Say something to the chat and get the answer.

    Costs money — a reply is a model call — so the budget is checked before it,
    never after.
    """
    ensure_can_spend(user)

    text = body.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Пустое сообщение"
        )

    stored = await _history(session, user.id)
    history_payload = [row.payload for row in stored]

    seq = await chat_service.next_seq(session, user.id)
    question = ChatMessage(
        user_id=user.id,
        role=chat_core.USER,
        payload={"role": chat_core.USER, "content": text},
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

    return ChatResponse(
        reply=reply,
        messages=_as_conversation(rows),
        proposals=[ProposalResponse.of(p) for p in runner.proposals],
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


@router.post("/clear")
async def clear(session: SessionDep, user: UserDep) -> dict[str, int]:
    """Forget the conversation. Pending proposals go with it.

    Leaving them behind would mean a user who cleared the chat still has
    changes waiting from a conversation they can no longer read.
    """
    proposals = await session.execute(
        select(Proposal).where(
            Proposal.user_id == user.id, Proposal.status == chat_service.PENDING
        )
    )
    dropped = 0
    for proposal in proposals.scalars():
        await session.delete(proposal)
        dropped += 1

    messages = await session.execute(
        select(ChatMessage).where(ChatMessage.user_id == user.id)
    )
    removed = 0
    for message in messages.scalars():
        await session.delete(message)
        removed += 1

    await session.commit()
    return {"messages": removed, "proposals": dropped}
