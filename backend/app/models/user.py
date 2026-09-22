"""Users.

Auth is deferred to M9, but this table and the user_id foreign keys on every
owned table exist from M1. That makes adding login a session lookup rather
than a migration across chat, documents and workflows.
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin

# Stable id for the single local user, seeded by the initial migration.
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Null until M9 adds login; argon2 hash thereafter.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
