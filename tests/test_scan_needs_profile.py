"""A scan is unavailable until the user accepts a search profile.

Uploading a CV fills `Resume.profile`; the search profile is a separate
confirmation and stays NULL until the user accepts a proposal. On 2026-09-19 a
scan ran in that state and stored 125 unscreened rows — SMM, legal, product
management — none carrying a rejection reason, because an empty SearchProfile
names no stop-word and no keyword.

No LLM is called here: the refusal happens before any model or network call.
"""

from __future__ import annotations

import uuid

import pytest

from app.models import Resume
from tests.dbmark import requires_db


@pytest.fixture
async def resume_without_search_profile(session, user) -> Resume:
    """A parsed CV whose search profile was never accepted — the reported state."""
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
class TestScanIsUnavailableWithoutAProfile:
    async def test_queueing_a_scan_is_refused(
        self, client, auth_headers, resume_without_search_profile
    ):
        r = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": {}}
        )
        assert r.status_code == 409
        assert "профиль поиска" in r.json()["detail"].lower()

    async def test_the_refusal_names_what_to_do(
        self, client, auth_headers, resume_without_search_profile
    ):
        """A refusal the user cannot act on is a dead end, not a guard."""
        r = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": {}}
        )
        assert "подтвердите" in r.json()["detail"].lower()

    async def test_other_job_kinds_still_queue(
        self, client, auth_headers, resume_without_search_profile
    ):
        """Only the scan depends on the profile. Evaluating a vacancy does not."""
        r = await client.post(
            "/api/jobs",
            headers=auth_headers,
            json={"kind": "evaluate", "payload": {"vacancy_id": str(uuid.uuid4())}},
        )
        assert r.status_code == 201

    async def test_an_accepted_profile_lets_the_scan_through(
        self, client, session, auth_headers, resume_without_search_profile
    ):
        resume_without_search_profile.search_profile = {
            "queries": ["Python разработчик"],
            "positive": ["Python"],
            "negative": ["Менеджер"],
        }
        await session.commit()

        r = await client.post(
            "/api/jobs", headers=auth_headers, json={"kind": "scan", "payload": {}}
        )
        assert r.status_code == 201
