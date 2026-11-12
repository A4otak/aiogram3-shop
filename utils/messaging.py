import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def render(
    target: Message | CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(target, Message):
        await target.answer(text, reply_markup=markup)
        return

    if isinstance(target.message, Message):
        try:
            await target.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message:
                return
            logger.debug("Сообщение не удалось отредактировать: %s", exc)

    await target.bot.send_message(target.from_user.id, text, reply_markup=markup)
