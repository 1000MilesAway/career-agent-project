---
name: ship
description: Commit and deliver a change in career-agent — check, commit to main, push, confirm the deploy by revision. Use when work is done and should reach production, or the user says "push", "выкати", "задеплой".
---

# Ship to production

Every push to `main` deploys to `82.25.161.103`. Run the steps in order and
report each result; do not report "done" before step 5.

1. **Sync.** `git fetch origin` and `git status -sb`. If `main` is behind
   `origin/main`, stop and tell the user — do not rebase on your own.
2. **Check.** `scripts/check.sh` — ruff, pytest, and the frontend checks when
   `frontend/` changed. It records the checked tree; the PreToolUse hook refuses
   `git push` for any other tree. Red → fix, rerun; never push around it.
3. **Commit** only the files of this change (`git add <paths>`, not `-A`).
   Conventional message in English (`feat(scan): …`, `fix(web): …`,
   `docs(decisions): …`), body says why. If the change made a decision, run the
   `decision` skill first so the journal and its index ship in the same commit.
   If you commit after step 2 with different content, run step 2 again.
4. **Push.** `git push origin main`.
5. **Confirm the deploy.** Poll `curl -s http://82.25.161.103:8000/api/health`
   until `revision` equals `git rev-parse --short HEAD` (about 2 minutes; give up
   after 6). A live service with the old revision is not a confirmation — if it
   never arrives, say so and point to the Actions run.

A frontend change is visible on https://career-helper.site only after step 5:
the bundle is built inside the `web` image.
