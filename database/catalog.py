from collections.abc import Sequence

from database.db import connect
from database.models import Category, ProductGroup

CATEGORY_STOCK = """
    SELECT c.id, c.name, COALESCE(SUM(p.is_sold = 0), 0) AS stock
    FROM categories c
    LEFT JOIN products p ON p.category_id = c.id
"""


async def create_category(name: str) -> bool:
    async with connect() as db:
        cursor = await db.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        await db.commit()
        return cursor.rowcount > 0


async def list_categories(limit: int, offset: int = 0) -> list[Category]:
    query = f"{CATEGORY_STOCK} GROUP BY c.id ORDER BY c.name LIMIT ? OFFSET ?"
    async with connect() as db:
        async with db.execute(query, (limit, offset)) as cursor:
            return [Category(**dict(row)) async for row in cursor]


async def count_categories() -> int:
    async with connect() as db:
        async with db.execute("SELECT COUNT(*) AS cnt FROM categories") as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def count_categories_before(name: str) -> int:
    async with connect() as db:
        async with db.execute("SELECT COUNT(*) AS cnt FROM categories WHERE name < ?", (name,)) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def get_category(category_id: int) -> Category | None:
    query = f"{CATEGORY_STOCK} WHERE c.id = ? GROUP BY c.id"
    async with connect() as db:
        async with db.execute(query, (category_id,)) as cursor:
            row = await cursor.fetchone()
    return Category(**dict(row)) if row else None


async def add_products(category_id: int, name: str, price: int, contents: Sequence[str]) -> int:
    async with connect() as db:
        await db.executemany(
            "INSERT INTO products (category_id, name, price, content) VALUES (?, ?, ?, ?)",
            [(category_id, name, price, content) for content in contents],
        )
        await db.commit()
    return len(contents)


async def list_product_groups(category_id: int, limit: int, offset: int = 0) -> list[ProductGroup]:
    query = """
        SELECT MIN(p.id) AS id, p.category_id, c.name AS category_name,
               p.name, p.price, COUNT(*) AS stock
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.category_id = ? AND p.is_sold = 0
        GROUP BY p.name, p.price
        ORDER BY p.name, p.price
        LIMIT ? OFFSET ?
    """
    async with connect() as db:
        async with db.execute(query, (category_id, limit, offset)) as cursor:
            return [ProductGroup(**dict(row)) async for row in cursor]


async def count_product_groups(category_id: int) -> int:
    query = """
        SELECT COUNT(*) AS cnt FROM (
            SELECT 1 FROM products WHERE category_id = ? AND is_sold = 0 GROUP BY name, price
        )
    """
    async with connect() as db:
        async with db.execute(query, (category_id,)) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def count_groups_before(group: ProductGroup) -> int:
    query = """
        SELECT COUNT(*) AS cnt FROM (
            SELECT name, price FROM products
            WHERE category_id = ? AND is_sold = 0
            GROUP BY name, price
        )
        WHERE name < ? OR (name = ? AND price < ?)
    """
    params = (group.category_id, group.name, group.name, group.price)
    async with connect() as db:
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def list_restock_groups(category_id: int, limit: int, offset: int = 0) -> list[ProductGroup]:
    query = """
        SELECT MIN(p.id) AS id, p.category_id, c.name AS category_name,
               p.name, p.price, COALESCE(SUM(p.is_sold = 0), 0) AS stock
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.category_id = ?
        GROUP BY p.name, p.price
        ORDER BY p.name, p.price
        LIMIT ? OFFSET ?
    """
    async with connect() as db:
        async with db.execute(query, (category_id, limit, offset)) as cursor:
            return [ProductGroup(**dict(row)) async for row in cursor]


async def count_restock_groups(category_id: int) -> int:
    query = """
        SELECT COUNT(*) AS cnt FROM (
            SELECT 1 FROM products WHERE category_id = ? GROUP BY name, price
        )
    """
    async with connect() as db:
        async with db.execute(query, (category_id,)) as cursor:
            row = await cursor.fetchone()
    return row["cnt"]


async def get_product_group(product_id: int) -> ProductGroup | None:
    query = """
        SELECT p.id, p.category_id, c.name AS category_name, p.name, p.price,
               (SELECT COUNT(*) FROM products s
                 WHERE s.category_id = p.category_id
                   AND s.name = p.name
                   AND s.price = p.price
                   AND s.is_sold = 0) AS stock
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.id = ?
    """
    async with connect() as db:
        async with db.execute(query, (product_id,)) as cursor:
            row = await cursor.fetchone()
    return ProductGroup(**dict(row)) if row else None
