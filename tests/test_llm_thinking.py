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


class FakeOpenRouter(FakeCompletions):
    """OpenRouter's usage block: OpenAI-style cached tokens plus the price."""

    def __init__(self, content: str = '{"letter": "x"}') -> None:
        super().__init__()
        self.content = content

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        usage = SimpleNamespace(
            prompt_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            completion_tokens=20,
            completion_tokens_details=None,
            cost=0.0021,
        )
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message)])


def routed_llm(monkeypatch, content: str = '{"letter": "x"}'):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-no-calls-are-made")
    llm, deepseek = fake_llm()
    openrouter = FakeOpenRouter(content)
    llm.openrouter = SimpleNamespace(chat=SimpleNamespace(completions=openrouter))
    return llm, deepseek, openrouter


def test_cover_letter_goes_to_haiku_through_openrouter(monkeypatch):
    llm, deepseek, openrouter = routed_llm(monkeypatch)
    pipeline.cover_letter(llm, {"name": "x"}, {"score": 4}, "Вакансия")
    assert not deepseek.kwargs
    assert openrouter.kwargs[0]["model"] == "anthropic/claude-haiku-5.5"
    assert openrouter.kwargs[0]["extra_body"] == {"usage": {"include": True}}


def test_other_steps_stay_on_deepseek(monkeypatch):
    llm, deepseek, openrouter = routed_llm(monkeypatch)
    pipeline.evaluate(llm, {"name": "x"}, "Вакансия")
    assert not openrouter.kwargs
    assert deepseek.kwargs[0]["model"] == "deepseek-flash"


def test_no_openrouter_key_keeps_letters_on_deepseek():
    # The key reaching the server must not be a deploy step.
    llm, deepseek = fake_llm()
    pipeline.cover_letter(llm, {"name": "x"}, {"score": 4}, "Вакансия")
    assert llm.openrouter is None
    assert deepseek.kwargs[0]["model"] == "deepseek-flash"


def test_openrouter_call_is_costed_by_its_own_price(monkeypatch):
    llm, _, _ = routed_llm(monkeypatch)
    pipeline.cover_letter(llm, {"name": "x"}, {"score": 4}, "Вакансия")
    u = llm.ledger.calls[0]
    assert (u.model, u.usd, u.peak) == ("anthropic/claude-haiku-5.5", 0.0021, 1.0)
    assert (u.cache_hit, u.cache_miss, u.output) == (40, 60, 20)


def test_fenced_json_is_parsed(monkeypatch):
    llm, _, openrouter = routed_llm(monkeypatch, '```json\n{"letter": "Текст"}\n```')
    out = pipeline.cover_letter(llm, {"name": "x"}, {"score": 4}, "Вакансия")
    assert out == {"letter": "Текст"}
    assert len(openrouter.kwargs) == 1
