"""Outgoing email, through Resend's HTTP API.

httpx rather than Resend's SDK: sending is one POST, and the SDK would be a
dependency for a single call. The free plan allows 100 letters a day and
3,000 a month, which is what the per-account resend throttle in api/auth.py
is protecting.
"""

from __future__ import annotations

import html
import logging

import httpx

from ..core.config import get_settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


class MailUnavailable(Exception):
    """The letter did not leave: Resend refused, timed out, or is not configured."""


def mail_enabled() -> bool:
    settings = get_settings()
    return bool(settings.resend_api_key and settings.mail_from)


async def send_email(to: str, subject: str, text: str, html_body: str) -> None:
    settings = get_settings()
    if not mail_enabled():
        raise MailUnavailable("mail is not configured")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.mail_from,
                    "to": [to],
                    "subject": subject,
                    "text": text,
                    "html": html_body,
                },
            )
    except httpx.HTTPError as exc:
        raise MailUnavailable(str(exc)) from exc
    if response.status_code >= 400:
        # The body names the reason (unverified domain, daily quota); the user
        # gets a generic message, the log gets this.
        log.warning("resend refused a letter: %s %s", response.status_code, response.text[:500])
        raise MailUnavailable(f"resend answered {response.status_code}")


def verification_letter(link: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML of the address confirmation letter."""
    subject = "Подтвердите почту для career-agent"
    text = (
        "Здравствуйте!\n\n"
        "Чтобы закончить регистрацию в career-agent, откройте ссылку:\n"
        f"{link}\n\n"
        "Ссылка действует сутки. Если вы не регистрировались, просто "
        "проигнорируйте это письмо — без подтверждения учётка не заработает.\n"
    )
    safe = html.escape(link, quote=True)
    html_body = (
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#1a1a1a">'
        "<p>Здравствуйте!</p>"
        "<p>Чтобы закончить регистрацию в career-agent, подтвердите почту:</p>"
        f'<p><a href="{safe}" style="display:inline-block;padding:10px 18px;'
        'background:#1a1a1a;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Подтвердить почту</a></p>"
        f'<p style="color:#666;font-size:13px">Или скопируйте ссылку: {safe}</p>'
        '<p style="color:#666;font-size:13px">Ссылка действует сутки. Если вы не '
        "регистрировались, просто проигнорируйте это письмо — без подтверждения "
        "учётка не заработает.</p>"
        "</div>"
    )
    return subject, text, html_body
