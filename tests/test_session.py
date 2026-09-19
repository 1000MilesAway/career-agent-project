"""The resume must survive a server restart — re-parsing costs money.

No network, no LLM.
"""

from probe import session

PROFILE = {"target_role": "Python-разработчик", "years_experience": 6}


class TestRoundTrip:
    def test_saved_resume_loads_back(self, tmp_path):
        p = tmp_path / "state.json"
        session.save(PROFILE, "текст резюме", "cv.md", p)
        back = session.load(p)
        assert back["profile"] == PROFILE
        assert back["resume_text"] == "текст резюме"
        assert back["resume_name"] == "cv.md"

    def test_missing_file_loads_as_none(self, tmp_path):
        assert session.load(tmp_path / "nope.json") is None

    def test_corrupt_file_loads_as_none(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("{broken", encoding="utf-8")
        assert session.load(p) is None

    def test_file_without_a_profile_is_not_a_session(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text('{"resume_name": "cv.md"}', encoding="utf-8")
        assert session.load(p) is None

    def test_clear_forgets_the_resume(self, tmp_path):
        p = tmp_path / "state.json"
        session.save(PROFILE, "текст", "cv.md", p)
        session.clear(p)
        assert session.load(p) is None

    def test_clearing_a_missing_file_is_not_an_error(self, tmp_path):
        session.clear(tmp_path / "nope.json")


class TestSearchProfile:
    def test_accepted_search_profile_is_stored_next_to_the_resume(self, tmp_path):
        p = tmp_path / "state.json"
        session.save(PROFILE, "текст", "cv.md", p)
        session.save_search_profile({"queries": ["Python"]}, p)

        back = session.load(p)
        assert back["search_profile"]["queries"] == ["Python"]
        assert back["profile"] == PROFILE, "resume must not be lost"

    def test_resume_save_keeps_an_existing_search_profile(self, tmp_path):
        """Re-uploading a CV should not silently drop the confirmed search profile."""
        p = tmp_path / "state.json"
        session.save(PROFILE, "текст", "cv.md", p)
        session.save_search_profile({"queries": ["Python"]}, p)
        session.save(PROFILE, "новый текст", "cv2.md", p)

        assert session.load(p).get("search_profile", {}).get("queries") == ["Python"]
