#!/usr/bin/env bash
# The check that must pass before `git push`: ruff and pytest, plus the
# frontend checks when frontend/ differs from origin/main.
#
# On success it records the tree it checked. The PreToolUse hook
# (.claude/hooks/guard-bash.sh) lets `git push` through only when the pushed
# commit has exactly that tree, so "tests before push" is enforced rather than
# remembered. The tree is taken from the working copy, so it does not matter
# whether the check runs before or after the commit.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
gitdir="$(git rev-parse --git-dir)"

# Tree of the working copy as it would be committed (respects .gitignore),
# built in a throwaway index so the real staging area is untouched.
tmp_index="$(mktemp)"
trap 'rm -f "$tmp_index"' EXIT
cp "$gitdir/index" "$tmp_index"
GIT_INDEX_FILE="$tmp_index" git add -A
tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"

uv run ruff check .
uv run pytest -q

if ! git diff --quiet origin/main -- frontend/ || [ -n "$(git status --porcelain -- frontend/)" ]; then
  (cd frontend && npm run lint && npm run typecheck && npm test && npm run build)
fi

echo "$tree" > "$gitdir/claude-checked-tree"
echo "check passed, tree $tree recorded"
