"""Which link in a post is worth fetching a description from.

The mirror image of test_apply_link: that one asks where the user applies, this
one asks where the vacancy is described. Same hrefs, opposite answers for
telegra.ph — nobody applies through an article, and yet that is where most
teasers keep the requirements.
"""

from core.sources import apply_link, description_link
from core.sources.description_link import is_description_link, pick


class TestWhitelist:
    def test_the_article_hosts_pass(self):
        assert is_description_link("https://telegra.ph/Python-Backend-09-17")
        assert is_description_link("https://teletype.in/@hr/vacancy")
        assert is_description_link("https://graph.org/Job-09-17")

    def test_a_subdomain_of_a_listed_host_passes(self):
        assert is_description_link("https://company.notion.site/Backend-1234")

    def test_a_lookalike_host_is_refused(self):
        """Substring matching would let "telegra.ph.evil.com" through."""
        assert not is_description_link("https://telegra.ph.evil.com/job")
        assert not is_description_link("https://nottelegra.ph/job")

    def test_an_unlisted_host_is_refused(self):
        """The default is no: an unknown host repays the request with a landing page."""
        assert not is_description_link("https://example-tracker.invalid/job/42")

    def test_plain_http_is_refused(self):
        assert not is_description_link("http://telegra.ph/Job-09-17")

    def test_non_http_schemes_are_refused(self):
        assert not is_description_link("file:///etc/passwd")
        assert not is_description_link("javascript:alert(1)")

    def test_a_malformed_url_is_refused_not_raised(self):
        assert not is_description_link("https://[")
        assert not is_description_link("")


class TestPick:
    def test_takes_the_first_whitelisted_link(self):
        """The teaser's "Смотреть вакансию" opens the post; later links are extra."""
        assert pick([
            "https://t.me/hr_channel",
            "https://telegra.ph/first",
            "https://telegra.ph/second",
        ]) == "https://telegra.ph/first"

    def test_no_whitelisted_link_gives_empty(self):
        assert pick(["https://t.me/hr", "https://example.invalid/x"]) == ""

    def test_empty_list_is_not_a_crash(self):
        assert pick([]) == ""


class TestAgainstApplyLink:
    """The two modules answer different questions and must not be merged."""

    def test_telegraph_describes_but_does_not_apply(self):
        url = "https://telegra.ph/Python-Backend-AI-agenty-09-17"
        assert is_description_link(url)
        assert apply_link.classify(url) is None

    def test_a_board_posting_can_be_both(self):
        """hh.ru carries the description AND is where the user applies."""
        url = "https://hh.ru/vacancy/12345678"
        assert is_description_link(url)
        assert apply_link.classify(url) is not None

    def test_a_post_can_carry_one_of_each(self):
        hrefs = [
            "https://telegra.ph/Vacancy-09-17",
            "https://vydumannaya-company.invalid/jobs/backend-1234",
        ]
        assert description_link.pick(hrefs) == "https://telegra.ph/Vacancy-09-17"
        chosen = apply_link.pick(hrefs)
        assert chosen is not None
        assert "telegra.ph" not in chosen.url
