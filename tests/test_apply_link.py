"""Which link a post gives the user to apply with.

career-agent never applies on the user's behalf, so handing them the right link
IS the product's last step. Getting it wrong is worse than leaving it empty: a
banner link shared by every post in a channel collapses the whole channel to
one row, because the url is a dedup key.
"""

import pytest

from core.sources.apply_link import BOARD, EMPLOYER, classify, pick, vacancy_key


class TestClassify:
    @pytest.mark.parametrize(
        "href,kind",
        [
            ("https://acme.ru/careers/senior-python-46728", EMPLOYER),
            ("https://acme.ru/jobs/vacancies/884412", EMPLOYER),
            ("https://hh.ru/vacancy/12345678", BOARD),
            ("https://career.habr.com/vacancies/1000123456", BOARD),
            ("https://linkedin.com/jobs/view/12345", BOARD),
            # An identifying query parameter makes a bare path a page.
            ("https://boards.acme.ru/?gh_jid=778", EMPLOYER),
        ],
    )
    def test_accepts_a_vacancy_page(self, href, kind):
        assert classify(href) == kind

    @pytest.mark.parametrize(
        "href",
        [
            # The channel's own promo block: never an apply link.
            "https://t.me/somechannel",
            "https://telegram.me/somechannel",
            # Target unverifiable without following the redirect, which a
            # collector must not do.
            "https://clc.to/abc123",
            "https://bit.ly/3xYz",
            # The CHANNEL's funnel, not the employer's posting — and two
            # vacancies share one form, which would collapse them.
            "https://forms.gle/abcdef",
            # A listing root or a homepage is not one vacancy.
            "https://acme.ru/jobs",
            "https://acme.ru/",
            "https://acme.ru/en",
            # A recruiter's own profile.
            "https://linkedin.com/in/recruiter",
            # Not https.
            "http://acme.ru/jobs/dev-42",
            # Social, media, code — linked for other reasons.
            "https://github.com/acme/repo",
            "https://habr.com/ru/post/123456",
            "https://youtu.be/abc",
            "not a url at all",
        ],
    )
    def test_rejects_what_is_not_an_apply_path(self, href):
        assert classify(href) is None

    def test_a_tracking_tail_does_not_promote_a_homepage(self):
        """`/?utm_source=telegram` is what channel footers append."""
        assert classify("https://acme.ru/?utm_source=telegram") is None


class TestPick:
    def test_the_banner_never_wins_over_the_posting(self):
        """ya_jobs prefixes EVERY post with /jobs/pages/benefits.

        It comes first, satisfies the `/jobs` hint, and taking it would give an
        entire channel one url — the dedup key — collapsing it to one vacancy.
        """
        link = pick([
            "https://ya.ru/jobs/pages/benefits",
            "https://ya.ru/jobs/vacancies/12345",
        ])
        assert link.url == "https://ya.ru/jobs/vacancies/12345"

    def test_a_banner_alone_is_still_better_than_nothing(self):
        link = pick(["https://ya.ru/jobs/pages/benefits"])
        assert link.url == "https://ya.ru/jobs/pages/benefits"

    def test_the_employer_beats_a_board_mirror(self):
        link = pick(["https://hh.ru/vacancy/111", "https://acme.ru/careers/dev-999"])
        assert link.kind == EMPLOYER
        assert link.url == "https://acme.ru/careers/dev-999"

    def test_a_digest_of_several_vacancies_yields_nothing(self):
        """There is no way to split one post into the jobs it bundles, so the
        row falls back to the permalink rather than pointing at one of them."""
        assert pick([
            "https://hh.ru/vacancy/1",
            "https://hh.ru/vacancy/2",
            "https://hh.ru/vacancy/3",
        ]) is None

    def test_one_vacancy_mirrored_on_a_board_is_not_a_digest(self):
        link = pick(["https://acme.ru/careers/dev-9", "https://hh.ru/vacancy/111"])
        assert link.kind == EMPLOYER

    def test_the_same_page_with_two_tracking_tails_is_one_vacancy(self):
        link = pick([
            "https://acme.ru/jobs/dev-42?utm_source=tg",
            "https://acme.ru/jobs/dev-42",
        ])
        assert link is not None

    def test_a_post_with_no_links_has_none(self):
        assert pick([]) is None
        assert pick(["https://t.me/channel", "https://clc.to/x"]) is None


def test_vacancy_key_ignores_tracking_but_not_identity():
    a = vacancy_key("https://acme.ru/jobs/dev?utm_source=tg&gh_jid=1")
    b = vacancy_key("https://acme.ru/jobs/dev?gh_jid=1")
    c = vacancy_key("https://acme.ru/jobs/dev?gh_jid=2")
    assert a == b
    assert a != c
