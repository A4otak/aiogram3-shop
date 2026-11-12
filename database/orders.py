import aiosqlite

from database.db import connect, transaction
from database.models import (
    BalanceReason,
    ClaimedItem,
    Order,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
    PaymentRecord,
    PurchaseRecord,
    PurchaseResult,
    PurchaseStatus,
    Stats,
    TopupStatus,
)
from database.users import log_balance_change

ORDER_COLUMNS = "id, user_id, product_id, price, quantity, method, invoice_id, status, created_at"

PURCHASE_SELECT = """
    SELECT o.id AS order_id, ref.name AS product_name, c.name AS category_name,
           o.price, o.quantity, o.method, o.paid_at
    FROM orders o
    JOIN products ref ON ref.id = o.product_id
    JOIN categories c ON c.id = ref.category_id
    WHERE o.user_id = ? AND o.status = ?
"""

PAYMENTS_UNION = """
    SELECT 'order' AS kind, id, price AS amount, quantity, method, status, created_at
    FROM orders WHERE user_id = ?
    UNION ALL
    SELECT 'topup' AS kind, id, amount, 1 AS quantity, 'cryptobot' AS method, status, created_at
    FROM topups WHERE user_id = ?
"""


def _to_order(row: aiosqlite.Row) -> Order:
    return Order(
        id=row["id"],
        user_id=row["user_id"],
        product_id=row["product_id"],
        price=row["price"],
        quantity=row["quantity"],
        method=PaymentMethod(row["method"]),
        invoice_id=row["invoice_id"],
        status=OrderStatus(row["status"]),
        created_at=row["created_at"],
    )


def _to_purchase(row: aiosqlite.Row) -> PurchaseRecord:
    return PurchaseRecord(**{**dict(row), "method": PaymentMethod(row["method"])})


def _to_payment(row: aiosqlite.Row) -> PaymentRecord:
    return PaymentRecord(
        kind=PaymentKind(row["kind"]),
        id=row["id"],
        amount=row["amount"],
        quantity=row["quantity"],
        method=PaymentMethod(row["method"]),
        status=OrderStatus(row["status"]),
        created_at=row["created_at"],
    )


async def _select_items(db: aiosqlite.Connection, reference_id: int, quantity: int) -> list[ClaimedItem]:
    query = """
        SELECT s.id, s.name, s.content
        FROM products r
        JOIN products s
          ON s.category_id = r.category_id AND s.name = r.name AND s.price = r.price
        WHERE r.id = ? AND s.is_sold = 0
        ORDER BY s.id
        LIMIT ?
    """
    async with db.execute(query, (reference_id, quantity)) as cursor:
        return [ClaimedItem(**dict(row)) async for row in cursor]


async def _mark_sold(
    db: aiosqlite.Connection, items: list[ClaimedItem], buyer_id: int, order_id: int
) -> None:
    await db.executemany(
        "UPDATE products SET is_sold = 1, sold_to = ?, sold_at = CURRENT_TIMESTAMP, order_id = ? "
        "WHERE id = ?",
        [(buyer_id, order_id, item.id) for item in items],
    )


async def create_pending_order(user_id: int, product_id: int, price: int, quantity: int = 1) -> int:
    async with connect() as db:
        cursor = await db.execute(
            "INSERT INTO orders (user_id, product_id, price, quantity, method, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, product_id, price, quantity, PaymentMethod.CRYPTOBOT, OrderStatus.PENDING),
        )
        await db.commit()
        return cursor.lastrowid


async def attach_invoice(order_id: int, invoice_id: int) -> None:
    async with connect() as db:
        await db.execute("UPDATE orders SET invoice_id = ? WHERE id = ?", (invoice_id, order_id))
        await db.commit()


async def cancel_pending_order(order_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
            (OrderStatus.CANCELLED, order_id, OrderStatus.PENDING),
        )
        await db.commit()


async def get_order(order_id: int) -> Order | None:
    async with connect() as db:
        async with db.execute(f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
    return _to_order(row) if row else None


async def list_user_purchases(user_id: int, limit: int, offset: int = 0) -> list[PurchaseRecord]:
    query = f"{PURCHASE_SELECT} ORDER BY o.id DESC LIMIT ? OFFSET ?"
    async with connect() as db:
        async with db.execute(query, (user_id, OrderStatus.PAID, limit, offset)) as cursor:
            return [_to_purchase(row) async for row in cursor]


async def count_user_purchases(user_id: int) -> int:
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM orders WHERE user_id = ? AND status = ?",
            (user_id, OrderStatus.PAID),
        ) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def get_purchase(order_id: int, user_id: int) -> PurchaseRecord | None:
    query = f"{PURCHASE_SELECT} AND o.id = ?"
    async with connect() as db:
        async with db.execute(query, (user_id, OrderStatus.PAID, order_id)) as cursor:
            row = await cursor.fetchone()
    return _to_purchase(row) if row else None


async def get_order_contents(order_id: int, user_id: int) -> tuple[str, ...]:
    async with connect() as db:
        async with db.execute(
            "SELECT content FROM products WHERE order_id = ? AND sold_to = ? ORDER BY id",
            (order_id, user_id),
        ) as cursor:
            return tuple([row["content"] async for row in cursor])


async def get_purchase_totals(user_id: int) -> tuple[int, int]:
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(price), 0) AS spent "
            "FROM orders WHERE user_id = ? AND status = ?",
            (user_id, OrderStatus.PAID),
        ) as cursor:
            row = await cursor.fetchone()
    return row["cnt"], row["spent"]


async def list_user_payments(user_id: int, limit: int, offset: int = 0) -> list[PaymentRecord]:
    query = f"{PAYMENTS_UNION} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    async with connect() as db:
        async with db.execute(query, (user_id, user_id, limit, offset)) as cursor:
            return [_to_payment(row) async for row in cursor]


async def count_user_payments(user_id: int) -> int:
    async with connect() as db:
        async with db.execute(
            "SELECT (SELECT COUNT(*) FROM orders WHERE user_id = ?) "
            "     + (SELECT COUNT(*) FROM topups WHERE user_id = ?) AS cnt",
            (user_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def purchase_with_balance(user_id: int, reference_id: int, quantity: int = 1) -> PurchaseResult:
    if quantity < 1:
        return PurchaseResult(PurchaseStatus.OUT_OF_STOCK)

    async with transaction() as db:
        async with db.execute("SELECT price FROM products WHERE id = ?", (reference_id,)) as cursor:
            product = await cursor.fetchone()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            buyer = await cursor.fetchone()
        if product is None or buyer is None:
            return PurchaseResult(PurchaseStatus.OUT_OF_STOCK)

        items = await _select_items(db, reference_id, quantity)
        if len(items) < quantity:
            return PurchaseResult(PurchaseStatus.OUT_OF_STOCK)

        total = product["price"] * quantity
        if buyer["balance"] < total:
            return PurchaseResult(PurchaseStatus.INSUFFICIENT_FUNDS)

        cursor = await db.execute(
            "INSERT INTO orders (user_id, product_id, price, quantity, method, status, paid_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, reference_id, total, quantity, PaymentMethod.BALANCE, OrderStatus.PAID),
        )
        order_id = cursor.lastrowid
        await _mark_sold(db, items, user_id, order_id)
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (total, user_id))
        await log_balance_change(db, user_id, -total, BalanceReason.PURCHASE)

    return _success(order_id, items)


async def fulfill_paid_order(order_id: int) -> PurchaseResult:
    async with transaction() as db:
        cursor = await db.execute(
            "UPDATE orders SET status = ?, paid_at = CURRENT_TIMESTAMP WHERE id = ? AND status = ?",
            (OrderStatus.PAID, order_id, OrderStatus.PENDING),
        )
        if cursor.rowcount == 0:
            return PurchaseResult(PurchaseStatus.ALREADY_PROCESSED, order_id)

        async with db.execute(
            "SELECT user_id, product_id, price, quantity FROM orders WHERE id = ?", (order_id,)
        ) as cursor:
            order = await cursor.fetchone()

        items = await _select_items(db, order["product_id"], order["quantity"])
        if len(items) < order["quantity"]:
            await db.execute("UPDATE orders SET status = ? WHERE id = ?", (OrderStatus.REFUNDED, order_id))
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (order["price"], order["user_id"]),
            )
            await log_balance_change(db, order["user_id"], order["price"], BalanceReason.REFUND)
            return PurchaseResult(PurchaseStatus.REFUNDED, order_id)

        await _mark_sold(db, items, order["user_id"], order_id)

    return _success(order_id, items)


def _success(order_id: int, items: list[ClaimedItem]) -> PurchaseResult:
    return PurchaseResult(
        PurchaseStatus.SUCCESS,
        order_id,
        items[0].name,
        tuple(item.content for item in items),
    )


async def get_stats() -> Stats:
    totals_query = """
        SELECT
            (SELECT COUNT(*) FROM users) AS users,
            (SELECT COALESCE(SUM(balance), 0) FROM users) AS balances,
            (SELECT COUNT(*) FROM products WHERE is_sold = 0) AS in_stock,
            (SELECT COUNT(*) FROM products WHERE is_sold = 1) AS sold_items
    """
    sales_query = """
        SELECT
            COUNT(*) AS orders,
            COALESCE(SUM(price), 0) AS revenue,
            COALESCE(SUM(CASE WHEN method = ? THEN price END), 0) AS crypto_revenue,
            COALESCE(SUM(CASE WHEN method = ? THEN price END), 0) AS balance_revenue,
            COALESCE(SUM(paid_at >= datetime('now', '-1 day')), 0) AS day_orders,
            COALESCE(SUM(CASE WHEN paid_at >= datetime('now', '-1 day') THEN price END), 0) AS day_revenue
        FROM orders
        WHERE status = ?
    """
    async with connect() as db:
        async with db.execute(totals_query) as cursor:
            totals = await cursor.fetchone()
        async with db.execute(
            sales_query, (PaymentMethod.CRYPTOBOT, PaymentMethod.BALANCE, OrderStatus.PAID)
        ) as cursor:
            sales = await cursor.fetchone()
        async with db.execute(
            "SELECT COUNT(*) AS topups, COALESCE(SUM(amount), 0) AS topups_sum "
            "FROM topups WHERE status = ?",
            (TopupStatus.PAID,),
        ) as cursor:
            topups = await cursor.fetchone()
    return Stats(**dict(totals), **dict(sales), **dict(topups))
