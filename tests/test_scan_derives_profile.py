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


class _StubLLM:
    """Stands in for the client, which is built before the stubbed step runs.

    `ensure_search_profile` constructs LLM(ledger=...) and only then calls
    `build_search_profile`, so stubbing the step alone still reached the real
    constructor — and that constructor demands DEEPSEEK_API_KEY. These tests
    therefore passed only where a .env happened to sit, and errored in CI,
    which has no key on purpose. Nothing here can reach the network: the stub
    has no client at all.
    """

    def __init__(self, *a, **kw) -> None:
        self.ledger = kw.get("ledger")


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(scan, "LLM", _StubLLM)


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

        async def fake_collect(session_, user_id, search_profile, pages=1, **kw):
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

        async def fake_collect(session_, user_id, search_profile, pages=1, **kw):
            # The form's threshold is laid over the stored profile (an empty
            # form clears it); everything else is the stored profile as is.
            assert {k: v for k, v in search_profile.items() if k != "salary_min"} == DERIVED
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

        async def fake_collect(session_, user_id, search_profile, pages=1, **kw):
            # `any_salary` defaults to true, which clears the threshold for
            # this run; everything else is the derived profile untouched.
            assert search_profile == {**DERIVED, "salary_min": None}
            return {"collected": 0, "new": 0, "kept": 0, "dropped": 0}

        monkeypatch.setattr(scan, "build_search_profile", fake_build)
        monkeypatch.setattr(scan, "collect_habr", fake_collect)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "habr", "pages": 1}
        )
        assert r.status_code == 200, r.text

        await session.refresh(resume_without_search_profile)
        assert resume_without_search_profile.search_profile == DERIVED


@requires_db
class TestScanFormOverrides:
    """The scan form's salary settings reach the collector.

    They used to reach nothing: the page read `a-sal` and `a-nosal` and sent
    neither, so the threshold the user typed did nothing at all.
    """

    @staticmethod
    def _capture(monkeypatch, seen):
        async def fake_collect(session_, user_id, search_profile, pages=1, **kw):
            seen.update(search_profile)
            return {"collected": 0, "new": 0, "kept": 0, "dropped": 0}

        monkeypatch.setattr(scan, "build_search_profile", lambda llm, profile: DERIVED)
        monkeypatch.setattr(scan, "collect_habr", fake_collect)

    async def test_any_salary_keeps_the_threshold_for_the_free_filter(
        self, client, auth_headers, resume_without_search_profile, monkeypatch
    ):
        """The tick controls what Habr is asked, not what PreScreen judges.

        Clearing the threshold here made a typed number filter nothing at all,
        while the hint under the field promised it would.
        """
        seen: dict = {}
        self._capture(monkeypatch, seen)

        r = await client.post(
            "/api/vacancies/scan",
            headers=auth_headers,
            json={"source": "habr", "salary_min": 250000, "any_salary": True},
        )
        assert r.status_code == 200, r.text
        assert seen["salary_min"] == 250000

    async def test_a_threshold_is_passed_when_any_salary_is_off(
        self, client, auth_headers, resume_without_search_profile, monkeypatch
    ):
        seen: dict = {}
        self._capture(monkeypatch, seen)

        r = await client.post(
            "/api/vacancies/scan",
            headers=auth_headers,
            json={"source": "habr", "salary_min": 250000, "any_salary": False},
        )
        assert r.status_code == 200, r.text
        assert seen["salary_min"] == 250000

    async def test_an_empty_field_clears_a_stored_threshold(
        self, client, session, auth_headers, resume_without_search_profile, monkeypatch
    ):
        """Wiping the box must mean no threshold, not "keep the old one"."""
        resume_without_search_profile.search_profile = {**DERIVED, "salary_min": 300000}
        await session.commit()

        seen: dict = {}
        self._capture(monkeypatch, seen)

        r = await client.post(
            "/api/vacancies/scan",
            headers=auth_headers,
            json={"source": "habr", "any_salary": False},
        )
        assert r.status_code == 200, r.text
        assert seen["salary_min"] is None

    async def test_the_stored_profile_is_not_rewritten_by_the_form(
        self, client, session, auth_headers, resume_without_search_profile, monkeypatch
    ):
        """A per-scan choice must not redefine the profile the user keeps."""
        resume_without_search_profile.search_profile = {**DERIVED, "salary_min": 300000}
        await session.commit()

        seen: dict = {}
        self._capture(monkeypatch, seen)

        r = await client.post(
            "/api/vacancies/scan",
            headers=auth_headers,
            json={"source": "habr", "salary_min": 100000, "any_salary": False},
        )
        assert r.status_code == 200, r.text
        assert seen["salary_min"] == 100000

        await session.refresh(resume_without_search_profile)
        assert resume_without_search_profile.search_profile["salary_min"] == 300000
