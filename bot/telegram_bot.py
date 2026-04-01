"""
Telegram бот — интерфейс для управления агентом
"""
import logging
import os
from telegram import (
    Update, BotCommand, BotCommandScopeDefault,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    InlineQueryHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from agent.claude_agent import process_message

logger = logging.getLogger(__name__)

# Глобальная ссылка на приложение (для notify_fn)
_app: Application = None


def get_allowed_user_id() -> int | None:
    user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    return int(user_id) if user_id else None


def is_authorized(update: Update) -> bool:
    allowed_id = get_allowed_user_id()
    if allowed_id is None:
        return True
    return update.effective_user.id == allowed_id


def can_use_reply_url(update: Update) -> bool:
    return True


async def deny_non_owner(update: Update):
    if update.message:
        await update.message.reply_text(
            "Эта команда доступна только владельцу бота.\n"
            "Для всех пользователей доступна только: /reply_url <ссылка_на_post>"
        )


CHANNEL_ID = -1003864267239  # Группа/канал для отчётов автопилота

# Топики для канала (создаются один раз, ID хранятся в БД)
TOPIC_DEFS = {
    "posts":   "📝 Посты",
    "replies": "💬 Ответы",
    "summary": "📊 Отчёты",
    "errors":  "⚠️ Ошибки",
}

_topics_cache: dict[str, int] = {}  # name → thread_id
_topics_loaded = False


async def _ensure_topics():
    """Загружает или создаёт топики в канале. ID хранятся в SQLite."""
    global _topics_cache, _topics_loaded
    if _topics_loaded:
        return
    _topics_loaded = True

    if not _app:
        return

    from database.db import get_all_topics, save_topic_id

    # 1. Загружаем из БД
    _topics_cache = get_all_topics()
    logger.info(f"Топики из БД: {_topics_cache}")

    # 2. Создаём недостающие
    for key, name in TOPIC_DEFS.items():
        if key in _topics_cache:
            continue
        try:
            result = await _app.bot.create_forum_topic(
                chat_id=CHANNEL_ID,
                name=name,
            )
            tid = result.message_thread_id
            _topics_cache[key] = tid
            save_topic_id(key, tid)
            logger.info(f"Создан топик '{name}' → thread_id={tid}")
        except Exception as e:
            logger.warning(f"Не удалось создать топик '{name}': {e}")


async def notify(text: str, group_only: bool = False, topic: str = None):
    """
    Отправить уведомление владельцу + в канал с топиком.
    topic: "posts" | "replies" | "summary" | "errors"
    """
    if not _app:
        return

    await _ensure_topics()

    user_id = get_allowed_user_id()

    # 1. Личка владельцу
    if not group_only and user_id:
        try:
            await _app.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Ошибка личка {user_id}: {e}")

    # 2. Канал с топиком
    thread_id = _topics_cache.get(topic) if topic else None
    try:
        kwargs = {
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await _app.bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"Ошибка канал {CHANNEL_ID} (topic={topic}, tid={thread_id}): {e}")
        # Fallback: без топика
        if thread_id:
            try:
                await _app.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(
            "Можно прислать команду /reply_url <ссылка_на_post> — я сгенерирую один контекстный ответ на конкретный пост Threads."
        )
        return

    await update.message.reply_text(
        "Привет! Я OpenClaw — твой AI-агент для Threads.\n\n"
        "Что я умею:\n"
        "• Публиковать посты в Threads\n"
        "• Создавать контент-планы\n"
        "• Генерировать посты под нишу\n"
        "• Планировать публикации\n"
        "• Автопилот: 5 постов + 10 ответов в день\n\n"
        "Команды:\n"
        "/autopilot — управление автопилотом\n"
        "/posts — последние посты\n"
        "/help — все команды\n\n"
        "Или просто напиши что нужно сделать."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(
            "Доступная команда:\n"
            "/reply_url <ссылка_на_post> — сгенерировать и отправить один ответ на конкретный пост Threads"
        )
        return

    await update.message.reply_text(
        "Команды:\n\n"
        "⚡ Реальное время:\n"
        "/monitor_on — мониторинг каждые 3 мин (или /monitor_on 5 для 5 мин)\n"
        "/monitor_off — остановить мониторинг\n"
        "/monitor — статус\n\n"
        "🤖 Автопилот:\n"
        "/run_autopilot — посты + ответы прямо сейчас\n"
        "/run_replies — только ответы на чужие посты\n"
        "/autopilot_on — включить по расписанию\n"
        "/autopilot_off — выключить\n"
        "/autopilot — настройки\n\n"
        "📝 Прочее:\n"
        "/test_post — один тестовый пост\n"
        "/check_search — диагностика поиска\n"
        "/clear — очистить историю\n"
        "/help — эта справка"
    )


def _format_cost(result: dict) -> str:
    """Формирует строку с инфо о стоимости запроса"""
    cost = result.get("cost_usd")
    model = result.get("model", "")
    if cost is not None:
        return f"\n\n─\n💰 ${cost:.4f} · {model}"
    elif model and model != "error":
        return f"\n\n─\n🔄 {model} (бесплатно)"
    return ""


async def posts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    await update.message.reply_text("Загружаю посты из Threads...")
    result = await process_message("Покажи мои последние 10 постов из Threads")
    response = result["text"] + _format_cost(result)
    await update.message.reply_text(response)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from database.db import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()
    await update.message.reply_text("История диалога очищена.")


async def autopilot_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус и настройки автопилота"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from database.db import get_autopilot_settings
    s = get_autopilot_settings()

    status = "включён" if s.get("enabled") else "выключен"
    keywords = ", ".join(s.get("keywords", []))
    last_run = s.get("last_run") or "никогда"

    await update.message.reply_text(
        f"Автопилот: {status}\n\n"
        f"Ниша: {s.get('niche')}\n"
        f"Ключевые слова: {keywords}\n"
        f"Своих постов в день: {s.get('own_posts_count')}\n"
        f"Ответов в день: {s.get('reply_posts_count')}\n"
        f"Время запуска: {s.get('run_hour')}:00\n"
        f"Последний запуск: {last_run}\n\n"
        "Команды:\n"
        "/autopilot_on — включить\n"
        "/autopilot_off — выключить\n"
        "/run_autopilot — запустить сейчас"
    )


async def autopilot_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from database.db import update_autopilot_settings
    update_autopilot_settings(enabled=1)
    await update.message.reply_text(
        "Автопилот включён.\n"
        "4 раза в день (10:00, 12:00, 20:00, 22:00 Алматы):\n"
        "→ @almat_y007kz постит 2 жалобы на цены\n"
        "→ через 5-7 мин аккаунт 2 отвечает с рекламой minprice\n"
        "Итого: 8 постов + 8 ответов в день.\n\n"
        "Запустить прямо сейчас: /run_autopilot"
    )


async def autopilot_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from database.db import update_autopilot_settings
    update_autopilot_settings(enabled=0)
    await update.message.reply_text("Автопилот выключен.")


async def run_autopilot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить автопилот немедленно"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    await update.message.reply_text(
        "Запускаю автопилот...\n"
        "Буду присылать уведомления по ходу.\n"
        "(Займёт ~10-15 минут)"
    )

    from agent.autopilot import run_autopilot
    import asyncio

    # force=True — ручной запуск работает даже если автопилот выключен
    asyncio.create_task(run_autopilot(notify_fn=notify, force=True))


async def test_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Опубликовать один тестовый пост (для проверки новых фич)"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    await update.message.reply_text("🧪 Генерирую один тестовый пост...")

    from agent.autopilot import run_test_post
    import asyncio

    asyncio.create_task(run_test_post(notify_fn=notify))


async def run_replies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить только ответы на чужие посты"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    await update.message.reply_text(
        "Ищу посты для ответов...\n"
        "Буду присылать уведомления по ходу."
    )

    from agent.autopilot import run_replies_only
    import asyncio

    asyncio.create_task(run_replies_only(notify_fn=notify, count=10))


async def monitor_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус монитора реального времени"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from agent.autopilot import is_monitor_active, _monitor_last_stats
    active = is_monitor_active()
    status = "🟢 работает" if active else "🔴 остановлен"

    stats = _monitor_last_stats
    if stats.get("cycle"):
        last_info = (
            f"\nПоследний цикл #{stats['cycle']} в {stats.get('time','?')}:\n"
            f"  найдено: {stats['found']} постов\n"
            f"  ответов: {stats['replied']}\n"
            f"  пропущено: {stats['skipped']}"
        )
    else:
        last_info = "\nЦиклов ещё не было с последнего запуска."

    await update.message.reply_text(
        f"Монитор реального времени: {status}{last_info}\n\n"
        "Ищет СВЕЖИЕ посты каждые 3 мин и сразу отвечает.\n\n"
        "/monitor_on — включить\n"
        "/monitor_off — выключить"
    )


async def monitor_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить мониторинг в реальном времени"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from agent.autopilot import is_monitor_active, run_monitor_loop
    import asyncio

    if is_monitor_active():
        await update.message.reply_text("Монитор уже работает. /monitor_off чтобы остановить.")
        return

    # Интервал из аргумента: /monitor_on 5 → каждые 5 минут
    interval = 3
    if context.args:
        try:
            interval = max(1, min(30, int(context.args[0])))
        except ValueError:
            pass

    await update.message.reply_text(
        f"⚡ Монитор запускается...\n"
        f"Буду искать свежие посты каждые {interval} мин\n"
        f"и сразу отвечать на них.\n\n"
        f"/monitor_off — остановить"
    )

    asyncio.create_task(run_monitor_loop(notify_fn=notify, interval_minutes=interval))


async def monitor_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановить мониторинг"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from agent.autopilot import stop_monitor, is_monitor_active

    if not is_monitor_active():
        await update.message.reply_text("Монитор и так не работает.")
        return

    stop_monitor()
    await update.message.reply_text("⏹ Монитор остановлен.")


async def check_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика: показать комментарии под своими постами"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from agent.skills.threads import get_my_posts, get_post_replies

    await update.message.reply_text("Проверяю комментарии под своими постами...")

    posts_data = await get_my_posts(limit=10)
    posts = posts_data.get("data", [])
    posts_with_replies = [p for p in posts if (p.get("replies_count") or 0) > 0]

    if not posts_with_replies:
        await update.message.reply_text(
            "Нет постов с комментариями.\n"
            "Как только кто-то напишет под постами — автопилот начнёт отвечать."
        )
        return

    # Показываем первый пост с комментариями
    sample_post = posts_with_replies[0]
    replies_data = await get_post_replies(sample_post["id"], limit=5)
    replies = replies_data.get("data", [])

    reply_lines = "\n".join([f"• @{r.get('username','?')}: {r.get('text','')[:60]}..." for r in replies[:5]])
    await update.message.reply_text(
        f"✅ Найдено {len(posts_with_replies)} постов с комментариями.\n\n"
        f"Пост: {sample_post.get('text','')[:80]}...\n"
        f"Комментариев: {sample_post.get('replies_count', 0)}\n\n"
        f"Примеры:\n{reply_lines or 'пусто'}"
    )


async def decoy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить decoy цикл вручную: создать жалобный пост + ответить на него"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    if not os.getenv("DECOY_THREADS_ACCESS_TOKEN"):
        await update.message.reply_text(
            "❌ DECOY_THREADS_ACCESS_TOKEN не задан в .env\n"
            "Добавь API токен второго аккаунта."
        )
        return

    await update.message.reply_text("🎭 Запускаю decoy цикл (создаю жалобный пост + отвечаю)...")

    from agent.autopilot import run_decoy_cycle
    result = await run_decoy_cycle(notify_fn=notify)

    if not result.get("success"):
        await update.message.reply_text(f"❌ Ошибка: {result.get('error', '?')}")


async def instagram_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Опубликовать ежедневный пост в Instagram вручную"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    import os
    if not os.getenv("INSTAGRAM_ACCESS_TOKEN"):
        await update.message.reply_text(
            "❌ INSTAGRAM_ACCESS_TOKEN не задан в .env\n"
            "Добавь токен Instagram Business аккаунта."
        )
        return

    await update.message.reply_text("📸 Публикую пост в Instagram...")
    from scheduler.scheduler import run_instagram_daily_post
    await run_instagram_daily_post()


async def instagram_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать последние посты Instagram из базы"""
    if not is_authorized(update):
        await deny_non_owner(update)
        return

    from database.db import get_recent_instagram_posts
    posts = get_recent_instagram_posts(limit=5)
    if not posts:
        await update.message.reply_text("📸 Instagram постов пока нет.")
        return

    text = "📸 Последние Instagram посты:\n\n"
    for p in posts:
        text += f"• {p['created_at'][:10]} — {p['caption'][:60]}...\n"
        if p.get("permalink"):
            text += f"  {p['permalink']}\n"
    await update.message.reply_text(text)


async def reply_url_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сгенерировать и отправить один ответ на конкретный URL поста Threads"""
    if not can_use_reply_url(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/reply_url https://www.threads.com/@username/post/SHORTCODE"
        )
        return

    post_url = context.args[0].strip()
    if "threads.com/" not in post_url and "threads.net/" not in post_url:
        await update.message.reply_text("Нужна ссылка именно на пост Threads.")
        return

    await update.message.reply_text("Открываю пост, генерирую ответ и пробую опубликовать...")

    from agent.autopilot import reply_to_post_url

    try:
        result = await reply_to_post_url(post_url)
        if result.get("success"):
            target = result.get("target", {})
            reply_text = result.get("reply_text", "")
            method = "🌐 API" if not result.get("target", {}).get("via_browser") else "🖥 браузер"
            await update.message.reply_text(
                f"✅ Ответ опубликован [{method}] → @{target.get('username', '?')}:\n\n"
                f"{reply_text}"
            )
        else:
            err = result.get("error", "неизвестная ошибка")
            hint = ""
            if "не залогинен" in err or "sessionid" in err.lower() or "поле ввода" in err:
                hint = (
                    "\n\n💡 Нужно обновить куки сессии Threads:\n"
                    "1. Открой threads.com → DevTools → Application → Cookies\n"
                    "2. Скопируй значения sessionid, csrftoken, ds_user_id, ig_did, mid\n"
                    "3. Обнови в .env файле"
                )
            await update.message.reply_text(
                f"❌ Не получилось ответить.\n{err}{hint}"
            )
    except Exception as e:
        logger.error(f"reply_url error: {e}")
        await update.message.reply_text(f"Ошибка: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(
            "Обычные сообщения и остальные команды доступны только владельцу.\n"
            "Для всех пользователей доступна команда: /reply_url <ссылка_на_post>"
        )
        return

    user_text = update.message.text
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        result = await process_message(user_text)
        response = result["text"]
        cost_line = _format_cost(result)

        if len(response) > 4096:
            # Разбиваем на части, стоимость добавляем к последней части
            parts = [response[i:i+4096] for i in range(0, len(response), 4096)]
            for idx, part in enumerate(parts):
                if idx == len(parts) - 1:
                    await update.message.reply_text(part + cost_line)
                else:
                    await update.message.reply_text(part)
        else:
            await update.message.reply_text(response + cost_line)

    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)}\n\nПопробуй снова.")


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline mode: @bot decoy → карточка с кнопкой запуска"""
    query_text = update.inline_query.query.strip().lower()

    results = []
    if query_text in ("", "decoy"):
        has_token = bool(os.getenv("DECOY_THREADS_ACCESS_TOKEN"))
        description = (
            "Создать жалобный пост + ответить на него"
            if has_token else
            "⚠️ DECOY_THREADS_ACCESS_TOKEN не задан в .env"
        )
        results.append(
            InlineQueryResultArticle(
                id="decoy",
                title="🎭 Decoy цикл",
                description=description,
                input_message_content=InputTextMessageContent(
                    "🎭 <b>Decoy цикл</b> — создаю жалобный пост и отвечаю на него...",
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("▶️ Запустить", callback_data="run_decoy")
                ]]),
            )
        )

    await update.inline_query.answer(results, cache_time=0)


async def _run_decoy_task():
    """Фоновая задача: запускает decoy цикл и шлёт результат через notify."""
    from agent.autopilot import run_decoy_cycle
    result = await run_decoy_cycle(notify_fn=notify)
    if not result.get("success"):
        await notify(f"❌ Decoy ошибка: {result.get('error', '?')}", topic="errors")


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопки ▶️ Запустить (run_decoy)"""
    cbq = update.callback_query

    # Проверка авторизации
    allowed_id = get_allowed_user_id()
    if allowed_id and cbq.from_user.id != allowed_id:
        await cbq.answer("⛔ Нет доступа", show_alert=True)
        return

    if cbq.data != "run_decoy":
        return

    if not os.getenv("DECOY_THREADS_ACCESS_TOKEN"):
        await cbq.answer("❌ DECOY_THREADS_ACCESS_TOKEN не задан в .env", show_alert=True)
        return

    await cbq.answer("🎭 Запускаю decoy цикл...")

    # Обновляем текст сообщения (убираем кнопку)
    new_text = "🎭 <b>Decoy цикл запущен</b>\nРезультат придёт в личку."
    try:
        if cbq.inline_message_id:
            await context.bot.edit_message_text(
                inline_message_id=cbq.inline_message_id,
                text=new_text,
                parse_mode="HTML",
            )
        elif cbq.message:
            await cbq.message.edit_text(new_text, parse_mode="HTML")
    except Exception:
        pass

    import asyncio
    asyncio.create_task(_run_decoy_task())


async def _setup_bot_commands(app: Application):
    """Регистрирует команды в меню Telegram (вызывается при старте)."""
    commands = [
        BotCommand("reply_url",     "Ответить на конкретный пост Threads по ссылке"),
        BotCommand("monitor_on",    "⚡ Мониторинг в реальном времени (каждые 3 мин)"),
        BotCommand("monitor_off",   "⏹ Остановить мониторинг"),
        BotCommand("monitor",       "Статус мониторинга"),
        BotCommand("test_post",     "Опубликовать один тестовый пост"),
        BotCommand("run_autopilot", "Запустить автопилот (посты + ответы)"),
        BotCommand("run_replies",   "Только ответы на трендовые посты"),
        BotCommand("autopilot_on",  "Включить автопилот по расписанию"),
        BotCommand("autopilot_off", "Выключить автопилот"),
        BotCommand("autopilot",     "Статус автопилота"),
        BotCommand("decoy",         "🎭 Создать жалобный пост + ответить (искусственный трафик)"),
        BotCommand("check_search",   "Проверить поиск Threads"),
        BotCommand("instagram_post", "📸 Опубликовать пост в Instagram сейчас"),
        BotCommand("instagram_stats","📊 Последние Instagram посты"),
        BotCommand("clear",          "Очистить историю чата"),
        BotCommand("help",           "Список команд"),
    ]
    await app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())


def create_bot() -> Application:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env файле")

    # post_init передаётся в builder — так PTB гарантированно вызовет его при старте
    _app = (
        Application.builder()
        .token(token)
        .post_init(_setup_bot_commands)
        .build()
    )

    _app.add_handler(CommandHandler("start", start_command))
    _app.add_handler(CommandHandler("help", help_command))
    _app.add_handler(CommandHandler("reply_url", reply_url_command))
    _app.add_handler(CommandHandler("posts", posts_command))
    _app.add_handler(CommandHandler("clear", clear_command))
    _app.add_handler(CommandHandler("autopilot", autopilot_status_command))
    _app.add_handler(CommandHandler("autopilot_on", autopilot_on_command))
    _app.add_handler(CommandHandler("autopilot_off", autopilot_off_command))
    _app.add_handler(CommandHandler("test_post", test_post_command))
    _app.add_handler(CommandHandler("run_autopilot", run_autopilot_command))
    _app.add_handler(CommandHandler("run_replies", run_replies_command))
    _app.add_handler(CommandHandler("monitor", monitor_status_command))
    _app.add_handler(CommandHandler("monitor_on", monitor_on_command))
    _app.add_handler(CommandHandler("monitor_off", monitor_off_command))
    _app.add_handler(CommandHandler("decoy", decoy_command))
    _app.add_handler(CommandHandler("check_search", check_search_command))
    _app.add_handler(CommandHandler("instagram_post", instagram_post_command))
    _app.add_handler(CommandHandler("instagram_stats", instagram_stats_command))
    _app.add_handler(InlineQueryHandler(inline_query_handler))
    _app.add_handler(CallbackQueryHandler(callback_query_handler))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    from scheduler.scheduler import set_notify_fn
    set_notify_fn(notify)

    return _app
