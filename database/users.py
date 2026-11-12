import aiosqlite

from database.db import connect, transaction
from database.models import BalanceReason, User

USER_COLUMNS = "user_id, username, full_name, balance"


async def register_user(user_id: int, username: str | None, full_name: str) -> tuple[User, bool]:
    async with connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name),
        )
        created = cursor.rowcount > 0
        if not created:
            await db.execute(
                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                (username, full_name, user_id),
            )
        await db.commit()
        async with db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return User(**dict(row)), created


async def get_user(user_id: int) -> User | None:
    async with connect() as db:
        async with db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return User(**dict(row)) if row else None


async def log_balance_change(
    db: aiosqlite.Connection,
    user_id: int,
    amount: int,
    reason: BalanceReason,
    admin_id: int | None = None,
) -> None:
    await db.execute(
        "INSERT INTO balance_log (user_id, amount, reason, admin_id) VALUES (?, ?, ?, ?)",
        (user_id, amount, reason, admin_id),
    )


async def change_balance(
    user_id: int,
    delta: int,
    reason: BalanceReason,
    admin_id: int | None = None,
) -> int | None:
    async with transaction() as db:
        cursor = await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ? AND balance + ? >= 0",
            (delta, user_id, delta),
        )
        if cursor.rowcount == 0:
            return None
        await log_balance_change(db, user_id, delta, reason, admin_id)
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row["balance"]
