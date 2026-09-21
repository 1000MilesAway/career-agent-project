"""Thin LLM wrapper with per-call usage and cost accounting.

Two questions have to stay answerable at all times: does the scoring feel
honest, and what does one user actually cost. So every call records tokens
and money.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# Price per 1M tokens, USD. Off-peak; peak is x2 (01:00-04:00, 06:00-10:00 UTC, Mon-Fri).
# Cache hits on input are ~50x cheaper, which is why the system prompt goes first.
PRICING = {
    "deepseek-flash": {"in_hit": 0.003, "in_miss": 0.15, "out": 0.60},
    "deepseek-v4-pro": {"in_hit": 0.03, "in_miss": 1.00, "out": 3.00},
}
USD_RUB = float(os.getenv("USD_RUB", "95"))


def _peak_multiplier(ts: datetime | None = None) -> float:
    """DeepSeek charges double during peak windows on weekdays."""
    ts = ts or datetime.now(UTC)
    if ts.weekday() >= 5:
        return 1.0
    h = ts.hour
    return 2.0 if (1 <= h < 4 or 6 <= h < 10) else 1.0


@dataclass
class Usage:
    """One LLM call, costed."""

    step: str
    model: str
    cache_hit: int = 0
    cache_miss: int = 0
    output: int = 0
    reasoning: int = 0
    seconds: float = 0.0
    usd: float = 0.0
    peak: float = 1.0
    error: str | None = None

    @property
    def rub(self) -> float:
        return self.usd * USD_RUB

    @property
    def total_in(self) -> int:
        return self.cache_hit + self.cache_miss


@dataclass
class Ledger:
    """Collects usage across a whole run so we can total it up."""

    calls: list[Usage] = field(default_factory=list)

    def add(self, u: Usage) -> None:
        self.calls.append(u)

    @property
    def usd(self) -> float:
        return sum(c.usd for c in self.calls)

    @property
    def rub(self) -> float:
        return self.usd * USD_RUB

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.calls)

    def as_dict(self) -> dict:
        return {
            "calls": [asdict(c) for c in self.calls],
            "total_usd": round(self.usd, 6),
            "total_rub": round(self.rub, 4),
            "total_seconds": round(self.seconds, 1),
            "usd_rub": USD_RUB,
        }


def _price(model: str, u: Usage) -> float:
    p = PRICING.get(model, PRICING["deepseek-flash"])
    raw = (
        u.cache_hit * p["in_hit"] + u.cache_miss * p["in_miss"] + u.output * p["out"]
    ) / 1_000_000
    return raw * u.peak


class LLM:
    """Wrapper kept deliberately thin: swapping the model is a config change."""

    def __init__(self, model: str | None = None, ledger: Ledger | None = None) -> None:
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY не задан — положите ключ в .env")
        self.model = model or MODEL
        self.client = OpenAI(api_key=key, base_url=BASE_URL, timeout=300.0)
        self.ledger = ledger if ledger is not None else Ledger()

    def complete_json(
        self,
        step: str,
        system: str,
        user: str | list[dict],
        temperature: float = 0.2,
        # 8000 was enough until each requirement grew a basis and a JD quote:
        # a long posting then truncated mid-string and the whole evaluation
        # failed to parse. Output is billed per token used, not per limit, so
        # the headroom is free unless it is actually spent.
        max_tokens: int = 16000,
        retries: int = 1,
    ) -> dict:
        """Ask for JSON, validate it, retry once on malformed output."""
        content = user if isinstance(user, list) else [{"type": "text", "text": user}]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            u = Usage(step=step, model=self.model, peak=_peak_multiplier())
            started = time.monotonic()
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:  # network, rate limit, auth
                u.seconds = time.monotonic() - started
                u.error = f"{type(e).__name__}: {e}"[:300]
                self.ledger.add(u)
                last_err = e
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise

            u.seconds = time.monotonic() - started
            usage = r.usage
            u.cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
            u.cache_miss = getattr(usage, "prompt_cache_miss_tokens", 0) or usage.prompt_tokens
            u.output = usage.completion_tokens or 0
            details = getattr(usage, "completion_tokens_details", None)
            u.reasoning = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
            u.usd = _price(self.model, u)

            text = r.choices[0].message.content or ""
            try:
                parsed = json.loads(text)
                self.ledger.add(u)
                return parsed
            except json.JSONDecodeError as e:
                u.error = "invalid json"
                self.ledger.add(u)
                last_err = e
                if attempt < retries:
                    messages.append({"role": "assistant", "content": text[:2000]})
                    messages.append(
                        {"role": "user", "content": "Ответ не распарсился. Верни только валидный JSON."}
                    )
                    continue
                raise RuntimeError(f"модель не вернула валидный JSON на шаге {step}") from e

        raise RuntimeError(f"шаг {step} не удался") from last_err
