"""Every entry of the decision journal has a line in its index.

Agents read docs/decisions-index.md instead of the 3000-line journal. An
entry missing from the index is invisible to them, so a decision appended
without its index line fails here rather than being quietly reopened later.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

# "## 2026-09-21 (дополнение) — Title" → "Title"
HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}[^—\n]*— (.+?)\s*$", re.MULTILINE)


def test_every_journal_entry_is_indexed():
    journal = (DOCS / "decisions.md").read_text(encoding="utf-8")
    index = (DOCS / "decisions-index.md").read_text(encoding="utf-8")

    titles = HEADING.findall(journal)
    assert len(titles) > 100, "journal headings no longer match the expected format"

    missing = [t for t in titles if t not in index]
    assert not missing, "add these to docs/decisions-index.md:\n" + "\n".join(missing)
