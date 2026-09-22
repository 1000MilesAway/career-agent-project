"""The FastAPI application.

JSON only — the SPA is a separate build, per docs/decisions.md 2026-09-12.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    artifacts,
    auth,
    evaluations,
    export,
    health,
    jobs,
    resumes,
    state,
    vacancies,
)
from .core.config import get_settings

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
    export.router,
    jobs.router,
):
    app.include_router(router)


@app.get("/", include_in_schema=False)
async def index() -> dict[str, str]:
    """JSON only.

    The cabinet is a React build served by nginx in its own container (see
    `frontend/`), which also proxies /api here. This endpoint stays so that
    hitting the API's port in a browser says where the cabinet is, rather
    than returning a bare 404 that reads like a broken deploy.
    """
    return {
        "service": "career-agent API",
        "docs": "/docs",
        "health": "/api/health",
        "cabinet": "порт 80 этого же хоста",
    }
