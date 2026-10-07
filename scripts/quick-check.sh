#!/usr/bin/env bash
# The check before `git push`: ruff, the tests that touch what changed, and the
# fast frontend checks when frontend/ changed. CI runs everything (ruff, the
# whole suite, migrations, the frontend build) before every deploy, and a red
# CI deploys nothing — so this only has to catch the obvious, quickly.
# scripts/check.sh is the whole local run, for when it is worth the minute.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Changed against origin/main: committed, staged, unstaged and untracked.
changed="$(
  {
    git diff --name-only origin/main
    git ls-files --others --exclude-standard
  } | sort -u
)"

uv run ruff check .

# Which tests to run. Anything that shapes every test runs them all.
if grep -qE '^(tests/(conftest|dbmark)\.py|pyproject\.toml|uv\.lock)$' <<<"$changed"; then
  tests="tests"
else
  tests="$(grep -E '^tests/test_.*\.py$' <<<"$changed" || true)"
  while read -r path; do
    [ -n "$path" ] || continue
    module="${path%.py}"
    stem="$(basename "$module")"
    package="$(dirname "$module" | tr / .)"
    # Imported by its dotted path or by name from its package; an API module
    # is exercised through its URL rather than imported.
    pattern="${module//\//\\.}\\b|from ${package//./\\.} import .*\\b${stem}\\b"
    [ "$package" = "app.api" ] && pattern="$pattern|/api/${stem}\\b"
    found="$(grep -lE "$pattern" tests/test_*.py || true)"
    # A module no test names (a service reached only through the API, say):
    # run them all rather than nothing for it.
    if [ -z "$found" ]; then
      echo "no test names $path: running the whole suite"
      tests="tests"
      break
    fi
    tests+=$'\n'"$found"
  done < <(grep -E '^(app|core|migrations)/.*\.py$' <<<"$changed" || true)
fi

# The journal and its index are kept in step by a test of their own.
if grep -qE '^docs/decisions(-index)?\.md$' <<<"$changed"; then
  tests+=$'\n'"tests/test_decisions_index.py"
fi

if grep -qx tests <<<"$tests"; then
  tests="tests"
else
  tests="$(sort -u <<<"$tests" | grep -v '^$' || true)"
fi
if [ -n "$tests" ]; then
  # shellcheck disable=SC2086  # one path per word
  uv run pytest -q $tests
else
  echo "no Python changes: pytest skipped"
fi

if grep -q '^frontend/' <<<"$changed"; then
  (cd frontend && npm run lint && npm run typecheck && npx vitest run --changed origin/main)
fi

echo "quick check passed"
