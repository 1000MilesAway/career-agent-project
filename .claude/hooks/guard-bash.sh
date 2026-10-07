#!/usr/bin/env bash
# PreToolUse(Bash): rules from CLAUDE.md that are cheaper to enforce than to repeat.
#   - `git push` only after scripts/check.sh passed on the tree being pushed;
#   - the prod server is reached as max@, never root@;
#   - a write request to the prod API (scans, evaluations, chat cost money) asks first.
set -uo pipefail

cmd="$(jq -r '.tool_input.command // ""')"
repo="${CLAUDE_PROJECT_DIR:-$PWD}"

decide() {
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}'
  exit 0
}

if grep -qE 'root@82\.25\.161\.103' <<<"$cmd"; then
  decide deny "The prod server is reached as max@82.25.161.103; root has no key there (CLAUDE.md)."
fi

if grep -qE '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+push([[:space:]]|$)' <<<"$cmd"; then
  gitdir="$(git -C "$repo" rev-parse --git-dir 2>/dev/null)" || exit 0
  case "$gitdir" in /*) ;; *) gitdir="$repo/$gitdir" ;; esac
  checked="$(cat "$gitdir/claude-checked-tree" 2>/dev/null)"
  head_tree="$(git -C "$repo" rev-parse 'HEAD^{tree}' 2>/dev/null)"
  if [ -z "$checked" ] || [ "$checked" != "$head_tree" ]; then
    decide deny "Run scripts/check.sh first: the commit being pushed has not passed ruff/pytest (and frontend checks) on this exact tree."
  fi
fi

if grep -qE '(curl|wget|http[[:space:]])' <<<"$cmd" \
  && grep -qE '82\.25\.161\.103|career-helper\.site' <<<"$cmd" \
  && grep -qiE -- '(-X|--request)[[:space:]]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-d|--data[a-z-]*|--json|-F|--form)([[:space:]=]|$)' <<<"$cmd"; then
  decide ask "Write request to the prod API: scans, evaluations, triage and chat spend real money. Confirm with the user first (CLAUDE.md, «Платные ручки»)."
fi

exit 0
