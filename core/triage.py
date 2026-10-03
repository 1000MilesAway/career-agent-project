"""Second screen: a few cheap model calls over the whole shortlist.

The free filters read keywords and fields; the full evaluation reads the whole
posting and costs 0.17 RUB each. Between them sits this — career-ops calls it
triage (docs/AUTOMATION.md): one small prompt that sorts the survivors into
"worth a look / maybe / skip" so the expensive step is aimed at a short list.

Two rules carried over from theirs, both deliberate:

  - It judges LISTING DATA ONLY — title, company, grade, salary, division,
    skills. No page is fetched. Their prompt is explicit that this is "a
    zero-cost first glance, not an evaluation"; fetching 53 pages to decide
    what not to read would defeat the point.
  - Nothing is deleted. Every verdict carries a reason and is written back to
    the posting, because a screen that silently drops postings is a black box —
    they keep data/discard.log for exactly this.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from . import prompts
from .llm import LLM

WORTH = "worth"
MAYBE = "maybe"
SKIP = "skip"
BUCKETS = (WORTH, MAYBE, SKIP)

# What the model is allowed to see. Anything not on this list either costs a
# page fetch or is noise the verdict should not rest on.
LISTING_FIELDS = (
    "id", "title", "company", "qualification", "salary_text", "divisions", "skills",
)


def posting_digest(row: dict) -> dict:
    """One posting, trimmed to the listing facts the prompt is built from."""
    out: dict[str, Any] = {}
    for key in LISTING_FIELDS:
        value = row.get(key)
        if key == "skills" and isinstance(value, list):
            # A long skill list is mostly noise and inflates every prompt.
            value = value[:12]
        if value:
            out[key] = value
    return out


def build_archetypes(llm: LLM, profile: dict) -> dict:
    """Derive the candidate's target roles once, so triage compares against
    a short list of archetypes instead of re-reading the whole CV each time."""
    return llm.complete_json(
        step="archetypes",
        system=prompts.ARCHETYPES,
        user=f"Профиль кандидата:\n{json.dumps(profile, ensure_ascii=False, indent=2)}",
        temperature=0.2,
    )


@dataclass
class TriageResult:
    """Verdicts keyed by posting id, plus whatever could not be matched."""

    verdicts: dict[str, dict] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(BUCKETS, 0)
        for v in self.verdicts.values():
            out[v["bucket"]] = out.get(v["bucket"], 0) + 1
        return out


def parse_verdicts(raw: dict, rows: list[dict]) -> TriageResult:
    """Match the model's answer back onto the postings.

    The model is asked for one verdict per posting, but a batch call can come
    back short or carry an id that was never sent. Both are reported rather
    than guessed at: a posting with no verdict keeps its place in the list, and
    inventing "skip" for it would quietly hide a job nobody judged.
    """
    wanted = {str(r.get("id")) for r in rows if r.get("id")}
    result = TriageResult()

    for item in raw.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "")
        bucket = str(item.get("bucket") or "").strip().lower()
        if not vid or vid not in wanted:
            if vid:
                result.unknown.append(vid)
            continue
        if bucket not in BUCKETS:
            # An unrecognised bucket is treated as the cautious one: "maybe"
            # keeps the posting visible instead of dropping it on a typo.
            bucket = MAYBE
        result.verdicts[vid] = {
            "bucket": bucket,
            "archetype": item.get("archetype") or None,
            "reason": (item.get("reason") or "").strip(),
        }

    result.missing = sorted(wanted - set(result.verdicts))
    return result


# Postings per model call. The whole shortlist in one call (230 on
# 2026-10-03) outgrew max_tokens: the reply was cut mid-JSON and the job
# failed outright. Forty keep a reply around 3k tokens, far from the limit.
BATCH_SIZE = 40
# Batches run side by side; the prefix (prompt + archetypes) is shared and
# cached, so splitting costs little and parallel calls keep the wait short.
PARALLEL_CALLS = 4


def _judge_batch(llm: LLM, archetypes: dict, rows: list[dict]) -> TriageResult:
    """One call over one batch, with the ids swapped for short numbers.

    A UUID is ~25 output tokens the model must copy back verbatim per verdict;
    a number is one or two, and cannot be miscopied into a near-miss id.
    """
    local = {str(n): str(r["id"]) for n, r in enumerate(rows, 1)}
    digests = [posting_digest({**r, "id": n}) for n, r in zip(local, rows, strict=True)]
    user = (
        f"Целевые роли кандидата:\n{json.dumps(archetypes, ensure_ascii=False, indent=2)}\n\n"
        f"Вакансии ({len(digests)}):\n{json.dumps(digests, ensure_ascii=False, indent=2)}"
    )
    raw = llm.complete_json(
        step="triage",
        system=prompts.TRIAGE,
        user=user,
        temperature=0.1,
    )
    short = parse_verdicts(raw, [{"id": n} for n in local])
    return TriageResult(
        verdicts={local[n]: v for n, v in short.verdicts.items()},
        missing=[local[n] for n in short.missing],
        unknown=short.unknown,
    )


def run_triage(llm: LLM, archetypes: dict, rows: list[dict]) -> TriageResult:
    """Judge the shortlist in batches. Free filters have already run.

    A batch whose reply is still unreadable after the retry does not sink the
    others: its postings go to `missing`, shown and unjudged. Only when every
    batch failed is the error raised — then there is nothing to show but it.
    """
    rows = [r for r in rows if r.get("id")]
    if not rows:
        return TriageResult()

    batches = [rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        futures = [pool.submit(_judge_batch, llm, archetypes, b) for b in batches]

    result = TriageResult()
    errors: list[Exception] = []
    for batch, future in zip(batches, futures, strict=True):
        try:
            part = future.result()
        except RuntimeError as e:
            errors.append(e)
            result.missing += [str(r["id"]) for r in batch]
            continue
        result.verdicts.update(part.verdicts)
        result.missing += part.missing
        result.unknown += part.unknown
    if len(errors) == len(batches):
        raise errors[0]
    result.missing.sort()
    return result


def apply_verdicts(rows: list[dict], result: TriageResult) -> list[dict]:
    """Write verdicts onto the stored rows. Nothing is removed."""
    for row in rows:
        verdict = result.verdicts.get(str(row.get("id")))
        if verdict:
            row["triage"] = verdict
    return rows
