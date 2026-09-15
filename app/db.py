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
    if "canonical_url" not in cols:
        alters.append("ALTER TABLE products ADD COLUMN canonical_url TEXT")
    for stmt in alters:
        logger.info("SQLite migrate: %s", stmt)
        await conn.execute(text(stmt))

    # Backfill canonical_url from url where empty
    try:
        await conn.execute(
            text(
                "UPDATE products SET canonical_url = url "
                "WHERE (canonical_url IS NULL OR canonical_url = '') "
                "AND url IS NOT NULL AND url != ''"
            )
        )
    except Exception as e:
        logger.warning("canonical_url backfill skipped: %s", e)

    # Drop legacy rigid unique if present (table-level uq_owner_url from older SQLAlchemy)
    # SQLite cannot DROP CONSTRAINT easily; we rely on partial indexes going forward.
    # Remove old unique index name if it blocks sku-based duplicates of cleaned URLs.
    for idx in ("uq_owner_url", "sqlite_autoindex_products_1"):
        try:
            await conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
        except Exception as e:
            logger.debug("drop index %s: %s", idx, e)

    # Unique: (owner, platform, sku) when sku known
    try:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_platform_sku "
                "ON products(owner_openid, platform, sku_id) "
                "WHERE owner_openid IS NOT NULL AND sku_id IS NOT NULL AND sku_id != ''"
            )
        )
    except Exception as e:
        logger.warning("Could not create uq_owner_platform_sku: %s", e)

    # Unique: (owner, url) when sku unknown
    try:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_url_nosku "
                "ON products(owner_openid, url) "
                "WHERE owner_openid IS NOT NULL "
                "AND (sku_id IS NULL OR sku_id = '') "
                "AND url != ''"
            )
        )
    except Exception as e:
        logger.warning("Could not create uq_owner_url_nosku: %s", e)

    # Also keep a non-unique helpful index on canonical_url
    try:
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_canonical_url "
                "ON products(canonical_url)"
            )
        )
    except Exception as e:
        logger.warning("Could not create canonical_url index: %s", e)

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
