"""
Планировщик задач:
- Каждую минуту: публикует запланированные посты из очереди
- Каждый день в заданный час: запускает автопилот (Threads)
- Каждый день в 08:00 Алматы: Instagram ежедневный пост
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


async def run_instagram_daily_post():
    """
    Ежедневный пост в Instagram: топ-5 дешёвых продуктов.
    Запускается каждый день в 08:00 Алматы (03:00 UTC).

    Флоу:
      1. Берём топ-5 продуктов из minprice.kz
      2. Генерируем PNG-карточку через Pillow
      3. Загружаем на Cloudflare R2 — получаем публичный URL
      4. Генерируем подпись через Claude
      5. Публикуем в Instagram
    """
    import asyncio
    from agent.skills.minprice import get_top_products
    from agent.skills.instagram import publish_photo
    from agent.skills.instagram_content import generate_daily_caption
    from agent.skills.image_generator import generate_price_image, upload_to_r2
    from database.db import save_instagram_post

    logger.info("Instagram: запускаю ежедневный пост...")
    if _notify_fn:
        await _notify_fn("📸 Instagram: генерирую ежедневный пост...", topic="posts")

    # Шаг 1: продукты
    try:
        products = await get_top_products(limit=5)
    except Exception as e:
        logger.error(f"Instagram: ошибка получения продуктов: {e}")
        products = []

    # Шаг 2: генерируем изображение
    try:
        image_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_price_image, products
        )
        logger.info(f"Instagram: изображение сгенерировано ({len(image_bytes)//1024} KB)")
    except Exception as e:
        logger.error(f"Instagram: ошибка генерации изображения: {e}")
        if _notify_fn:
            await _notify_fn(f"❌ Instagram: генерация изображения: {e}", topic="errors")
        return

    # Шаг 3: загружаем на R2
    try:
        image_url = await asyncio.get_event_loop().run_in_executor(
            None, upload_to_r2, image_bytes, None
        )
        logger.info(f"Instagram: загружено на R2 → {image_url}")
    except Exception as e:
        logger.error(f"Instagram: ошибка загрузки на R2: {e}")
        if _notify_fn:
            await _notify_fn(f"❌ Instagram: загрузка на R2: {e}", topic="errors")
        return

    # Шаг 4: генерируем подпись
    caption = await generate_daily_caption(products)

    # Шаг 5: публикуем
    result = await publish_photo(image_url, caption)

    if result.get("success"):
        save_instagram_post(
            media_id=result["media_id"],
            caption=caption,
            image_url=image_url,
            post_type="PHOTO",
            permalink=result.get("permalink"),
        )
        logger.info(f"Instagram: опубликован {result.get('permalink')}")
        if _notify_fn:
            await _notify_fn(
                f"📸 Instagram пост опубликован!\n{caption[:150]}...\n🔗 {result.get('permalink', '')}",
                topic="posts"
            )
    else:
        logger.error(f"Instagram: ошибка публикации: {result.get('error')}")
        if _notify_fn:
            await _notify_fn(f"❌ Instagram: {result.get('error')}", topic="errors")


def start_scheduler() -> AsyncIOScheduler:
    """
    Запустить планировщик.

    Расписание по алгоритму Threads 2025 (время UTC = Алматы UTC+5 минус 5):
    - 05:00 UTC = 10:00 Алматы  — утренний пик (люди едут на работу)
    - 07:00 UTC = 12:00 Алматы  — обеденный пик
    - 15:00 UTC = 20:00 Алматы  — вечерний пик (самый высокий охват)
    - 17:00 UTC = 22:00 Алматы  — поздний вечер (активная аудитория)
    """
    scheduler = AsyncIOScheduler()

    # Каждую минуту — проверяем очередь запланированных постов
    scheduler.add_job(publish_pending_posts, "interval", minutes=1, id="scheduled_posts")

    # 4 запуска автопилота в день — пиковые часы по Алматы
    peak_hours_utc = [
        (5,  0,  "autopilot_10am"),   # 10:00 Алматы — утро
        (7,  0,  "autopilot_12pm"),   # 12:00 Алматы — обед
        (15, 0,  "autopilot_20pm"),   # 20:00 Алматы — вечер (главный пик)
        (17, 0,  "autopilot_22pm"),   # 22:00 Алматы — поздний вечер
    ]
    for hour, minute, job_id in peak_hours_utc:
        scheduler.add_job(
            run_autopilot_job,
            "cron",
            hour=hour,
            minute=minute,
            id=job_id,
            misfire_grace_time=1800,
        )

    # Instagram: ежедневный пост в 08:00 Алматы = 03:00 UTC
    scheduler.add_job(
        run_instagram_daily_post,
        "cron",
        hour=3,
        minute=0,
        id="instagram_daily",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("Планировщик запущен: Threads 4x/день + Instagram 08:00 Алматы")
    return scheduler
