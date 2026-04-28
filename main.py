import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from telethon_client import start_client, parser_loop, init_db
from bot import register_handlers


# 🔹 Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


async def main():
    logger.info("Запуск бота...")
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    await register_handlers(dp)
    logger.info("Хендлеры зарегистрированы")

    await start_client()
    logger.info("Telethon клиент запущен")

    asyncio.create_task(parser_loop(bot))
    logger.info("Parser loop запущен")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
