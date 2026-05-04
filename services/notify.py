import logging

from aiogram import Bot

from config import get_settings

logger = logging.getLogger(__name__)


async def notify_masters(bot: Bot, text: str) -> None:
    for mid in get_settings().master_ids:
        try:
            await bot.send_message(mid, text)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление мастеру %s: %s", mid, e)
