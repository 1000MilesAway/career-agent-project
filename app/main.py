"""The FastAPI application.

JSON only — the SPA is a separate build, per docs/decisions.md 2026-09-12.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import artifacts, auth, evaluations, health, jobs, resumes, state, vacancies
from .core.config import get_settings

STATIC = Path(__file__).resolve().parent / "static"

settings = get_settings()

if not settings.debug and settings.secret_key == "change-me":
    # A predictable signing key lets anyone mint a token for any account, so
    # this is a refusal to start rather than a warning nobody reads.
    raise RuntimeError(
        "SECRET_KEY не задан. Сгенерируйте ключ и положите его в .env — "
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

app = FastAPI(
    title="career-agent",
    description="Оценка вакансий по резюме. Отклик отправляет человек.",
    version="0.1.0",
)

# The SPA is served from a different origin in development (Vite on 5173).
# Credentials stay off: the token travels in the Authorization header, not a
# cookie, so there is nothing for a cross-site request to replay.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    health.router,
    auth.router,
    state.router,
    resumes.router,
    vacancies.router,
    evaluations.router,
    artifacts.router,
    jobs.router,
):
    app.include_router(router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """The cabinet itself.

    Served by the API rather than by a separate nginx: there is one page and
    no build step, and a second container to hand over one file would be
    infrastructure with nothing to do. When the React SPA replaces this, it
    gets its own static serving.

    Read from disk on each request, as the demo did — editing the page and
    pressing F5 should be enough to see the change.
    """
    return FileResponse(STATIC / "index.html")
