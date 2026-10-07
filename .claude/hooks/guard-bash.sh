#!/usr/bin/env bash
# PreToolUse(Bash): rules from CLAUDE.md that are cheaper to enforce than to repeat.
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


if grep -qE '(curl|wget|http[[:space:]])' <<<"$cmd" \
  && grep -qE '82\.25\.161\.103|career-helper\.site' <<<"$cmd" \
  && grep -qiE -- '(-X|--request)[[:space:]]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-d|--data[a-z-]*|--json|-F|--form)([[:space:]=]|$)' <<<"$cmd"; then
  decide ask "Write request to the prod API: scans, evaluations, triage and chat spend real money. Confirm with the user first (CLAUDE.md, «Платные ручки»)."
fi

exit 0
