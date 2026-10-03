"""An employer's feed, read whole, closes the postings it no longer lists.

The employer sources have no page to ask whether a posting is open, so the
list used to fill with "older than 30 days" rows that were mostly still
listed. A feed (Greenhouse, Lever, Ashby, Huntflow) returns the company's
whole list, so a posting missing from it is a posting taken down — positive
evidence, counted over several reads so one glitch closes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Posting as PooledPosting
from app.models import Resume, Vacancy
from app.services import liveness, pool, scan
from core.sources import employers
from core.sources.habr_api import Posting
from tests.dbmark import requires_db

BOARD = "greenhouse/acme"
PROFILE = {"queries": ["python"], "positive": ["python"]}


def card(native: str) -> Posting:
    return Posting(
        id=f"{BOARD}/{native}", url=f"https://boards.greenhouse.io/acme/jobs/{native}",
        title=f"Python {native}", company="Acme",
    )


async def _pool(session, *natives: str) -> None:
    await pool.upsert(session, "company", [pool.Sighting(card(n)) for n in natives])


async def _row(session, native: str) -> PooledPosting:
    return (await session.execute(
        select(PooledPosting).where(PooledPosting.external_id == f"{BOARD}/{native}")
    )).scalar_one()


async def _read(session, seen: set[str], when: datetime, boards=(BOARD,)) -> int:
    return await pool.mark_absent(
        session, "company", list(boards), {f"{BOARD}/{n}" for n in seen}, when
    )


@requires_db
class TestMarkAbsent:
    async def test_three_reads_over_a_day_close_it(self, session):
        await _pool(session, "1", "2")
        start = datetime.now(UTC)
        assert await _read(session, {"1"}, start) == 0
        assert await _read(session, {"1"}, start + timedelta(hours=12)) == 0
        assert await _read(session, {"1"}, start + timedelta(hours=25)) == 1

        gone, kept = await _row(session, "2"), await _row(session, "1")
        assert gone.closed_at is not None
        assert gone.closed_reason.startswith(pool.ABSENT_REASON)
        assert kept.closed_at is None and kept.missed_reads == 0

    async def test_three_quick_reads_are_one_glitch_not_a_closing(self, session):
        await _pool(session, "1", "2")
        start = datetime.now(UTC)
        for minutes in (0, 1, 2, 3):
            await _read(session, {"1"}, start + timedelta(minutes=minutes))
        row = await _row(session, "2")
        assert row.closed_at is None and row.missed_reads == 4

    async def test_a_sighting_resets_the_count(self, session):
        await _pool(session, "1", "2")
        start = datetime.now(UTC)
        await _read(session, {"1"}, start)
        await _read(session, {"1"}, start + timedelta(hours=12))
        await _pool(session, "1", "2")
        await _read(session, {"1", "2"}, start + timedelta(hours=25))
        row = await _row(session, "2")
        assert row.missed_reads == 0 and row.missing_since is None

    async def test_a_posting_back_in_the_feed_is_reopened(self, session):
        await _pool(session, "1", "2")
        start = datetime.now(UTC)
        for hours in (0, 12, 25):
            await _read(session, {"1"}, start + timedelta(hours=hours))
        assert (await _row(session, "2")).closed_at is not None

        await _pool(session, "2")
        row = await _row(session, "2")
        assert row.closed_at is None and row.closed_reason is None

    async def test_other_evidence_is_not_overruled_by_a_listing(self, session):
        await _pool(session, "1")
        row = await _row(session, "1")
        row.closed_at = datetime.now(UTC)
        row.closed_reason = "410"
        await _pool(session, "1")
        assert (await _row(session, "1")).closed_reason == "410"

    async def test_only_the_boards_read_whole_count(self, session):
        """A board missing from `complete` — failed, searchable — proves nothing."""
        await _pool(session, "1", "2")
        start = datetime.now(UTC)
        for hours in (0, 12, 25):
            await _read(session, set(), start + timedelta(hours=hours), boards=())
        assert (await _row(session, "2")).missed_reads == 0


@requires_db
class TestThroughTheScan:
    async def test_a_scan_closes_what_the_feed_dropped_and_says_so(
        self, session, user, monkeypatch
    ):
        session.add(Resume(user_id=user.id, filename="cv.md", raw_text="т", profile={},
                           search_profile=PROFILE, is_active=True))
        await session.commit()
        listed = [card("1"), card("2")]

        def fake_collect(queries, groups):
            return employers.Collected(postings=list(listed), complete=[BOARD])

        monkeypatch.setattr(employers, "collect", fake_collect)
        monkeypatch.setattr(pool, "ABSENT_READS", 1)
        monkeypatch.setattr(pool, "ABSENT_FOR", timedelta(0))

        await scan.collect_employers(session, user.id, PROFILE)
        await session.commit()
        listed.pop()
        # The listing cache would answer the second scan; age it out.
        from app.models import SourceFetch

        for fetch in (await session.execute(select(SourceFetch))).scalars():
            fetch.fetched_at -= timedelta(hours=3)
        result = await scan.collect_employers(session, user.id, PROFILE)
        await session.commit()

        assert result["closed"] == 1
        rows = {v.external_id: v for v in (await session.execute(select(Vacancy))).scalars()}
        # Closed, not deleted: the user's row stays, marked.
        assert rows[f"{BOARD}/2"].closed_at is not None
        assert rows[f"{BOARD}/1"].closed_at is None


class TestStaleForFeeds:
    def _posting(self, external_id: str, published_days: int, seen_days: int):
        now = datetime.now(UTC)
        return PooledPosting(
            source="company", external_id=external_id, url="https://example.com/x",
            published=now - timedelta(days=published_days),
            last_seen_at=now - timedelta(days=seen_days),
        )

    def test_an_old_posting_still_in_its_feed_is_not_stale(self):
        assert liveness.stale_reason(self._posting(f"{BOARD}/1", 90, 0)) is None

    def test_a_feed_posting_nobody_read_for_a_week_is(self):
        reason = liveness.stale_reason(self._posting(f"{BOARD}/1", 2, 10))
        assert reason and "ленте компании" in reason

    @pytest.mark.parametrize("ext", ["sber/sber/1", "yandex/yandex/1"])
    def test_a_searchable_board_is_still_judged_by_age(self, ext):
        assert "дн. назад" in liveness.stale_reason(self._posting(ext, 45, 0))
