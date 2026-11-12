from collections.abc import Callable, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.callbacks import NoopCB
from utils.pagination import Page


def cb(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def markup(*rows: Sequence[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows if row])


def short(text: str, limit: int = 26) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def pager(page: Page, make_callback: Callable[[int], CallbackData]) -> list[InlineKeyboardButton]:
    if page.pages == 1:
        return []

    previous = cb("⬅️", make_callback(page.number - 1)) if page.number > 1 else cb("·", NoopCB())
    following = cb("➡️", make_callback(page.number + 1)) if page.number < page.pages else cb("·", NoopCB())
    return [previous, cb(f"Стр. {page.number}/{page.pages}", NoopCB()), following]
