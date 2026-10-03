"""The second screen: matching a batch answer back onto the postings.

No network — the model's reply is handed in as a dict. What is pinned here is
what happens when that reply is imperfect, because a batch call routinely comes
back short and the failure must never be silent.
"""

import json

import pytest

from core.triage import (
    BATCH_SIZE,
    MAYBE,
    SKIP,
    WORTH,
    apply_verdicts,
    parse_verdicts,
    posting_digest,
    run_triage,
)


def row(vid: str, title: str = "Python разработчик", **kw) -> dict:
    return {"id": vid, "title": title, "company": "Компания", **kw}


def verdict(vid: str, bucket: str = WORTH, reason: str = "причина") -> dict:
    return {"id": vid, "bucket": bucket, "archetype": "Python Backend", "reason": reason}


class TestPostingDigest:
    def test_only_listing_fields_reach_the_prompt(self):
        """Anything beyond the listing would cost a page fetch — the whole
        point of this step is deciding what NOT to read."""
        d = posting_digest(row("1", url="https://x", report={"score": 4}, published="2026-09-01"))
        assert set(d) <= {"id", "title", "company", "qualification",
                          "salary_text", "divisions", "skills"}
        assert "url" not in d and "report" not in d

    def test_empty_values_are_dropped(self):
        d = posting_digest({"id": "1", "title": "Python", "salary_text": "", "skills": []})
        assert d == {"id": "1", "title": "Python"}

    def test_long_skill_list_is_trimmed(self):
        d = posting_digest(row("1", skills=[f"skill{i}" for i in range(30)]))
        assert len(d["skills"]) == 12


class TestParseVerdicts:
    def test_verdicts_are_keyed_by_id(self):
        rows = [row("1"), row("2")]
        raw = {"verdicts": [verdict("1", WORTH), verdict("2", SKIP)]}
        res = parse_verdicts(raw, rows)
        assert res.verdicts["1"]["bucket"] == WORTH
        assert res.verdicts["2"]["bucket"] == SKIP
        assert not res.missing

    def test_missing_verdict_is_reported_not_invented(self):
        """A batch reply can come back short. Defaulting the gap to "skip"
        would hide a posting nobody actually judged."""
        rows = [row("1"), row("2"), row("3")]
        res = parse_verdicts({"verdicts": [verdict("1")]}, rows)
        assert res.missing == ["2", "3"]
        assert "2" not in res.verdicts

    def test_unknown_id_is_reported(self):
        rows = [row("1")]
        res = parse_verdicts({"verdicts": [verdict("1"), verdict("999")]}, rows)
        assert res.unknown == ["999"]
        assert set(res.verdicts) == {"1"}

    def test_unrecognised_bucket_falls_back_to_maybe(self):
        """The cautious side: a typo must not drop the posting from sight."""
        rows = [row("1")]
        res = parse_verdicts({"verdicts": [verdict("1", "worth_a_look")]}, rows)
        assert res.verdicts["1"]["bucket"] == MAYBE

    def test_empty_reply_leaves_everything_unjudged(self):
        rows = [row("1"), row("2")]
        res = parse_verdicts({}, rows)
        assert res.missing == ["1", "2"]
        assert res.counts() == {WORTH: 0, MAYBE: 0, SKIP: 0}

    def test_malformed_entries_are_skipped(self):
        rows = [row("1")]
        res = parse_verdicts({"verdicts": ["мусор", None, verdict("1")]}, rows)
        assert set(res.verdicts) == {"1"}

    def test_counts_by_bucket(self):
        rows = [row("1"), row("2"), row("3")]
        raw = {"verdicts": [verdict("1", WORTH), verdict("2", SKIP), verdict("3", SKIP)]}
        assert parse_verdicts(raw, rows).counts() == {WORTH: 1, MAYBE: 0, SKIP: 2}


class TestApplyVerdicts:
    def test_skip_is_marked_never_removed(self):
        """Marking, not deleting: a wrong verdict has to stay visible."""
        rows = [row("1"), row("2")]
        res = parse_verdicts({"verdicts": [verdict("1", SKIP, "чужой стек")]}, rows)
        out = apply_verdicts(rows, res)
        assert len(out) == 2
        assert out[0]["triage"] == {"bucket": SKIP, "archetype": "Python Backend",
                                    "reason": "чужой стек"}

    def test_unjudged_row_keeps_no_triage_key(self):
        rows = [row("1"), row("2")]
        res = parse_verdicts({"verdicts": [verdict("1")]}, rows)
        apply_verdicts(rows, res)
        assert "triage" not in rows[1]

    def test_paid_report_is_untouched(self):
        rows = [row("1", report={"score": 4.5})]
        res = parse_verdicts({"verdicts": [verdict("1", SKIP)]}, rows)
        apply_verdicts(rows, res)
        assert rows[0]["report"] == {"score": 4.5}


class FakeLLM:
    """Answers each batch from its digests; a batch holding a `broken` title fails."""

    def __init__(self, broken: frozenset[str] = frozenset()) -> None:
        self.broken = broken
        self.calls: list[list[dict]] = []

    def complete_json(self, step, system, user, temperature):
        digests = json.loads(user.split("):\n", 1)[1])
        self.calls.append(digests)
        if any(d["title"] in self.broken for d in digests):
            raise RuntimeError("модель не вернула валидный JSON на шаге triage")
        return {"verdicts": [verdict(d["id"]) for d in digests]}


class TestRunTriage:
    def test_a_long_shortlist_is_split_into_batches(self):
        rows = [row(f"uuid-{i}", title=f"t{i}") for i in range(BATCH_SIZE * 2 + 5)]
        llm = FakeLLM()
        result = run_triage(llm, {}, rows)
        assert sorted(len(c) for c in llm.calls) == [5, BATCH_SIZE, BATCH_SIZE]
        assert set(result.verdicts) == {r["id"] for r in rows}
        assert result.missing == []

    def test_the_model_sees_short_numbers_not_uuids(self):
        llm = FakeLLM()
        run_triage(llm, {}, [row("d5f266b6-bc3a"), row("23d19438-dddc")])
        assert [d["id"] for d in llm.calls[0]] == ["1", "2"]

    def test_a_broken_batch_goes_to_missing_not_to_skip(self):
        rows = [row(f"uuid-{i}", title=f"t{i}") for i in range(BATCH_SIZE + 3)]
        result = run_triage(FakeLLM(broken=frozenset({"t0"})), {}, rows)
        assert set(result.missing) == {r["id"] for r in rows[:BATCH_SIZE]}
        assert set(result.verdicts) == {r["id"] for r in rows[BATCH_SIZE:]}

    def test_every_batch_broken_is_an_error(self):
        with pytest.raises(RuntimeError):
            run_triage(FakeLLM(broken=frozenset({"t"})), {}, [row("a", title="t")])
