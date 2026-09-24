"""Does the scoring still obey its own rules?

Borrowed from career-ops `eval-golden.mjs`, which replays a labeled golden set
and fails CI when a model stops agreeing with the reference. The mechanism
transfers; the labels do not, because their taxonomy is their domain.

What transfers better is something their harness cannot check: our EVALUATE
prompt states its rules in the open — the verdict follows from the score, an
inferred requirement may not be called critical, and "everyone gets a 4" is
forbidden. Those are checkable without a model, so this costs nothing to run
and needs no key.

Two ways to run it:
  - stored  — judge the reports already in out/found.json (free, no model)
  - golden  — replay the labeled cases in data/golden/ (free, no model)

The expensive failure this guards against: a prompt edit that makes the model
flatter. That shows up as verdicts drifting away from their own thresholds and
as the spread collapsing — both visible without spending a rouble.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import pstdev

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "data" / "golden"

# The thresholds EVALUATE states in its own prompt. Kept here as data so a
# prompt edit that moves them fails loudly instead of quietly disagreeing.
APPLY_AT = 4.0
SKIP_BELOW = 3.0

# Importance bands, highest first. Same five as career-ops (`oferta.md`):
# a binary must/nice forced every real requirement into "must" and then let one
# unmet row sink the score — which is how a strong CV candidate scored 2.5 on a
# posting career-ops scored 4.4.
WEIGHTS = ("critical", "high", "meaningful", "preferred", "low_signal")

# Bands an "inferred" basis may never claim. Market intuition is allowed, but it
# does not get to create obligations — career-ops calls this "the gate".
INFERRED_MAX_BANDS = ("critical", "high")

# Below this spread the model is flattering rather than judging. Week 0 named
# the stop-criterion "everything is a 4"; this is that criterion as a number.
MIN_SPREAD = 0.5

# Distance from a golden label still counted as agreement. Same idea as
# career-ops' SCORE_TOLERANCE, same default.
SCORE_TOLERANCE = 0.5


def verdict_for(score: float) -> str:
    """The verdict the prompt's own rule demands for this score."""
    if score >= APPLY_AT:
        return "apply"
    if score < SKIP_BELOW:
        return "skip"
    return "apply_with_caveats"


@dataclass
class Finding:
    """One thing that is wrong, named so it can be acted on."""

    case: str
    kind: str
    detail: str

    def as_dict(self) -> dict:
        return {"case": self.case, "kind": self.kind, "detail": self.detail}


@dataclass
class Result:
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    agreed: int = 0
    labeled: int = 0

    @property
    def spread(self) -> float:
        """How far apart the scores are. A flattering model scores flat."""
        return round(pstdev(self.scores), 2) if len(self.scores) > 1 else 0.0

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        out = {
            "checked": self.checked,
            "ok": self.ok,
            "spread": self.spread,
            "findings": [f.as_dict() for f in self.findings],
        }
        if self.labeled:
            out["agreement"] = round(self.agreed / self.labeled, 2)
            out["labeled"] = self.labeled
        return out


def check_report(case: str, report: dict, result: Result, schema: str = "") -> None:
    """Check one evaluation against the rules the prompt states for itself.

    `schema` names the rubric a stored report was produced under. A v1 report
    used a binary must/nice weight and a hard score cap; judging it by the
    current bands would report the old rubric as a defect on every run. The
    score and verdict rules apply to both — only the requirement checks differ.
    """
    legacy = schema.startswith("v1")
    score = report.get("score")
    verdict = report.get("verdict")

    if not isinstance(score, (int, float)):
        result.findings.append(Finding(case, "нет балла", f"score={score!r}"))
        return
    score = float(score)
    result.checked += 1
    result.scores.append(score)

    if not 1.0 <= score <= 5.0:
        result.findings.append(Finding(case, "балл вне шкалы", f"{score}"))

    # The prompt asks for steps of 0.1. A 4.25 means it stopped reading the rule.
    # Compared with a tolerance: 4.3 * 10 is 42.99999... in binary floating point.
    if abs(score * 10 - round(score * 10)) > 1e-6:
        result.findings.append(Finding(case, "балл не кратен 0.1", f"{score}"))

    expected = verdict_for(score)
    if verdict != expected:
        # One sanctioned exception, added to EVALUATE after calibration caught a
        # real disagreement: a 4.0 posting with no salary, a junior grade hidden
        # under a senior title and a fixed-term contract IS "with caveats", and
        # the model was right to say so. Downgrading on red flags is allowed —
        # downgrading silently is not, so the flags have to be there.
        downgraded_with_cause = (
            expected == "apply"
            and verdict == "apply_with_caveats"
            and bool(report.get("red_flags"))
        )
        if not downgraded_with_cause:
            result.findings.append(Finding(
                case, "вердикт не следует из балла",
                f"score={score} → ожидался {expected}, получен {verdict!r}",
            ))

    for r in (report.get("requirements") or []) if not legacy else []:
        weight, basis = r.get("weight"), r.get("basis")
        text = (r.get("text") or "?")[:60]

        if weight and weight not in WEIGHTS:
            result.findings.append(Finding(
                case, "неизвестная важность требования", f"{weight!r} у {text!r}",
            ))

        # The gate: an inferred band cannot create an obligation. Without it the
        # model can invent a "critical" requirement the posting never stated and
        # sink the score on its own guess.
        if basis == "inferred" and weight in INFERRED_MAX_BANDS:
            result.findings.append(Finding(
                case, "догадка объявлена обязательной",
                f"basis=inferred не может быть {weight!r}: {text!r}",
            ))

        # A "stated" requirement claims the posting says so — in its own words.
        if basis == "stated" and not (r.get("jd_signal") or "").strip():
            result.findings.append(Finding(
                case, "stated без цитаты из вакансии", f"{text!r}",
            ))

    # The failure this was built for: one unmet row sinking a posting whose core
    # the candidate actually covers. Judged on the top two bands only — a gap in
    # "preferred" was never supposed to matter.
    top = [] if legacy else [r for r in report.get("requirements") or []
                             if r.get("weight") in INFERRED_MAX_BANDS]
    if len(top) >= 4:
        # A partial match is half-credit, not a miss: the candidate who did the
        # adjacent thing on the same stack is not in the same position as the
        # one who never touched it. Counting only "full" was how a posting with
        # five full and four partial rows still read as a poor fit.
        covered = sum(1.0 if r.get("match") == "full"
                      else 0.5 if r.get("match") == "partial" else 0.0
                      for r in top)
        if covered / len(top) >= 0.6 and score < 3.5:
            result.findings.append(Finding(
                case, "ядро закрыто, а балл низкий",
                f"закрыто {covered:g} из {len(top)} важных требований"
                f" (partial считается за половину), но score={score}"
                " — похоже на потолок из-за отдельных пробелов",
            ))

    # Evidence is the whole product. A requirement judged "full" with nothing
    # behind it is the failure mode week 0 was watching for.
    for r in report.get("requirements") or []:
        if r.get("match") == "full" and not (r.get("evidence") or "").strip():
            result.findings.append(Finding(
                case, "совпадение без обоснования", f"{r.get('text', '?')!r}",
            ))
            break


def check_spread(result: Result) -> None:
    """Flag a flat run. Judged once per run, not per case."""
    if len(result.scores) > 2 and result.spread < MIN_SPREAD:
        result.findings.append(Finding(
            "—", "баллы почти не различаются",
            f"разброс {result.spread} < {MIN_SPREAD}: модель льстит, а не судит",
        ))


def from_stored(rows: list[dict]) -> Result:
    """Judge the reports already bought. Free: nothing is called."""
    result = Result()
    for row in rows:
        report = row.get("report")
        if report:
            check_report(row.get("title") or row.get("id", "?"), report, result,
                         row.get("schema", ""))
    check_spread(result)
    return result


def load_golden(path: Path | None = None) -> list[dict]:
    """Labeled cases. Absent directory is not an error — the set is optional."""
    path = path or GOLDEN_DIR
    if not path.exists():
        return []
    cases = []
    for f in sorted(path.glob("*.json")):
        try:
            cases.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ValueError(f"{f.name}: не разбирается как JSON — {e}") from e
    return cases


def from_golden(cases: list[dict]) -> Result:
    """Replay labeled cases: does the recorded answer still match its label?

    Agreement-with-label, exactly as career-ops measures it. The label is a
    frozen verdict, not absolute truth — it answers "did this drift", which is
    the question a prompt edit raises.
    """
    result = Result()
    for case in cases:
        name = case.get("id", "?")
        report = case.get("report") or {}
        check_report(name, report, result, case.get("schema", ""))

        label = case.get("label") or {}
        if "score" not in label:
            continue
        result.labeled += 1
        got = report.get("score")
        if isinstance(got, (int, float)) and abs(float(got) - float(label["score"])) <= (
            SCORE_TOLERANCE
        ):
            result.agreed += 1
        else:
            result.findings.append(Finding(
                name, "балл разошёлся с эталоном",
                f"эталон {label['score']}, получено {got} (допуск ±{SCORE_TOLERANCE})",
            ))

        if label.get("verdict") and report.get("verdict") != label["verdict"]:
            result.findings.append(Finding(
                name, "вердикт разошёлся с эталоном",
                f"эталон {label['verdict']!r}, получено {report.get('verdict')!r}",
            ))

    check_spread(result)
    return result
