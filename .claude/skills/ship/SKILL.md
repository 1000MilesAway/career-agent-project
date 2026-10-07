---
name: ship
description: Commit and deliver a change in career-agent — check, commit to main, push, confirm the deploy by revision. Use at the end of every finished task without being asked (project rule: work goes straight to main and to prod), and when the user says "push", "выкати", "задеплой".
---

# Ship to production

Every push to `main` deploys to `82.25.161.103`. Run the steps in order and
report each result; do not report "done" before step 6.

1. **Sync.** `git fetch origin` and `git status -sb`. If `main` is behind
   `origin/main`, stop and tell the user — do not rebase on your own.
2. **Check.** `scripts/quick-check.sh` — ruff, the tests of the modules that
   changed (the whole suite when a changed module has none), and frontend
   lint, typecheck and changed tests when `frontend/` changed. Red → fix,
   rerun; never push red. The full run is CI's job (step 5); `scripts/check.sh`
   runs it locally when a change reaches far.
3. **Commit** only the files of this change (`git add <paths>`, not `-A`).
   Conventional message in English (`feat(scan): …`, `fix(web): …`,
   `docs(decisions): …`), body says why. If the change made a decision, run the
   `decision` skill first so the journal and its index ship in the same commit.
4. **Push.** `git push origin main`.
5. **Wait for CI.** `gh run list -L 1 -w Deploy` for the run of this commit,
   then `gh run watch <id> --exit-status`. CI runs ruff, the whole suite,
   migrations and the frontend build; red deploys nothing. Red → read
   `gh run view <id> --log-failed`, fix, and ship the fix as a new commit
   (steps 2–6); say in the report that the first push was red.
6. **Confirm the deploy.** Poll `curl -s http://82.25.161.103:8000/api/health`
   until `revision` equals `git rev-parse --short HEAD`. A live service with
   the old revision is not a confirmation — if it never arrives, say so and
   point to the Actions run.

A frontend change is visible on https://career-helper.site only after step 6:
the bundle is built inside the `web` image.
