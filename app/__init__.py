"""The product: FastAPI over Postgres, multi-user, behind a login.

The pipeline itself is not here — it lives in `core/`, which knows nothing
about storage. What this package adds is everything around it: users, a
database, a job queue, and an HTTP surface for a browser.
"""
