"""`/api/health` — the only thing that can confirm a deploy from outside.

The revision field was added after a push to main reached the server and there
was no way to tell, from off the host, whether the running container was the
new one: nothing broke, but nothing breaking is also what a deploy that never
ran looks like.

The trap it has to survive is a stale value. compose falls back to `.env` when
GIT_SHA is unset in the shell, so a sha written into a real .env would be
served forever as "the running revision" — the precise lie this field exists
to prevent. Hence: deploy.sh passes it per run, .env.example says never to put
it there, and "unknown" stays distinguishable from any sha.
"""

from __future__ import annotations

from app.core.config import Settings
from tests.dbmark import requires_db


class TestRevisionSetting:
    """No database needed: this is about how the value is read."""

    def test_it_defaults_to_unknown_rather_than_empty(self):
        """An empty string would render as a blank field and read as a bug."""
        assert Settings(_env_file=None).git_sha == "unknown"

    def test_it_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "b618337")
        assert Settings(_env_file=None).git_sha == "b618337"

    def test_unknown_is_not_mistakable_for_a_sha(self):
        """A hand-started stack must be tellable from a deployed one."""
        default = Settings(_env_file=None).git_sha
        assert not all(c in "0123456789abcdef" for c in default)


@requires_db
class TestHealthPayload:
    async def test_it_reports_the_running_revision(self, client, monkeypatch):
        """The whole point: confirm which revision answers, without ssh."""
        from app.core import config

        monkeypatch.setenv("GIT_SHA", "deadbee")
        config.get_settings.cache_clear()
        try:
            body = (await client.get("/api/health")).json()
            assert body["revision"] == "deadbee"
        finally:
            config.get_settings.cache_clear()

    async def test_it_still_answers_the_questions_it_already_answered(self, client):
        """The revision is an addition; a monitor watching status must not break."""
        body = (await client.get("/api/health")).json()
        assert body["status"] == "ok"
        assert body["database"] is True
        assert body["model"]

    async def test_no_login_is_required(self, client):
        """A monitor should not need an account."""
        assert (await client.get("/api/health")).status_code == 200
