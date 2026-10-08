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


# The target is a prod URL, or localhost inside an ssh session to prod. A bare host
# mention is not enough: `ssh max@82.25.161.103 '... curl api.telegram.org ... cut -d='`
# used to trip it.
prod_url='https?://([a-z0-9-]+\.)*(gethire\.ru|82\.25\.161\.103)([:/"'"'"'[:space:]]|$)'
local_url='(https?://)?(localhost|127\.0\.0\.1)(:[0-9]+)?([/"'"'"'[:space:]]|$)'
# -d needs a separator curl accepts (space, quote, @); `cut -d=` and the like are not bodies.
write_flag='(-X|--request)[[:space:]]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-d[[:space:]"'"'"'@]|-F[[:space:]"'"'"'@]|--data[a-z-]*[[:space:]=]|--json[[:space:]=]|--form[[:space:]=])'

if grep -qE '(curl|wget|http[[:space:]])' <<<"$cmd" \
  && { grep -qiE "$prod_url" <<<"$cmd" \
    || { grep -qE 'ssh[[:space:]].*82\.25\.161\.103' <<<"$cmd" && grep -qiE "$local_url" <<<"$cmd"; }; } \
  && grep -qiE -- "$write_flag" <<<"$cmd"; then
  decide ask "Write request to the prod API: scans, evaluations, triage and chat spend real money. Confirm with the user first (CLAUDE.md, «Платные ручки»)."
fi

exit 0
