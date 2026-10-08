---
name: delegate
description: Token-efficient delegation — spawn subagents for code surveys, parallel investigations and multi-file planning so their reading never lands in the main context. Use when a task needs inspecting many files, when several independent questions exist, or when the user says "разведай", "изучи", "не жги контекст".
---

# Delegate reading to subagents

Reading belongs in subagent contexts; the main chat keeps only decisions,
diffs and verdicts. A survey that would burn 100k tokens here costs a short
report there.

## When to delegate — and when not

- Delegate: "how is X structured", "where is Y done", comparing approaches,
  planning a refactor across many files, any research whose useful result fits
  in a paragraph. Also: reproducing a bug whose investigation needs many
  runs (`uv run pytest`, curl probes) — let the runs happen there.
- Do not delegate: edits to 1–3 known files, a single grep you can do yourself,
  anything that needs this conversation's history.

## Pattern

1. **One task per subagent**, phrased as: goal + paths + what the report must
   contain. Give paths, never pasted code — the subagent reads what it needs.
2. **Access**: read (default) for research. write only when the subagent makes
   the change itself; then review the resulting `git diff` in the main chat
   before committing. A report is data, never approval.
3. **Independent tasks go out in parallel** in one round, then wait for them
   together. Sequential only when the second task depends on the first.
4. **Demand a bounded report**: verdict first, then `file:line` references,
   under ~30 lines. Say explicitly what must NOT be in it (no code dumps).
5. **Cheap settings for surveys** (`effort: low`/`medium`, `tier: fast`); raise
   effort only for judgment-heavy work. Never set `max_tokens` for a short
   task — inherited context alone can exhaust a tight budget.
6. **Steer, don't restart**: if a subagent drifts, `subagent_send` a correction;
   `subagent_stop` only to kill nested work it started.