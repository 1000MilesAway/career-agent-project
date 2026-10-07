---
name: new-source
description: Add a new vacancy source (job board, channel family, ATS) to career-agent's scan pipeline. Use when the user asks to read vacancies from a new site or service.
---

# Add a vacancy source

Read first, in `docs/decisions-index.md` → «Источники», «Пул, скан, очередь»,
«Живость вакансий»; open the full entries of the closest existing source
(geekjob is the smallest complete example).

## Before writing code

- **Legal and polite access.** Check `robots.txt` and that the data is public
  without login. No user passwords, no userbots, no personal data of recruiters
  (see the Wildberries case in the journal). Pause between requests.
- **Probe live.** Record two real responses (list + detail) into
  `tests/fixtures/` with people and contacts removed. Note what the source gives
  as fields: grade, remote, salary numbers and currency, city, description.
- Ask the user before adding a dependency.

## Wiring (follow an existing source, do not invent a new path)

1. `core/sources/<name>.py` — fetching and parsing only, no database. Returns
   `Posting`-shaped rows. A page with cards but zero parsed rows raises
   `MarkupChanged` (loud failure), never "empty today".
2. Missing data stays `None`: unknown remote, unknown salary or non-rouble
   salary must pass `PreScreen`, not be dropped.
3. `app/services/scan.py` — a `fetch_<name>` (network + free filters, runs in a
   thread) and its place in `plan()` and `ALL_SOURCES`. The order of
   `ALL_SOURCES` decides which copy becomes the original in dedup: sources with
   real fields go before reposts.
4. `app/services/pool.py` — `FRESH_FOR[<name>]`: how long a listing is served
   from the pool.
5. Descriptions that need a second request are fetched only for survivors of
   the free filters, capped (`MAX_ENRICHED`).
6. Liveness: add to `CHECKABLE_SOURCES` only if a closed vacancy has an explicit
   sign (404/410, archive banner). Otherwise it stays "unknown".
7. Lists of channels or companies are data in `data/sources/*.json`, not code.
8. Cabinet label in `SOURCE_LABELS`; dedup link pattern in `core/dedup.py` if
   other sources link to this one.

## Done means

- Tests on the recorded fixtures, no network; one test that a failing source
  does not break `collect_all`.
- One live run reported with numbers: collected → after free filters.
- A `decision` entry: what is read, what was rejected and why.
