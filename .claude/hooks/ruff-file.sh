#!/usr/bin/env bash
# PostToolUse(Edit|Write): lint the edited Python file right away, so a broken
# import or unused name surfaces in the same turn instead of at scripts/check.sh.
# Safe fixes are applied; what remains is reported back to Claude (exit 2).
set -uo pipefail

f="$(jq -r '.tool_response.filePath // .tool_input.file_path // ""')"
case "$f" in *.py) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$PWD}" || exit 0
if ! out="$(uv run --quiet ruff check --fix --quiet "$f" 2>&1)"; then
  printf 'ruff on %s:\n%s\n' "$f" "$out" >&2
  exit 2
fi
exit 0
