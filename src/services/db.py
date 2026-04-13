# src/services/db.py

import aiosqlite
import asyncio
from pathlib import Path

_db: aiosqlite.Connection | None = None

async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db

async def init_db(path: str) -> None:
    global _db
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads
    await _db.execute("PRAGMA foreign_keys=ON")    # enforce ON DELETE CASCADE
    await _apply_migrations(_db)

async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None

async def _apply_migrations(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
    """)
    async with db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
        row = await cur.fetchone()
        current_version = row[0]

    migrations = _get_migrations()
    for version, sql in migrations:
        if version > current_version:
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            await db.commit()

def _get_migrations() -> list[tuple[int, str]]:
    return [
        (1, """
            CREATE TABLE streaks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                name          TEXT NOT NULL,
                slug          TEXT NOT NULL,
                description   TEXT,
                schedule      TEXT NOT NULL DEFAULT 'daily',
                freeze_tokens INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                UNIQUE(user_id, slug)
            );

            CREATE TABLE streak_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                streak_id  INTEGER NOT NULL REFERENCES streaks(id) ON DELETE CASCADE,
                logged_at  TEXT NOT NULL,
                note       TEXT,
                mood       INTEGER CHECK(mood IS NULL OR (mood BETWEEN 1 AND 5)),
                tags       TEXT
            );

            CREATE TABLE streak_freezes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                streak_id  INTEGER NOT NULL REFERENCES streaks(id) ON DELETE CASCADE,
                frozen_for TEXT NOT NULL
            );

            CREATE INDEX idx_streak_logs_streak_id ON streak_logs(streak_id);
            CREATE INDEX idx_streak_logs_logged_at ON streak_logs(logged_at);
        """),
    ]