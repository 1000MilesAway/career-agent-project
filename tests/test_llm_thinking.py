"""Which steps ask DeepSeek not to think. No network: the client is a fake."""

from types import SimpleNamespace

from core import pipeline
from core.llm import LLM


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        usage = SimpleNamespace(
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=10,
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=None,
        )
        message = SimpleNamespace(content='{"score": 4}')
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message)])


def fake_llm() -> tuple[LLM, FakeCompletions]:
    llm = LLM()
    completions = FakeCompletions()
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


def test_thinking_stays_the_api_default_unless_turned_off():
    llm, completions = fake_llm()
    llm.complete_json(step="x", system="s", user="u")
    assert "extra_body" not in completions.kwargs[0]


def test_evaluate_asks_for_no_reasoning():
    # Reasoning ate ~70% of an evaluation's output and once the whole limit.
    llm, completions = fake_llm()
    pipeline.evaluate(llm, {"name": "x"}, "Вакансия")
    assert completions.kwargs[0]["extra_body"] == {"thinking": {"type": "disabled"}}
