from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup

from database.models import Category, ProductGroup
from keyboards.callbacks import AdminAction, AdminCB, HomeCB
from keyboards.common import cb, markup, pager, short
from utils.money import format_rub
from utils.pagination import Page

BACK_TEXT = "⬅️ Назад"


def _back() -> list:
    return [cb(BACK_TEXT, AdminCB(action=AdminAction.BACK))]


def admin_menu_kb() -> InlineKeyboardMarkup:
    return markup(
        [cb("➕ Добавить категорию", AdminCB(action=AdminAction.ADD_CATEGORY))],
        [cb("📦 Добавить товар", AdminCB(action=AdminAction.ADD_PRODUCT))],
        [cb("🔐 Настроить CryptoBot", AdminCB(action=AdminAction.CRYPTOBOT))],
        [cb("📊 Статистика", AdminCB(action=AdminAction.STATS))],
        [cb("💰 Баланс пользователя", AdminCB(action=AdminAction.BALANCE))],
        [cb("🏠 В меню магазина", HomeCB())],
    )


def back_kb() -> InlineKeyboardMarkup:
    return markup(_back())


def category_pick_kb(categories: Sequence[Category], page: Page) -> InlineKeyboardMarkup:
    rows = [
        [
            cb(
                f"{short(category.name)} · {category.stock} шт.",
                AdminCB(action=AdminAction.PICK_CATEGORY, value=category.id),
            )
        ]
        for category in categories
    ]
    rows.append(pager(page, lambda number: AdminCB(action=AdminAction.CATEGORY_PAGE, value=number)))
    rows.append(_back())
    return markup(*rows)


def product_choice_kb(groups: Sequence[ProductGroup], page: Page) -> InlineKeyboardMarkup:
    rows = [[cb("🆕 Создать новый товар", AdminCB(action=AdminAction.NEW_PRODUCT))]]
    rows += [
        [
            cb(
                f"📦 Пополнить: {short(group.name, 22)} ({format_rub(group.price)})",
                AdminCB(action=AdminAction.PICK_PRODUCT, value=group.id),
            )
        ]
        for group in groups
    ]
    rows.append(pager(page, lambda number: AdminCB(action=AdminAction.PRODUCT_PAGE, value=number)))
    rows.append(_back())
    return markup(*rows)


def balance_action_kb(page: Page) -> InlineKeyboardMarkup:
    return markup(
        [
            cb("➕ Выдать", AdminCB(action=AdminAction.GIVE)),
            cb("➖ Забрать", AdminCB(action=AdminAction.TAKE)),
        ],
        pager(page, lambda number: AdminCB(action=AdminAction.HISTORY_PAGE, value=number)),
        _back(),
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return markup(
        [cb("✅ Подтвердить", AdminCB(action=AdminAction.CONFIRM))],
        _back(),
    )
