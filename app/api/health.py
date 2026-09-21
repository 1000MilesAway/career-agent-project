"""Liveness. No auth: a monitor should not need an account."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from ..core.config import get_settings
from ..core.deps import SessionDep

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(session: SessionDep) -> dict:
    """Report whether the database answers, not just whether the process is up.

    A pod that serves 200 while its database is unreachable is worse than one
    that is plainly down: the load balancer keeps sending it traffic.
    """
    try:
        await session.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False

    settings = get_settings()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "model": settings.deepseek_model,
        # So a deploy can be confirmed from outside. Without it the only proof
        # that a push reached the server is that nothing broke — which is also
        # what a deploy that never ran looks like.
        "revision": settings.git_sha,
    }
