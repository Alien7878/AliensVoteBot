import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
from database import Database
from handlers import register_all_routers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    me = await bot.get_me()
    config.BOT_USERNAME = me.username
    logger.info(f"Bot @{me.username} (id={me.id}) started.")

    # Make sure DB is ready
    await Database.get_instance()


async def on_shutdown(bot: Bot):
    db = await Database.get_instance()
    await db.close()
    logger.info("Bot stopped.")


async def main():
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    # Register lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Register all routers
    register_all_routers(dp)

    logger.info("Starting polling with 60 concurrent workers …")
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
