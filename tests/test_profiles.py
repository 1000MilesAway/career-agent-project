"""Run profiles decide what the user pays for, so the named sets are pinned here.

No network, no LLM.
"""

from core import profiles
from core.pipeline import run_all


class TestLookup:
    def test_unknown_name_falls_back_to_the_cheapest(self):
        """A typo must never silently trigger the full package."""
        assert profiles.get("nonsense").name == "evaluate"
        assert profiles.get(None).name == "evaluate"
        assert profiles.get("").name == "evaluate"

    def test_name_is_case_and_space_insensitive(self):
        assert profiles.get("  ALL  ").name == "all"

    def test_every_profile_starts_with_evaluate(self):
        """Everything downstream consumes the evaluation report."""
        for p in profiles.PROFILES.values():
            assert p.steps[0] == "evaluate", p.name

    def test_steps_are_known_and_ordered(self):
        """Steps must follow run_all's execution order, or the report is missing."""
        order = profiles.ALL_STEPS
        for p in profiles.PROFILES.values():
            assert set(p.steps) <= set(order), p.name
            positions = [order.index(s) for s in p.steps]
            assert positions == sorted(positions), p.name


class TestShownToTheUser:
    def test_every_profile_can_be_shown_to_the_user(self):
        for row in profiles.as_list():
            assert row["title"]
            assert row["hint"]
            assert row["seconds"] > 0

    def test_no_price_reaches_the_screen(self):
        """Users see a share of their quota, never money (decided 2026-10-01)."""
        rows = profiles.as_list() + profiles.steps_as_list()
        assert all("rub" not in row for row in rows)


class TestPipelineContract:
    def test_run_all_default_matches_the_full_profile(self):
        """run_all's default must not drift away from the "all" profile."""
        import inspect

        default = inspect.signature(run_all).parameters["steps"].default
        assert tuple(default) == profiles.get("all").steps

    def test_profile_steps_are_what_run_all_understands(self):
        """The step names are a contract between profiles.py and pipeline.py."""
        source = inspect_source()
        for step in profiles.ALL_STEPS:
            assert f'"{step}"' in source, step


def inspect_source() -> str:
    import inspect

    from core import pipeline

    return inspect.getsource(pipeline.run_all)
