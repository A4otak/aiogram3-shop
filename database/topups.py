from database.db import connect, transaction
from database.models import BalanceReason, Topup, TopupStatus
from database.users import log_balance_change

TOPUP_COLUMNS = "id, user_id, amount, invoice_id, status"


async def create_topup(user_id: int, amount: int) -> int:
    async with connect() as db:
        cursor = await db.execute(
            "INSERT INTO topups (user_id, amount, status) VALUES (?, ?, ?)",
            (user_id, amount, TopupStatus.PENDING),
        )
        await db.commit()
        return cursor.lastrowid


async def attach_invoice(topup_id: int, invoice_id: int) -> None:
    async with connect() as db:
        await db.execute("UPDATE topups SET invoice_id = ? WHERE id = ?", (invoice_id, topup_id))
        await db.commit()


async def cancel_topup(topup_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE topups SET status = ? WHERE id = ? AND status = ?",
            (TopupStatus.CANCELLED, topup_id, TopupStatus.PENDING),
        )
        await db.commit()


async def get_topup(topup_id: int) -> Topup | None:
    async with connect() as db:
        async with db.execute(f"SELECT {TOPUP_COLUMNS} FROM topups WHERE id = ?", (topup_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return Topup(**{**dict(row), "status": TopupStatus(row["status"])})


async def fulfill_topup(topup_id: int) -> int | None:
    async with transaction() as db:
        cursor = await db.execute(
            "UPDATE topups SET status = ?, paid_at = CURRENT_TIMESTAMP WHERE id = ? AND status = ?",
            (TopupStatus.PAID, topup_id, TopupStatus.PENDING),
        )
        if cursor.rowcount == 0:
            return None

        async with db.execute("SELECT user_id, amount FROM topups WHERE id = ?", (topup_id,)) as cursor:
            topup = await cursor.fetchone()
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (topup["amount"], topup["user_id"]),
        )
        await log_balance_change(db, topup["user_id"], topup["amount"], BalanceReason.TOPUP)
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (topup["user_id"],)) as cursor:
            row = await cursor.fetchone()
    return row["balance"]
