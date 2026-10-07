---
name: decision
description: Record a project decision in career-agent — append to docs/decisions.md and add its line to docs/decisions-index.md. Use when a fork was chosen or rejected for a reason worth keeping, when the user decides something about the product, or when a change deliberately departs from career-ops or from a rule in CLAUDE.md.
---

# Record a decision

The journal is append-only and long; agents read only the index. A decision
missing from the index is invisible, and `tests/test_decisions_index.py` fails.

1. **Append** to the end of `docs/decisions.md` (never rewrite or reorder):

   ```markdown
   ## YYYY-MM-DD — Short title in Russian, unique in the journal

   Why the question came up (symptom, request, measurement).

   **Как теперь.** What was decided, with names of modules, fields, endpoints.

   Checked forks: what was tried or considered and why it was rejected —
   this is what stops the next session from reopening it.

   **Не сделано.** What is knowingly left out (only if something is).
   ```

   Facts, numbers and dates, not impressions. A measurement names when and
   on what it was taken.

2. **Index line** in `docs/decisions-index.md`, in the fitting topic:

   `- <what holds now, one line> → MM-DD <exact title from step 1>`

   The title must be copied exactly — the test matches it as a substring.

3. **If it replaces an earlier decision:** say so in the new entry
   («Отменяет … от YYYY-MM-DD»), move the old index line to «Отменено или
   заменено» with `[отменено]`, and correct any CLAUDE.md line that stated the
   old rule.

4. `uv run pytest -q tests/test_decisions_index.py`.
