"""Shared pipeline core: everything that decides, nothing that stores.

Imported by both `probe/` (the file-backed demo) and `app/` (the product on
Postgres). The split is deliberate and the boundary is storage: a module lives
here only if it has no idea where its input came from or where its output goes.

That is why `found.py`, `session.py`, `history.py` and `paths.py` stayed in
`probe/` — they ARE the demo's storage. Their product equivalents are database
tables, not a second copy of a JSON writer.

The rule that keeps this honest: nothing in `core/` may import from `probe/`
or `app/`. A filter that runs in the demo and not in the product would make
the demo lie about the product, which is the same reason `tg_scan.py` was
shared between the CLI and the web screen in the first place.
"""
