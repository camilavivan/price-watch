"""SQLAlchemy async engine + session + lightweight SQLite migrations."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        url = get_config().databaseUrl
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_async_engine(url, echo=False, connect_args=connect_args)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def _migrate_sqlite(conn) -> None:
    """Add columns / indexes that create_all won't alter on existing DBs."""
    result = await conn.execute(text("PRAGMA table_info(products)"))
    cols = {row[1] for row in result.fetchall()}
    if not cols:
        return
    alters: list[str] = []
    if "owner_openid" not in cols:
        alters.append("ALTER TABLE products ADD COLUMN owner_openid VARCHAR(128)")
    if "image_url" not in cols:
        alters.append("ALTER TABLE products ADD COLUMN image_url TEXT")
    for stmt in alters:
        logger.info("SQLite migrate: %s", stmt)
        await conn.execute(text(stmt))
    # Unique index for (owner, url) — ignore if already present
    try:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_url "
                "ON products(owner_openid, url) "
                "WHERE owner_openid IS NOT NULL AND url != ''"
            )
        )
    except Exception as e:
        logger.warning("Could not create uq_owner_url index: %s", e)
    try:
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_owner_openid "
                "ON products(owner_openid)"
            )
        )
    except Exception as e:
        logger.warning("Could not create owner index: %s", e)


async def init_db() -> None:
    from app import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        url = get_config().databaseUrl
        if url.startswith("sqlite"):
            await _migrate_sqlite(conn)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
