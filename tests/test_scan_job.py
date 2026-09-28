"""The search button queues a job instead of holding the request open.

A scan of everything takes up to a minute. Run inside the request, it kept the
cabinet's page busy for all of it — navigation included — and the result was
lost the moment the user left the page. Now the cabinet queues a `scan` job,
the worker runs the same `scan.run_scan` the endpoint does, and the page polls
the job.

No network and no model: the collectors and the profile builder are stubbed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import worker
from app.models import Job, Resume
from app.services import scan
from tests.dbmark import requires_db

PROFILE = {"queries": ["Python разработчик"], "positive": ["Python"], "negative": []}


@pytest.fixture
async def resume(session, user) -> Resume:
    row = Resume(
        user_id=user.id,
        filename="cv.md",
        raw_text="Python developer",
        profile={"skills": ["Python"]},
        search_profile=PROFILE,
        is_active=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@pytest.fixture
def worker_db(engine, monkeypatch):
    """Point the worker's own sessions at the test database."""
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    return factory


FORM = {
    "source": "all",
    "salary_min": 250_000,
    "any_salary": True,
    "remote_only": True,
    "include_global": True,
}


@requires_db
class TestQueueingAScan:
    async def test_the_form_is_stored_with_the_job(self, client, auth_headers, resume):
        r = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["status"] == "queued"

        rows = await client.get("/api/jobs?kind=scan&limit=1", headers=auth_headers)
        assert [j["id"] for j in rows.json()] == [job["id"]]

    async def test_a_bad_form_is_refused_now_not_by_the_worker(
        self, client, auth_headers, resume
    ):
        r = await client.post(
            "/api/jobs",
            headers=auth_headers,
            json={"kind": "scan", "payload": {"source": "hh"}},
        )
        assert r.status_code == 422

    async def test_a_second_press_gets_the_scan_already_running(
        self, client, auth_headers, resume
    ):
        """Two scans at once walk the same boards twice for nothing."""
        first = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        second = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        assert second.json()["id"] == first.json()["id"]

    async def test_a_finished_scan_does_not_block_the_next(
        self, client, auth_headers, resume, session
    ):
        first = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        job = await session.get(Job, first.json()["id"])
        job.status = "done"
        await session.commit()

        second = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        assert second.json()["id"] != first.json()["id"]


@requires_db
class TestTheWorkerRunsTheForm:
    async def test_form_settings_reach_the_scan(
        self, client, auth_headers, resume, worker_db, monkeypatch
    ):
        seen = {}

        async def fake_all(session_, user_id, search_profile, **kw):
            seen["profile"] = search_profile
            seen.update(kw)
            return {"sources": {}, "collected": 3, "kept": 1, "dropped": 2,
                    "duplicates": 0, "new": 1}

        monkeypatch.setattr(scan, "collect_all", fake_all)

        await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        assert await worker.tick() is True

        assert seen["profile"]["salary_min"] == 250_000
        assert seen["profile"]["remote_only"] is True
        assert seen["company_groups"] == ("ru", "global")
        # The stored profile is the user's; a form setting must not rewrite it.
        async with worker_db() as s:
            stored = await s.get(Resume, resume.id)
            assert stored.search_profile == PROFILE

        rows = await client.get("/api/jobs?kind=scan&limit=1", headers=auth_headers)
        job = rows.json()[0]
        assert job["status"] == "done"
        assert job["result"]["new"] == 1

    async def test_a_failed_scan_fails_once_with_its_reason(
        self, client, auth_headers, resume, worker_db, monkeypatch
    ):
        """Retrying a minute-long scan only delays the error the user waits for."""

        async def broken(*a, **kw):
            raise ValueError("профиль поиска пуст")

        monkeypatch.setattr(scan, "collect_all", broken)

        await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": FORM}
        )
        await worker.tick()

        job = (await client.get("/api/jobs?kind=scan&limit=1", headers=auth_headers)).json()[0]
        assert job["status"] == "failed"
        assert job["attempts"] == 1
        assert "профиль поиска пуст" in job["error"]


@requires_db
class TestOrphans:
    async def test_a_job_left_running_is_failed_at_startup(
        self, session, user, resume, worker_db
    ):
        """Otherwise the cabinet shows a scan in progress forever and will not
        queue another."""
        session.add(Job(user_id=user.id, kind="scan", payload={}, status="running"))
        session.add(Job(user_id=user.id, kind="scan", payload={}, status="done"))
        await session.commit()

        assert await worker.reap_orphans() == 1

        async with worker_db() as s:
            statuses = sorted((await s.execute(select(Job.status))).scalars())
        assert statuses == ["done", "failed"]
