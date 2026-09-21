"""The starter list of Telegram channels, read from data/sources/telegram-channels.json.

We ship the list, the user edits it — the same deal as the keyword lists in
core/defaults.py. It is data, not code, because it goes stale on its own schedule:
channels die quietly (`t.me/s/` keeps serving their last page years later) and new
ones appear, and refreshing a JSON file should not mean touching a module.

Groups are listed too, under `chats_unreachable`, precisely because they CANNOT be
read. Without that list the same dead-end names get rediscovered and re-tried every
few months.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "sources" / "telegram-channels.json"


def load(path: Path | None = None) -> dict[str, Any]:
    """The whole file. Missing file is not an error: the user may not want Telegram."""
    path = path or DEFAULT_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def channel_names(
    path: Path | None = None,
    groups: tuple[str, ...] = ("core",),
) -> list[str]:
    """Channel names from the chosen groups, in file order, without repeats.

    Defaults to `core` alone: those are the ones that actually paid off for this
    profile. `adjacent` holds other stacks and is opt-in, because for a Python
    profile it is mostly free requests that return nothing.
    """
    data = load(path)
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in data.get(group) or []:
            name = item["name"] if isinstance(item, dict) else str(item)
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out
