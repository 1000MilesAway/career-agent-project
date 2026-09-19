"""The admin CLI — the only way an account comes into existence.

The email check here is not cosmetic. It was added after `user-add
max@career.local` created a row on the deployed server that could never log
in: `.local` is a reserved domain, the CLI accepted it, and the API's EmailStr
refused it at login. An account nobody can authenticate as is worse than a
refusal, because it looks like it worked.
"""

from __future__ import annotations

from app.cli import _generate_password, _valid_email


class TestEmailValidation:
    def test_an_ordinary_address_passes(self):
        assert _valid_email("max@example.com") == "max@example.com"

    def test_a_reserved_domain_is_refused(self):
        """`.local` is reserved for mDNS; login would reject it."""
        assert _valid_email("max@career.local") is None

    def test_an_address_without_a_domain_is_refused(self):
        assert _valid_email("max") is None

    def test_an_address_without_a_user_is_refused(self):
        assert _valid_email("@example.com") is None

    def test_the_cli_agrees_with_what_login_accepts(self):
        """Both sides must use the same rule, or accounts become unusable.

        Checks the actual validator the API applies rather than a copy of its
        rules, so a pydantic upgrade that changes them cannot split the two.
        """
        from pydantic import EmailStr, TypeAdapter, ValidationError

        adapter = TypeAdapter(EmailStr)
        for address in ("ok@example.com", "bad@career.local", "nope"):
            try:
                adapter.validate_python(address)
                login_accepts = True
            except ValidationError:
                login_accepts = False
            assert (_valid_email(address) is not None) == login_accepts


class TestGeneratedPasswords:
    def test_two_passwords_differ(self):
        assert _generate_password() != _generate_password()

    def test_long_enough_to_be_hopeless_to_guess(self):
        """~62 bits, still short enough to dictate over the phone."""
        assert len(_generate_password()) >= 16
