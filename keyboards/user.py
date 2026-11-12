from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from config import config
from database.models import Category, ProductGroup, PurchaseRecord
from keyboards.callbacks import (
    BUY_ALL,
    BuyCB,
    BuyStep,
    CatalogCB,
    CategoryCB,
    DownloadCB,
    HelpCB,
    HistoryCB,
    HomeCB,
    OrderCB,
    ProductCB,
    ProfileCB,
    PurchaseCB,
    QtyCustomCB,
    TopUpCheckCB,
    TopUpCustomCB,
    TopUpMenuCB,
    TopUpPresetCB,
)
from keyboards.common import cb, markup, pager, short
from utils.money import format_rub
from utils.pagination import Page

BTN_CATALOG = "🛍 Каталог"
BTN_PROFILE = "👤 Профиль"
BTN_HELP = "❓ Помощь"

TOPUP_PRESETS = (100, 300, 500, 1000)
QUANTITY_PRESETS = (1, 2, 5)


def _nav(back: CallbackData, *, home: bool = True) -> list[InlineKeyboardButton]:
    row = [cb("⬅️ Назад", back)]
    if home:
        row.append(cb("🏠 Главное меню", HomeCB()))
    return row


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CATALOG)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def main_menu_inline_kb() -> InlineKeyboardMarkup:
    return markup(
        [cb(BTN_CATALOG, CatalogCB()), cb(BTN_PROFILE, ProfileCB())],
        [cb(BTN_HELP, HelpCB())],
    )


def help_kb() -> InlineKeyboardMarkup:
    support = []
    if config.support_url:
        support.append(InlineKeyboardButton(text="👨‍💻 Написать в поддержку", url=config.support_url))
    return markup(support, _nav(HomeCB(), home=False))


def empty_catalog_kb() -> InlineKeyboardMarkup:
    return markup(_nav(HomeCB(), home=False))


def categories_kb(categories: Sequence[Category], page: Page) -> InlineKeyboardMarkup:
    rows = []
    for category in categories:
        stock = f"{category.stock} шт." if category.stock else "нет в наличии"
        marker = "🟢" if category.stock else "⚫️"
        rows.append([cb(f"{marker} {short(category.name)} · {stock}", CategoryCB(category_id=category.id))])
    rows.append(pager(page, lambda number: CatalogCB(page=number)))
    rows.append(_nav(HomeCB(), home=False))
    return markup(*rows)


def products_kb(
    groups: Sequence[ProductGroup], category_id: int, page: Page, catalog_page: int
) -> InlineKeyboardMarkup:
    rows = [
        [
            cb(
                f"🔹 {short(group.name)} · {format_rub(group.price)} · {group.stock} шт.",
                ProductCB(product_id=group.id),
            )
        ]
        for group in groups
    ]
    rows.append(pager(page, lambda number: CategoryCB(category_id=category_id, page=number)))
    rows.append(_nav(CatalogCB(page=catalog_page)))
    return markup(*rows)


def product_kb(group: ProductGroup, category_page: int) -> InlineKeyboardMarkup:
    if group.stock == 1:
        rows = [
            [
                cb(
                    f"🛒 Купить за {format_rub(group.price)}",
                    BuyCB(product_id=group.id, step=BuyStep.ASK, qty=1),
                )
            ]
        ]
    else:
        presets = [
            cb(f"{amount} шт.", BuyCB(product_id=group.id, step=BuyStep.ASK, qty=amount))
            for amount in QUANTITY_PRESETS
            if amount <= group.stock
        ]
        rows = [
            presets,
            [cb("✏️ Своё количество", QtyCustomCB(product_id=group.id))],
            [
                cb(
                    f"📦 Купить всё · {group.stock} шт.",
                    BuyCB(product_id=group.id, step=BuyStep.ASK, qty=BUY_ALL),
                )
            ],
        ]
    rows.append(_nav(CategoryCB(category_id=group.category_id, page=category_page)))
    return markup(*rows)


def quantity_prompt_kb(product_id: int) -> InlineKeyboardMarkup:
    return markup(_nav(ProductCB(product_id=product_id)))


def confirm_purchase_kb(group: ProductGroup, quantity: int, balance: int) -> InlineKeyboardMarkup:
    rows = [[cb("💳 Оплатить через CryptoBot", BuyCB(product_id=group.id, step=BuyStep.CRYPTO, qty=quantity))]]
    if balance >= group.price * quantity:
        rows.append(
            [
                cb(
                    f"💰 Оплатить с баланса · {format_rub(balance)}",
                    BuyCB(product_id=group.id, step=BuyStep.BALANCE, qty=quantity),
                )
            ]
        )
    rows.append(_nav(ProductCB(product_id=group.id)))
    return markup(*rows)


def invoice_kb(order_id: int, pay_url: str, product_id: int, quantity: int) -> InlineKeyboardMarkup:
    return markup(
        [InlineKeyboardButton(text="💳 Оплатить счёт", url=pay_url)],
        [cb("🔄 Проверить оплату", OrderCB(order_id=order_id))],
        _nav(BuyCB(product_id=product_id, step=BuyStep.ASK, qty=quantity)),
    )


def to_catalog_kb() -> InlineKeyboardMarkup:
    return markup([cb("🛍 В каталог", CatalogCB()), cb("🏠 Главное меню", HomeCB())])


def delivery_kb(order_id: int) -> InlineKeyboardMarkup:
    return markup(
        [cb("📥 Скачать .txt", DownloadCB(order_id=order_id))],
        [cb("🛍 В каталог", CatalogCB()), cb("🏠 Главное меню", HomeCB())],
    )


def profile_kb() -> InlineKeyboardMarkup:
    return markup(
        [cb("💳 Пополнить баланс", TopUpMenuCB()), cb("📜 История покупок", HistoryCB())],
        [cb(BTN_CATALOG, CatalogCB())],
        _nav(HomeCB(), home=False),
    )


def history_kb(purchases: Sequence[PurchaseRecord], page: Page) -> InlineKeyboardMarkup:
    rows = []
    for purchase in purchases:
        amount = f" ×{purchase.quantity}" if purchase.quantity > 1 else ""
        label = f"📦 №{purchase.order_id} · {short(purchase.product_name, 20)}{amount}"
        rows.append([cb(label, PurchaseCB(order_id=purchase.order_id, page=page.number))])
    if not purchases:
        rows.append([cb(BTN_CATALOG, CatalogCB())])
    rows.append(pager(page, lambda number: HistoryCB(page=number)))
    rows.append(_nav(ProfileCB()))
    return markup(*rows)


def purchase_view_kb(order_id: int, page: int) -> InlineKeyboardMarkup:
    return markup(
        [cb("📥 Скачать .txt", DownloadCB(order_id=order_id))],
        _nav(HistoryCB(page=page)),
    )


def topup_menu_kb() -> InlineKeyboardMarkup:
    presets = [cb(f"{amount} ₽", TopUpPresetCB(amount=amount)) for amount in TOPUP_PRESETS]
    return markup(
        presets[:2],
        presets[2:],
        [cb("✏️ Своя сумма", TopUpCustomCB())],
        _nav(ProfileCB()),
    )


def topup_amount_kb() -> InlineKeyboardMarkup:
    return markup(_nav(TopUpMenuCB()))


def topup_invoice_kb(topup_id: int, pay_url: str) -> InlineKeyboardMarkup:
    return markup(
        [InlineKeyboardButton(text="💳 Оплатить счёт", url=pay_url)],
        [cb("🔄 Проверить оплату", TopUpCheckCB(topup_id=topup_id))],
        _nav(TopUpMenuCB()),
    )


def to_profile_kb() -> InlineKeyboardMarkup:
    return markup(
        [cb("👤 Профиль", ProfileCB()), cb(BTN_CATALOG, CatalogCB())],
        [cb("🏠 Главное меню", HomeCB())],
    )
