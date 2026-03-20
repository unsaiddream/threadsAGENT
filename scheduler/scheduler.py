"""
Планировщик задач:
- Каждую минуту: публикует запланированные посты из очереди
- Каждый день в заданный час: запускает автопилот
"""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import get_pending_scheduled_posts, mark_scheduled_post_done, get_autopilot_settings
from agent.skills.threads import post_text, post_with_image

logger = logging.getLogger(__name__)

# Глобальная ссылка на функцию уведомлений (заполняется из бота)
_notify_fn = None


def set_notify_fn(fn):
    """Зарегистрировать функцию для отправки уведомлений в Telegram"""
    global _notify_fn
    _notify_fn = fn


async def publish_pending_posts():
    """Публикует запланированные посты которые уже пора"""
    pending = get_pending_scheduled_posts()
    for post in pending:
        try:
            if post.get("media_url"):
                result = await post_with_image(post["text"], post["media_url"])
            else:
                result = await post_text(post["text"])

            if result.get("success"):
                mark_scheduled_post_done(post["id"], "published")
                logger.info(f"Запланированный пост #{post['id']} опубликован")
                if _notify_fn:
                    await _notify_fn(f"Запланированный пост опубликован:\n{post['text'][:100]}...")
            else:
                mark_scheduled_post_done(post["id"], f"error")
                logger.error(f"Ошибка публикации поста #{post['id']}: {result.get('error')}")

        except Exception as e:
            mark_scheduled_post_done(post["id"], "exception")
            logger.error(f"Исключение при публикации #{post['id']}: {e}")


async def run_autopilot_job():
    """Запускает автопилот если включён"""
    from agent.autopilot import run_autopilot
    settings = get_autopilot_settings()

    if not settings.get("enabled"):
        return

    logger.info("Запуск автопилота по расписанию...")
    try:
        await run_autopilot(notify_fn=_notify_fn)
    except Exception as e:
        logger.error(f"Ошибка автопилота: {e}")
        if _notify_fn:
            await _notify_fn(f"Автопилот завершился с ошибкой: {e}")


def start_scheduler() -> AsyncIOScheduler:
    """Запустить планировщик"""
    scheduler = AsyncIOScheduler()

    # Каждую минуту — проверяем очередь запланированных постов
    scheduler.add_job(publish_pending_posts, "interval", minutes=1, id="scheduled_posts")

    # Каждый день в 10:00 — автопилот (время можно менять через настройки)
    scheduler.add_job(
        run_autopilot_job,
        "cron",
        hour=10,
        minute=0,
        id="autopilot_daily",
        misfire_grace_time=3600  # Запустить даже если пропустили до 1 часа
    )

    scheduler.start()
    logger.info("Планировщик запущен (посты: каждую минуту, автопилот: 10:00)")
    return scheduler
