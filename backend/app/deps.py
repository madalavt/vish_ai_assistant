"""Shared FastAPI dependencies.

`current_user_id` is the seam for M9. Today it returns the seeded local user;
when auth lands, it reads the session instead and every route that depends on
it becomes user-scoped without changing.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import LOCAL_USER_ID


async def current_user_id() -> uuid.UUID:
    return uuid.UUID(LOCAL_USER_ID)


async def session_dep() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


CurrentUserId = Annotated[uuid.UUID, Depends(current_user_id)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
