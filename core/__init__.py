"""Shared pipeline core: everything that decides, nothing that stores.

Imported by `app/` (the product on Postgres). The split is deliberate and the
boundary is storage: a module lives here only if it has no idea where its
input came from or where its output goes.

The boundary was drawn when a file-backed demo (`probe/`, removed 2026-09-21)
and the product ran the same pipeline over different storage. The demo is gone
and the product is the only caller, but the line stays: storage decisions live
in `app/`, and everything here must remain callable with no database in sight.
That is what keeps the filters and prompts testable without a Postgres.

The rule that keeps this honest: nothing in `core/` may import from `app/`.
"""
