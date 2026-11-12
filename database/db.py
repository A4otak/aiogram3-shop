import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from config import config
from database.models import OrderStatus

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT    NOT NULL,
    balance    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories (id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    price       INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    is_sold     INTEGER NOT NULL DEFAULT 0,
    sold_to     INTEGER REFERENCES users (user_id),
    sold_at     TEXT,
    order_id    INTEGER,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_products_group
    ON products (category_id, name, price, is_sold);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (user_id),
    product_id INTEGER NOT NULL REFERENCES products (id),
    price      INTEGER NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,
    method     TEXT    NOT NULL,
    invoice_id INTEGER,
    status     TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id, id DESC);

CREATE TABLE IF NOT EXISTS balance_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (user_id),
    amount     INTEGER NOT NULL,
    reason     TEXT    NOT NULL,
    admin_id   INTEGER,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (user_id),
    amount     INTEGER NOT NULL,
    invoice_id INTEGER,
    status     TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


@asynccontextmanager
async def transaction() -> AsyncIterator[aiosqlite.Connection]:
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except Exception:
            await db.rollback()
            raise
        else:
            await db.commit()


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        return {row["name"] async for row in cursor}


async def _migrate(db: aiosqlite.Connection) -> None:
    if "balance" not in await _columns(db, "users"):
        await db.execute("ALTER TABLE users ADD COLUMN balance INTEGER NOT NULL DEFAULT 0")
        logger.info("Миграция: в users добавлена колонка balance")

    if "quantity" not in await _columns(db, "orders"):
        await db.execute("ALTER TABLE orders ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")
        logger.info("Миграция: в orders добавлена колонка quantity")

    if "order_id" not in await _columns(db, "products"):
        await db.execute("ALTER TABLE products ADD COLUMN order_id INTEGER")
        # В старых заказах product_id указывал на единственную проданную строку.
        await db.execute(
            "UPDATE products SET order_id = ("
            "    SELECT o.id FROM orders o WHERE o.product_id = products.id AND o.status = ?"
            ") WHERE is_sold = 1 AND order_id IS NULL",
            (OrderStatus.PAID,),
        )
        logger.info("Миграция: в products добавлена колонка order_id")

    await db.execute("CREATE INDEX IF NOT EXISTS idx_products_order ON products (order_id)")


async def init_db() -> None:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    async with connect() as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(SCHEMA)
        await _migrate(db)
        await db.commit()
    logger.info("База данных готова: %s", config.db_path)
