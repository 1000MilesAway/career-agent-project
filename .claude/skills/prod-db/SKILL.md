---
name: prod-db
description: Read-only look at the career-agent production database (counts, a user's rows, job queue state) over ssh. Use for diagnosing what happened on prod. Never for writes.
---

# Read the production database

Only `SELECT`. Any `INSERT/UPDATE/DELETE/ALTER`, migration or restart is a
separate request that needs the user's explicit consent and a backup first —
the tables hold paid evaluations.

```bash
ssh -o BatchMode=yes max@82.25.161.103 \
  'docker exec career-agent-db-1 psql -U career -d career -P pager=off -c "<SELECT …>"'
```

- User is `max`, not `root`. `Permission denied (publickey)` means the wrong
  user; a block from the permission classifier is not a server answer.
- Wrap the query in `BEGIN READ ONLY; … ROLLBACK;` when it is more than a
  plain `SELECT`, so a mistake cannot write.
- Always `LIMIT`. Do not print `raw_text`, resumes, e-mails or chat messages
  into the conversation — count or select ids and short fields instead.
- Schema: `app/models.py`. Shared vacancies are `postings`; per-user rows are
  `vacancies`; money is in `llm_calls`, `evaluations.cost_rub`, `users.spent_rub`.
