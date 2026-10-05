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
from typing import Any

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
# CBR rate on 2026-10-01. Only the owner's own accounting reads it: users see a
# share of their budget, never roubles.
USD_RUB = float(os.getenv("USD_RUB", "83.56"))


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
    def offpeak_usd(self) -> float:
        """What the run would have cost outside DeepSeek's peak hours.

        This, not `usd`, is what a user's quota is charged: the same button
        must take the same share of the budget at 10:00 and at 15:00. The peak
        surcharge is the owner's cost, not the user's.
        """
        return sum(c.usd / (c.peak or 1.0) for c in self.calls)

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
        # the headroom is free unless it is actually spent. With thinking on,
        # the reasoning counts against this limit too: 13.5k of 16k went on it
        # once and the JSON was cut off, paid for and thrown away.
        max_tokens: int = 16000,
        retries: int = 1,
        # DeepSeek thinks by default. On a step whose answer is a filled-in
        # schema the reasoning was ~70% of the output tokens and most of the
        # latency, and thinking mode also ignores `temperature`.
        thinking: bool = True,
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
                    **({} if thinking else {"extra_body": {"thinking": {"type": "disabled"}}}),
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

    def _record(self, u: Usage, response: Any) -> None:
        """Cost one reply. Same accounting as complete_json, one copy."""
        usage = response.usage
        u.cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        u.cache_miss = getattr(usage, "prompt_cache_miss_tokens", 0) or usage.prompt_tokens
        u.output = usage.completion_tokens or 0
        details = getattr(usage, "completion_tokens_details", None)
        u.reasoning = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        u.usd = _price(self.model, u)
        self.ledger.add(u)

    def complete_with_tools(
        self,
        step: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4000,
    ) -> dict:
        """One round of a tool-using conversation.

        Unlike `complete_json` there is no retry and no JSON mode: the model is
        free to answer in words, and the caller (`core/chat.py`) owns the loop.
        Retrying here would re-run tool calls the caller has already executed.

        The reply is returned as a plain dict so it can be stored and replayed:
        the API requires a tool result to follow the assistant message that
        asked for it, so that message has to survive verbatim.
        """
        u = Usage(step=step, model=self.model, peak=_peak_multiplier())
        started = time.monotonic()
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            u.seconds = time.monotonic() - started
            u.error = f"{type(e).__name__}: {e}"[:300]
            self.ledger.add(u)
            raise

        u.seconds = time.monotonic() - started
        self._record(u, r)

        message = r.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )

        out: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if calls:
            out["tool_calls"] = calls
        return {"message": out}
