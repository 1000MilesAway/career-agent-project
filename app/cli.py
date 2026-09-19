"""Admin commands. In the MVP the important one is creating a user.

There is no public registration and no email, so this is the only door into
the service:

    uv run python -m app.cli user-add you@example.com
    uv run python -m app.cli user-list
    uv run python -m app.cli user-passwd you@example.com
    uv run python -m app.cli user-off you@example.com

A password typed on the command line lands in shell history, so by default one
is generated and printed once. `--password` stays available for scripted runs.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from .core.db import SessionLocal
from .core.security import hash_password
from .models import User


def _generate_password() -> str:
    # ~62 bits: long enough that guessing is hopeless, short enough to dictate
    # over the phone, which is how a tester actually receives it.
    return secrets.token_urlsafe(12)


async def _user_add(email: str, password: str | None, is_admin: bool) -> int:
    email = email.strip().lower()
    password = password or _generate_password()

    async with SessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"Пользователь {email} уже есть", file=sys.stderr)
            return 1

        session.add(
            User(email=email, password_hash=hash_password(password), is_admin=is_admin)
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
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{u.email:<40} потрачено {float(u.spent_rub):.2f} ₽{suffix}")
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


async def _user_toggle(email: str, active: bool) -> int:
    email = email.strip().lower()
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"Нет пользователя {email}", file=sys.stderr)
            return 1
        user.is_active = active
        await session.commit()

    print(f"{email}: {'включён' if active else 'выключен'}")
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
    off.add_argument("email")

    on = sub.add_parser("user-on", help="включить доступ обратно")
    on.add_argument("email")

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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
