"""
OpenClaw — AI агент для Threads Meta
Запуск: python main.py
"""
import asyncio
import sys
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env файл (override=True чтобы перезаписать системные переменные)
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, override=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("openclaw.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


def is_focus_overlay_mode() -> bool:
    """Desktop overlay mode does not need bot-related environment variables."""
    return len(sys.argv) > 1 and sys.argv[1].lower() in {"focus", "overlay", "focus-overlay", "adhd"}


def check_env():
    """Проверить что все нужные переменные окружения заданы"""
    required = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]

    if missing:
        logger.error(f"Отсутствуют переменные в .env: {', '.join(missing)}")
        logger.error("Скопируй .env.example в .env и заполни значения")
        return False

    # Предупреждения (не обязательные но важные)
    warnings = ["THREADS_ACCESS_TOKEN", "THREADS_USER_ID", "TELEGRAM_ALLOWED_USER_ID"]
    for key in warnings:
        if not os.getenv(key):
            logger.warning(f"Не задан {key} — часть функций будет недоступна")

    return True


async def main():
    """Основная точка входа"""
    logger.info("=" * 50)
    logger.info("OpenClaw запускается...")
    logger.info("=" * 50)

    if not check_env():
        return

    # Инициализируем базу данных
    from database.db import init_db
    init_db()
    logger.info("База данных инициализирована")

    # Запускаем планировщик постов
    from scheduler.scheduler import start_scheduler
    scheduler = start_scheduler()

    # Запускаем Telegram бота
    from bot.telegram_bot import create_bot
    bot_app = create_bot()

    logger.info("Telegram бот запущен. Ожидаю сообщения...")
    logger.info("Напиши /start в Telegram боте чтобы начать")

    # Запускаем бота
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    try:
        # Держим процесс живым
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка OpenClaw...")
    finally:
        scheduler.shutdown()
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("OpenClaw остановлен.")


if __name__ == "__main__":
    if is_focus_overlay_mode():
        from desktop.focus_overlay import run_focus_overlay

        raise SystemExit(run_focus_overlay())

    asyncio.run(main())
