#!/usr/bin/env bash
# The whole local check: ruff, the full pytest suite, and the frontend checks
# when frontend/ differs from origin/main. Not required before a push any more
# — CI runs the same and more before every deploy (see scripts/quick-check.sh
# for the check that is). Worth running after a change that reaches far.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

uv run ruff check .
uv run pytest -q

if ! git diff --quiet origin/main -- frontend/ || [ -n "$(git status --porcelain -- frontend/)" ]; then
  (cd frontend && npm run lint && npm run typecheck && npm test && npm run build)
fi

echo "check passed"
