"""A scan derives its search profile when the CV was uploaded without one.

Uploading a CV now builds the search profile in the same request. CVs stored
before that still have none, and a scan on an EMPTY profile is what produced
125 unscreened rows on 2026-09-19 — SMM, legal, product management, none
carrying a rejection reason. So the worker derives one first and stores it,
rather than refusing and leaving the user stuck.

No LLM is called here: `build_search_profile` is stubbed.
"""

from __future__ import annotations

import uuid

import pytest

from app import worker
from app.models import Resume
from app.services import scan
from tests.dbmark import requires_db

DERIVED = {
    "queries": ["Python разработчик"],
    "positive": ["Python"],
    "negative": ["Менеджер"],
}


@pytest.fixture
async def resume_without_search_profile(session, user) -> Resume:
    """A CV parsed before the profile was derived on upload."""
    resume = Resume(
        user_id=user.id,
        filename="cv.md",
        raw_text="Python developer, 6 years",
        profile={"skills": ["Python"]},
        search_profile=None,
        is_active=True,
    )
    session.add(resume)
    await session.commit()
    await session.refresh(resume)
    return resume


@requires_db
class TestScanDerivesAMissingProfile:
    async def test_queueing_a_scan_is_allowed(
        self, client, auth_headers, resume_without_search_profile
    ):
        """No refusal: the worker fills the gap instead of blocking the user."""
        r = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": {}}
        )
        assert r.status_code == 201

    async def test_the_worker_stores_the_derived_profile(
        self, session, user, resume_without_search_profile, monkeypatch
    ):
        calls = []

        def fake_build(llm, profile):
            calls.append(profile)
            return DERIVED

        async def fake_collect(session_, user_id, search_profile, pages=1):
            return {"collected": 0, "new": 0, "kept": 0, "dropped": 0}

        monkeypatch.setattr(scan, "build_search_profile", fake_build)
        monkeypatch.setattr(worker.scan, "collect_habr", fake_collect)

        from app.models import Job

        job = Job(user_id=user.id, kind="scan", payload={})
        session.add(job)
        await session.flush()

        await worker._run_scan(session, job, user, resume_without_search_profile)

        assert calls == [{"skills": ["Python"]}]
        await session.refresh(resume_without_search_profile)
        assert resume_without_search_profile.search_profile == DERIVED

    async def test_an_existing_profile_is_not_rebuilt(
        self, session, user, resume_without_search_profile, monkeypatch
    ):
        """Deriving costs money; a stored profile must be reused, not re-bought."""
        resume_without_search_profile.search_profile = DERIVED
        await session.commit()

        def fail_build(llm, profile):  # pragma: no cover - must not run
            raise AssertionError("the stored profile was rebuilt")

        async def fake_collect(session_, user_id, search_profile, pages=1):
            assert search_profile == DERIVED
            return {"collected": 0, "new": 0, "kept": 0, "dropped": 0}

        monkeypatch.setattr(scan, "build_search_profile", fail_build)
        monkeypatch.setattr(worker.scan, "collect_habr", fake_collect)

        from app.models import Job

        job = Job(user_id=user.id, kind="scan", payload={})
        session.add(job)
        await session.flush()

        await worker._run_scan(session, job, user, resume_without_search_profile)

    async def test_other_job_kinds_still_queue(
        self, client, auth_headers, resume_without_search_profile
    ):
        r = await client.post(
            "/api/jobs",
            headers=auth_headers,
            json={"kind": "evaluate", "payload": {"vacancy_id": str(uuid.uuid4())}},
        )
        assert r.status_code == 201


@requires_db
class TestTheScanEndpointDerivesItToo:
    """POST /api/vacancies/scan is what the "Подобрать" button calls.

    It reaches collect_habr without going through the worker, so the fallback
    added to the worker alone left this path raising ValueError -> 500.
    """

    async def test_scanning_without_a_profile_does_not_fail(
        self, client, session, auth_headers, resume_without_search_profile, monkeypatch
    ):
        def fake_build(llm, profile):
            return DERIVED

        async def fake_collect(session_, user_id, search_profile, pages=1):
            assert search_profile == DERIVED
            return {"collected": 0, "new": 0, "kept": 0, "dropped": 0}

        monkeypatch.setattr(scan, "build_search_profile", fake_build)
        monkeypatch.setattr(scan, "collect_habr", fake_collect)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "habr", "pages": 1}
        )
        assert r.status_code == 200, r.text

        await session.refresh(resume_without_search_profile)
        assert resume_without_search_profile.search_profile == DERIVED
