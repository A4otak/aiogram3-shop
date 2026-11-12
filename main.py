import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import config
from database import init_db
from handlers import admin, user

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    )

    await init_db()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_routers(user.router, admin.router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
