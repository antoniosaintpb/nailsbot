import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.handlers import client, common, master
from bot.middlewares.db import DbSessionMiddleware
from config import get_settings
from db.session import ensure_seed, get_session_factory, init_db


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()
    async with get_session_factory()() as s:
        await ensure_seed(s)
        await s.commit()

    settings = get_settings()
    bot = Bot(settings.bot_token)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть главное меню"),
            BotCommand(command="master", description="Панель мастера"),
        ]
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware())
    dp.include_router(common.router)
    dp.include_router(master.router)
    dp.include_router(client.router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
