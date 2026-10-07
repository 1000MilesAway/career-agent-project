"""The stored portrait, as `core.photo` sees it."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.photo import Photo

from ..models import UserPhoto


async def load(session: AsyncSession, user_id: uuid.UUID) -> Photo | None:
    row = await session.get(UserPhoto, user_id)
    return Photo(data=row.data, media_type=row.media_type) if row else None


async def has_photo(session: AsyncSession, user_id: uuid.UUID) -> bool:
    # Only the key: /api/state asks on every refresh and must not drag the
    # picture's bytes along each time.
    found = await session.scalar(select(UserPhoto.user_id).where(UserPhoto.user_id == user_id))
    return found is not None
