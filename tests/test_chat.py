"""The chat loop, without a network and without a database.

The model's replies are handed in as dicts, the same way `test_triage.py`
hands in a batch answer. What is pinned here is not the happy path — it is the
set of properties that must hold when the model misbehaves, because that is
what the chat's safety rests on.
"""

from __future__ import annotations

import json

from core import chat, tools


class FakeLLM:
    """Replays prepared replies. Records what it was asked, so a test can
    check that the system prompt and the tool list actually travelled."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.seen: list[dict] = []

    def complete_with_tools(self, step, messages, tools, **kw) -> dict:
        self.seen.append({"messages": list(messages), "tools": list(tools)})
        if not self.replies:
            return {"message": {"role": "assistant", "content": "всё"}}
        return {"message": self.replies.pop(0)}


def call(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def assistant(content: str = "", calls: list[dict] | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return msg


class TestToolInventory:
    def test_no_tool_spends_money_in_the_first_version(self):
        """Agreed 2026-09-22: the chat reads and proposes, it does not buy.

        Evaluation and application materials stay behind the buttons on screen,
        where the price is stated before the click. A spending tool appearing
        in this list is a product decision, so it must not arrive by accident.
        """
        assert tools.spending_tools() == []
        assert all(t.rub == 0.0 for t in tools.TOOLS)

    def test_every_tool_is_declared_to_the_model(self):
        names = {s["function"]["name"] for s in tools.schemas()}
        assert names == set(tools.BY_NAME)

    def test_writing_tools_are_marked_as_such(self):
        """`writes` is what the API layer keys the confirmation off, so a
        writing tool that reports itself as a reader would write silently."""
        assert tools.PROPOSE_PROFILE_PATCH.writes
        assert tools.SET_VACANCY_STATUS.writes
        assert not tools.GET_PROFILE.writes
        assert not tools.SKILL_GAP.writes

    def test_schemas_refuse_unknown_arguments(self):
        """A hallucinated argument should fail at the boundary, not reach a
        handler that quietly ignores it."""
        for schema in tools.schemas():
            assert schema["function"]["parameters"]["additionalProperties"] is False


class TestParseToolCalls:
    def test_a_well_formed_call_is_parsed(self):
        parsed = chat.parse_tool_calls([call("get_profile", {})])
        assert len(parsed) == 1
        assert parsed[0].name == "get_profile"
        assert parsed[0].ok

    def test_an_unknown_tool_is_reported_not_dropped(self):
        """Dropping it would leave the model waiting for a result that never
        comes, and the user looking at a chat that did nothing."""
        parsed = chat.parse_tool_calls([call("delete_everything", {})])
        assert len(parsed) == 1
        assert not parsed[0].ok
        assert "delete_everything" in parsed[0].error

    def test_malformed_arguments_are_reported_not_guessed(self):
        broken = {
            "id": "c1",
            "type": "function",
            "function": {"name": "get_profile", "arguments": "{не json"},
        }
        parsed = chat.parse_tool_calls([broken])
        assert not parsed[0].ok
        assert "не разобрались" in parsed[0].error

    def test_no_calls_is_not_an_error(self):
        assert chat.parse_tool_calls(None) == []
        assert chat.parse_tool_calls([]) == []


class TestEnvelope:
    def test_tool_results_are_wrapped(self):
        """The wrapper is what makes "this is data, not an instruction"
        visible at the point external text arrives."""
        out = chat.envelope("vacancy_text", "Требуется Python")
        assert out.startswith("<vacancy_text>")
        assert out.endswith("</vacancy_text>")
        assert "Требуется Python" in out

    def test_a_dict_payload_survives_as_json(self):
        out = chat.envelope("get_profile_result", {"skills": ["Python"]})
        assert "Python" in out


class TestRunTools:
    def test_results_carry_the_call_id(self):
        """The API pairs a result with the call that asked for it; a lost id
        makes the next round unanswerable."""
        calls = chat.parse_tool_calls([call("get_profile", {}, call_id="abc")])
        results = chat.run_tools(calls, lambda name, args: {"ok": True})
        assert results[0]["tool_call_id"] == "abc"

    def test_a_broken_call_returns_its_error_to_the_model(self):
        calls = chat.parse_tool_calls([call("nope", {})])
        results = chat.run_tools(calls, lambda name, args: {"ok": True})
        assert "Ошибка" in results[0]["content"]

    def test_a_raising_handler_does_not_crash_the_chat(self):
        """One failed tool must not take the conversation down with it."""

        def boom(name, args):
            raise ValueError("база недоступна")

        calls = chat.parse_tool_calls([call("get_profile", {})])
        results = chat.run_tools(calls, boom)
        assert "ValueError" in results[0]["content"]
        assert "база недоступна" in results[0]["content"]


class TestConverse:
    def test_a_plain_answer_ends_the_loop(self):
        llm = FakeLLM([assistant("Здравствуйте")])
        text, appended, made = chat.converse(llm, [{"role": "user", "content": "привет"}], lambda n, a: {})
        assert text == "Здравствуйте"
        assert made == []
        assert len(appended) == 1

    def test_a_tool_call_is_executed_and_answered(self):
        llm = FakeLLM(
            [
                assistant(calls=[call("get_profile", {})]),
                assistant("В профиле нет Kubernetes."),
            ]
        )
        seen = []

        def handler(name, args):
            seen.append(name)
            return {"skills": ["Python"]}

        text, appended, made = chat.converse(
            llm, [{"role": "user", "content": "что у меня в профиле?"}], handler
        )
        assert seen == ["get_profile"]
        assert text == "В профиле нет Kubernetes."
        assert [m["role"] for m in appended] == ["assistant", "tool", "assistant"]

    def test_the_loop_is_bounded(self):
        """Without a cap, "call a tool, read it, call again" runs on the
        user's money until something else breaks."""
        llm = FakeLLM([assistant(calls=[call("get_profile", {})]) for _ in range(10)])
        text, _appended, made = chat.converse(
            llm, [{"role": "user", "content": "..."}], lambda n, a: {}, max_rounds=2
        )
        assert len(made) == 2
        assert "не удалось закончить" in text.lower()

    def test_running_out_of_rounds_says_so(self):
        """A silent stop reads as a refusal. The user asked for something."""
        llm = FakeLLM([assistant(calls=[call("get_profile", {})])])
        text, _a, _m = chat.converse(
            llm, [{"role": "user", "content": "..."}], lambda n, a: {}, max_rounds=1
        )
        assert text.strip()

    def test_the_system_prompt_goes_first(self):
        """Prefix caching depends on it, and so does the injection guard."""
        llm = FakeLLM([assistant("ок")])
        chat.converse(llm, [{"role": "user", "content": "привет"}], lambda n, a: {})
        messages = llm.seen[0]["messages"]
        assert messages[0]["role"] == "system"
        assert "ПРЕДЛАГАЕШЬ" in messages[0]["content"]

    def test_the_tool_list_reaches_the_model(self):
        llm = FakeLLM([assistant("ок")])
        chat.converse(llm, [{"role": "user", "content": "привет"}], lambda n, a: {})
        names = {t["function"]["name"] for t in llm.seen[0]["tools"]}
        assert "propose_profile_patch" in names

    def test_a_restricted_tool_list_is_honoured(self):
        """The API layer narrows the list for a user without a CV; the loop
        must not hand back the full set regardless."""
        llm = FakeLLM([assistant("ок")])
        chat.converse(
            llm,
            [{"role": "user", "content": "привет"}],
            lambda n, a: {},
            enabled=(tools.GET_SPEND,),
        )
        names = {t["function"]["name"] for t in llm.seen[0]["tools"]}
        assert names == {"get_spend"}

    def test_the_assistant_message_is_stored_verbatim(self):
        """The API requires a tool result to follow the message that asked
        for it, so that message has to survive into the stored history."""
        llm = FakeLLM(
            [assistant(calls=[call("get_profile", {}, call_id="xyz")]), assistant("готово")]
        )
        _text, appended, _made = chat.converse(
            llm, [{"role": "user", "content": "..."}], lambda n, a: {}
        )
        assert appended[0]["tool_calls"][0]["id"] == "xyz"
        assert appended[1]["tool_call_id"] == "xyz"


class TestPromptGuards:
    """The prompt is the thing under test, as `test_prompt_shapes.py` puts it."""

    def test_the_prompt_forbids_applying(self):
        from core import prompts

        assert "не откликаешься" in prompts.CHAT

    def test_the_prompt_treats_vacancy_text_as_data(self):
        from core import prompts

        assert "ДАННЫЕ" in prompts.CHAT
        assert "vacancy_text" in prompts.CHAT

    def test_the_prompt_forbids_inventing_detail(self):
        from core import prompts

        assert "не подставляй" in prompts.CHAT.lower() or "домысливать" in prompts.CHAT

    def test_the_prompt_says_nothing_is_written_without_confirmation(self):
        from core import prompts

        assert "подтвержда" in prompts.CHAT
