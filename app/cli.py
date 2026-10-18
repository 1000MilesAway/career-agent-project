"""Admin commands. In the MVP the important one is creating a user.

There is no public registration and no email, so this is the only door into
the service:

    uv run python -m app.cli user-add you@example.com
    uv run python -m app.cli user-list
    uv run python -m app.cli user-passwd you@example.com
    uv run python -m app.cli user-off you@example.com

A password typed on the command line lands in shell history, so by default one
is generated and printed once. `--password` stays available for scripted runs.

Also here, because it has nowhere better to live:

    uv run python -m app.cli calibrate                 # judge stored reports
    uv run python -m app.cli calibrate --source golden # replay labeled cases

Calibration answers "does the scoring still obey its own rules" without calling
the model, so it costs nothing and needs no key.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import UTC, datetime, timedelta

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from core import calibrate

from .core.db import SessionLocal
from .core.security import hash_password
from .models import Evaluation, Posting, User, Vacancy


def _generate_password() -> str:
    # ~62 bits: long enough that guessing is hopeless, short enough to dictate
    # over the phone, which is how a tester actually receives it.
    return secrets.token_urlsafe(12)


def _valid_email(email: str) -> str | None:
    """The address, or None if login would reject it.

    Checked with the same validator the API uses, because the CLI is the only
    way an account is created: without this, `user-add max@career.local`
    succeeds and the account can never log in — `.local` is a reserved domain
    that EmailStr refuses. A row nobody can authenticate as is worse than a
    refusal, because it looks like it worked.
    """
    try:
        return str(TypeAdapter(EmailStr).validate_python(email))
    except ValidationError:
        return None


async def _user_add(email: str, password: str | None, is_admin: bool) -> int:
    email = email.strip().lower()
    if _valid_email(email) is None:
        print(
            f"Недопустимый адрес: {email}\n"
            "Войти с ним будет нельзя. Домены вроде .local зарезервированы.",
            file=sys.stderr,
        )
        return 1
    password = password or _generate_password()

    async with SessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"Пользователь {email} уже есть", file=sys.stderr)
            return 1

        session.add(
            User(
                email=email,
                password_hash=hash_password(password),
                is_admin=is_admin,
                # The admin vouches for the address; there is no letter to click.
                email_verified_at=datetime.now(UTC),
            )
        )
        await session.commit()

    print(f"Создан: {email}")
    print(f"Пароль: {password}")
    print("Передайте пароль вне сервиса. Второй раз он не покажется.")
    return 0


async def _user_list() -> int:
    async with SessionLocal() as session:
        result = await session.execute(select(User).order_by(User.created_at))
        users = list(result.scalars())

    if not users:
        print("Пользователей нет")
        return 0

    for u in users:
        flags = []
        if u.is_admin:
            flags.append("админ")
        if not u.is_active:
            flags.append("выключен")
        if u.paid_until and u.paid_until > datetime.now(UTC):
            flags.append(f"подписка до {u.paid_until:%Y-%m-%d}")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{u.email or u.telegram_name or u.id!s:<40} потрачено {float(u.spent_rub):.2f} ₽{suffix}")
    return 0


async def _user_passwd(email: str, password: str | None) -> int:
    email = email.strip().lower()
    password = password or _generate_password()

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"Нет пользователя {email}", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        await session.commit()

    print(f"Пароль изменён: {email}")
    print(f"Новый пароль: {password}")
    # Tokens keep working: they carry only the user id, and there is no token
    # store to revoke against. To cut someone off now, use `user-off`.
    print("Выданные токены продолжат работать. Чтобы закрыть доступ сразу — user-off.")
    return 0


async def _find_user(session, who: str) -> User | None:
    """`who` is an email, or the "@handle" user-list shows for a Telegram sign-up."""
    column = User.telegram_name if who.startswith("@") else User.email
    key = who if who.startswith("@") else who.lower()
    result = await session.execute(select(User).where(column == key))
    users = list(result.scalars())
    if not users:
        print(f"Нет пользователя {who}", file=sys.stderr)
        return None
    # Handles are Telegram's, not ours, and a stale one can repeat.
    if len(users) > 1:
        print(f"{who}: несколько учёток, уточните через email", file=sys.stderr)
        return None
    return users[0]


async def _user_toggle(who: str, active: bool) -> int:
    who = who.strip()
    async with SessionLocal() as session:
        user = await _find_user(session, who)
        if user is None:
            return 1
        user.is_active = active
        await session.commit()

    print(f"{who}: {'включён' if active else 'выключен'}")
    return 0


async def _user_plan(who: str, days: int | None) -> int:
    """Grant the paid plan for `days`, or end it with None.

    Days add to a subscription still running rather than restarting it, so a
    referral bonus or a renewal paid early is not lost. There is no payment in
    the product yet: this is how a subscription is given by hand.
    """
    who = who.strip()
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        user = await _find_user(session, who)
        if user is None:
            return 1
        if days is None:
            user.paid_until = None
        else:
            start = user.paid_until if user.paid_until and user.paid_until > now else now
            user.paid_until = start + timedelta(days=days)
        await session.commit()
        until = user.paid_until

    if until is None:
        print(f"{who}: бесплатный тариф")
    else:
        print(f"{who}: подписка до {until:%Y-%m-%d %H:%M} UTC")
    return 0


def _schema_of(report: dict) -> str:
    """Which rubric produced this report, judged from the report itself.

    The table has no `schema` column: everything in Postgres was written by the
    current prompt, which grades requirements on five bands. A v1 report used a
    binary must/nice, and judging one by the current bands reports the old
    rubric as a defect on every row — 43 findings on six reports, when tried.
    Nothing guarantees an old export never reaches this table, so the rubric is
    detected rather than assumed.
    """
    weights = {r.get("weight") for r in report.get("requirements") or []}
    return "v1-must-nice" if weights & {"must", "nice"} else ""


async def _stored_rows(email: str | None) -> list[dict]:
    """Paid reports out of the database, shaped the way calibrate expects."""
    async with SessionLocal() as session:
        # An evaluation carries no user of its own — it reaches one through the
        # vacancy it judged, which is where ownership lives.
        query = (
            select(Evaluation.report, Posting.title, User.email)
            .join(Vacancy, Evaluation.vacancy_id == Vacancy.id)
            .join(Posting, Vacancy.posting_id == Posting.id)
            .join(User, Vacancy.user_id == User.id)
            .order_by(Evaluation.created_at)
        )
        if email:
            query = query.where(User.email == email.strip().lower())
        result = await session.execute(query)
        return [
            {"title": f"{title} ({owner})", "report": report, "schema": _schema_of(report)}
            for report, title, owner in result.all()
        ]


def _print_result(result: calibrate.Result) -> int:
    print(f"Разброс баллов: {result.spread} (порог {calibrate.MIN_SPREAD})")
    if result.labeled:
        print(f"Совпадение с эталоном: {result.agreed}/{result.labeled}")

    if result.ok:
        print("\nПравила соблюдаются.")
        return 0

    print(f"\nНарушений: {len(result.findings)}")
    for f in result.findings:
        print(f"  [{f.kind}] {f.case}")
        print(f"      {f.detail}")
    # Non-zero on purpose: this is meant to be usable as a CI gate, the way
    # career-ops gates on eval-golden.mjs.
    return 1


async def _calibrate(source: str, email: str | None) -> int:
    if source == "golden":
        try:
            cases = calibrate.load_golden()
        except ValueError as e:
            print(f"Ошибка в золотом наборе: {e}", file=sys.stderr)
            return 1
        if not cases:
            print(
                f"Золотой набор пуст: положите размеченные случаи в {calibrate.GOLDEN_DIR}",
                file=sys.stderr,
            )
            return 1
        result = calibrate.from_golden(cases)
        print(f"Золотой набор: {len(cases)} случаев")
    else:
        rows = await _stored_rows(email)
        result = calibrate.from_stored(rows)
        print(f"Сохранённые отчёты: {result.checked}")

    if not result.checked:
        print("Нечего проверять — сначала оцените хотя бы одну вакансию.", file=sys.stderr)
        return 1

    return _print_result(result)


async def _discover_channels(limit: int) -> int:
    from .services import channel_discovery

    async with SessionLocal() as session:
        result = await channel_discovery.run(session, limit=limit)
        await session.commit()
    print(", ".join(f"{k}: {v}" for k, v in result.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Администрирование career-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("user-add", help="завести пользователя")
    add.add_argument("email")
    add.add_argument("--password", help="задать пароль вручную (иначе сгенерируется)")
    add.add_argument("--admin", action="store_true", help="права администратора")

    sub.add_parser("user-list", help="список пользователей")

    passwd = sub.add_parser("user-passwd", help="сменить пароль")
    passwd.add_argument("email")
    passwd.add_argument("--password")

    off = sub.add_parser("user-off", help="выключить доступ")
    off.add_argument("email", help="email или @handle из user-list")

    on = sub.add_parser("user-on", help="включить доступ обратно")
    on.add_argument("email", help="email или @handle из user-list")

    plan = sub.add_parser("user-plan", help="выдать или снять подписку")
    plan.add_argument("email", help="email или @handle из user-list")
    group = plan.add_mutually_exclusive_group(required=True)
    group.add_argument("--days", type=int, help="продлить подписку на столько дней")
    group.add_argument("--free", action="store_true", help="вернуть на бесплатный тариф")

    cal = sub.add_parser("calibrate", help="соблюдает ли скоринг свои правила (бесплатно)")
    cal.add_argument(
        "--source", choices=("stored", "golden"), default="stored",
        help="stored — оценки из БД (по умолчанию), golden — размеченный набор из data/golden",
    )
    cal.add_argument("--email", help="только оценки этого пользователя (для --source stored)")

    disc = sub.add_parser(
        "discover-channels", help="найти новые Telegram-каналы с вакансиями (добавляются скрытыми)"
    )
    disc.add_argument("--limit", type=int, default=60, help="сколько превью прочитать")

    args = parser.parse_args(argv)

    if args.command == "user-add":
        return asyncio.run(_user_add(args.email, args.password, args.admin))
    if args.command == "user-list":
        return asyncio.run(_user_list())
    if args.command == "user-passwd":
        return asyncio.run(_user_passwd(args.email, args.password))
    if args.command == "user-off":
        return asyncio.run(_user_toggle(args.email, active=False))
    if args.command == "user-on":
        return asyncio.run(_user_toggle(args.email, active=True))
    if args.command == "user-plan":
        return asyncio.run(_user_plan(args.email, None if args.free else args.days))
    if args.command == "discover-channels":
        return asyncio.run(_discover_channels(args.limit))
    if args.command == "calibrate":
        # The golden set is files only, so it must stay runnable with no
        # database at all — that is what makes it usable in CI.
        if args.source == "golden":
            return asyncio.run(_calibrate("golden", None))
        return asyncio.run(_calibrate("stored", args.email))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
