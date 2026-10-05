"""A fraud mark for postings: dictionaries and rules, no model.

A free check, like the stop-words, but it marks instead of dropping: a
legitimate posting that trips a rule must stay visible, and the user decides
with the reason in front of them. The rules are data
(data/sources/fraud-signals.json) because they age the way stop-words do.

Two strengths. A strong signal — the candidate is asked to pay, a bank-card
scheme, drug drops — marks on its own. A weak one — "write me in DM", "no
experience" — is common in honest Telegram posts, and only two different weak
signals together mark a posting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "sources" / "fraud-signals.json"

# The mark is a label on a card; the column holding it is this long.
MAX_REASON = 256


@dataclass(frozen=True)
class Signal:
    reason: str
    pattern: re.Pattern[str]
    # A context that makes the words the job's subject rather than its
    # scheme: an anti-fraud analyst's posting talks about drops all day.
    unless: re.Pattern[str] | None = None

    def found(self, body: str) -> bool:
        if not self.pattern.search(body):
            return False
        return not (self.unless and self.unless.search(body))


@dataclass(frozen=True)
class Rules:
    strong: tuple[Signal, ...]
    weak: tuple[Signal, ...]


def _signals(items: list[dict]) -> tuple[Signal, ...]:
    return tuple(
        Signal(
            i["reason"],
            re.compile(i["pattern"]),
            re.compile(i["unless"]) if i.get("unless") else None,
        )
        for i in items
    )


@lru_cache(maxsize=4)
def load(path: Path = DEFAULT_PATH) -> Rules:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Rules(_signals(data.get("strong", [])), _signals(data.get("weak", [])))


def _normalise(text: str) -> str:
    # One spelling for the rules to match: lower case, ё as е, and runs of
    # whitespace (post line breaks, non-breaking spaces) as one space.
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е"))


def check(title: str, text: str | None, rules: Rules | None = None) -> str | None:
    """Why the posting looks like fraud, or None.

    The reason names what was found, so a false mark can be read as one.
    """
    rules = rules or load()
    body = _normalise(f"{title}\n{text or ''}")
    for signal in rules.strong:
        if signal.found(body):
            return f"похоже на мошенничество: {signal.reason}"[:MAX_REASON]
    weak = [s.reason for s in rules.weak if s.found(body)]
    if len(weak) >= 2:
        return f"похоже на мошенничество: {', '.join(weak)}"[:MAX_REASON]
    return None
