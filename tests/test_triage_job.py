"""Triage is a queued job, and the queue serves scans in a lane of their own.

Held open inside a request, the triage call kept the cabinet's navigation
waiting for the model. In a single queue lane, one user's minute-long scan
held another user's paid evaluation. No network and no model here: the
triage step is stubbed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import worker
from app.models import Job, Resume, Vacancy
from app.services import scan
from app.services import triage as triage_service
from core.sources.habr_api import Posting
from tests.dbmark import requires_db


@pytest.fixture
async def resume(session, user) -> Resume:
    row = Resume(
        user_id=user.id, filename="cv.md", raw_text="x",
        profile={"skills": ["Python"]}, archetypes={"roles": []},
        search_profile={"queries": ["python"], "positive": ["python"]}, is_active=True,
    )
    session.add(row)
    await session.commit()
    return row


@pytest.fixture
def worker_db(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    return factory


@pytest.fixture
def stub_model(monkeypatch):
    """A model that calls every row it sees "worth"; records what it saw."""
    seen: list[str] = []

    def fake_triage(llm, archetypes, digests):
        seen.extend(d["id"] for d in digests)

        class Outcome:
            verdicts = {d["id"]: {"bucket": "worth", "reason": "подходит"} for d in digests}
            missing: list = []
            unknown: list = []

            def counts(self):
                return {"worth": len(digests)}

        return Outcome()

    class StubLLM:
        def __init__(self, *a, **kw) -> None:
            self.ledger = kw.get("ledger")

    monkeypatch.setattr(triage_service, "LLM", StubLLM)
    monkeypatch.setattr(triage_service, "run_triage", fake_triage)
    return seen


async def _rows(session, user, *ids: str) -> list[Vacancy]:
    postings = [(Posting(id=i, url=f"u{i}", title=f"Python {i}", company=f"C{i}"), "")
                for i in ids]
    await scan.persist(session, user.id, "habr", kept=postings, dropped=[])
    await session.commit()
    from sqlalchemy import select

    return list((await session.execute(select(Vacancy))).scalars())


@requires_db
class TestTriageIsAJob:
    async def test_the_worker_writes_the_verdicts(
        self, client, auth_headers, session, user, resume, worker_db, stub_model
    ):
        rows = await _rows(session, user, "1", "2")
        r = await client.post(
            "/api/jobs", headers=auth_headers,
            json={"kind": "triage", "payload": {"ids": [str(rows[0].id)]}},
        )
        assert r.status_code == 201, r.text

        assert await worker.tick() is True

        job = (await client.get("/api/jobs?kind=triage&limit=1", headers=auth_headers)).json()[0]
        assert job["status"] == "done", job["error"]
        assert job["result"]["judged"] == 1
        # Only the row the conditions kept went to the model.
        assert stub_model == [str(rows[0].id)]

        found = (await client.get("/api/state", headers=auth_headers)).json()["found"]
        verdicts = {f["id"]: f["triage"] for f in found}
        assert verdicts[str(rows[0].id)]["bucket"] == "worth"
        assert verdicts[str(rows[1].id)] is None

    async def test_a_second_press_gets_the_running_job(
        self, client, auth_headers, session, user, resume
    ):
        first = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "triage", "payload": {}}
        )
        second = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "triage", "payload": {}}
        )
        assert first.json()["id"] == second.json()["id"]

    async def test_a_malformed_id_is_refused_at_once(self, client, auth_headers, resume):
        r = await client.post(
            "/api/jobs", headers=auth_headers,
            json={"kind": "triage", "payload": {"ids": ["не-uuid"]}},
        )
        assert r.status_code == 422

    async def test_nothing_to_judge_is_a_note_not_a_failure(
        self, client, auth_headers, resume, worker_db, stub_model
    ):
        await client.post("/api/jobs", headers=auth_headers, json={"kind": "triage"})
        await worker.tick()
        job = (await client.get("/api/jobs?kind=triage&limit=1", headers=auth_headers)).json()[0]
        assert job["status"] == "done"
        assert job["result"]["note"] == triage_service.NOTHING_TO_DO
        assert stub_model == []


@requires_db
class TestLanes:
    async def test_a_queued_scan_does_not_hold_a_paid_job(self, session, user, worker_db):
        session.add(Job(user_id=user.id, kind="scan", payload={}))
        await session.commit()
        session.add(Job(user_id=user.id, kind="evaluate", payload={}))
        await session.commit()

        async with worker_db() as s:
            paid = await worker.claim(s, scans=False)
            assert paid is not None and paid.kind == "evaluate"
        async with worker_db() as s:
            scan_job = await worker.claim(s, scans=True)
            assert scan_job is not None and scan_job.kind == "scan"
        async with worker_db() as s:
            assert await worker.claim(s, scans=True) is None
            assert await worker.claim(s, scans=False) is None

    async def test_triage_runs_in_the_paid_lane(self, session, user, worker_db):
        session.add(Job(user_id=user.id, kind="triage", payload={}))
        await session.commit()
        async with worker_db() as s:
            assert await worker.claim(s, scans=True) is None
            job = await worker.claim(s, scans=False)
            assert job is not None and job.kind == "triage"
