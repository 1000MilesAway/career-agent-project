"""Nothing in core/ imports app/.

The core has to run without a database: filters and prompts are tested
without Postgres only as long as this boundary holds. CLAUDE.md states it;
this test is what keeps it.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_core_does_not_import_app():
    offenders = [
        f"{path.relative_to(CORE.parent)}: {name}"
        for path in sorted(CORE.rglob("*.py"))
        for name in imported_modules(path)
        if name == "app" or name.startswith("app.")
    ]
    assert not offenders, "core/ must not import app/:\n" + "\n".join(offenders)
