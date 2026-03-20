"""
OpenClaw агент — Claude (primary) + Gemini (fallback)
Обрабатывает сообщения от Telegram бота и выполняет действия через инструменты
"""
import anthropic
from google import genai as google_genai
import json
import os
import logging
from database.db import get_history, save_message, log_action, save_scheduled_post
from agent.skills.threads import (
    post_text, post_with_image, reply_to_post,
    get_my_posts, get_insights
)
from agent.skills.marketing import get_marketing_context, get_content_plan_prompt

logger = logging.getLogger(__name__)

# Ленивая инициализация клиентов (чтобы .env успел загрузиться до первого вызова)
_anthropic_client = None
_gemini_client = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        key = os.getenv("GEMINI_API_KEY")
        if key:
            _gemini_client = google_genai.Client(api_key=key)
    return _gemini_client

SITE_LINK = "https://minprice.kz/?th"

SYSTEM_PROMPT = f"""Ты — OpenClaw, AI-агент аккаунта @minimalprice_kz в Threads.
Ниша: сравнение цен на продукты в Казахстане. Сайт: {SITE_LINK}

ПРАВИЛО №1 — ОБЯЗАТЕЛЬНО: каждый пост и каждый ответ который ты публикуешь ДОЛЖЕН содержать ссылку {SITE_LINK}
Вставляй её органично в конце поста. Например: "Все цены сравниваем тут: {SITE_LINK}"

СТИЛЬ ПОСТОВ — вирусный, честный, немного провокационный:
- Используй реальные цифры из minprice.kz — посты с конкретными ценами набирают в 3x больше охвата
- Первая строка = крючок. Примеры хорошего крючка:
  "Бананы в Магнуме стоят 999₸. В Airba Fresh — 750₸. Это не ошибка."
  "Как магазины зарабатывают на том что вы не сравниваете цены"
  "Я проверил 4 магазина. Разница на молоко — 340₸. За месяц это 10 000₸"
- Конкретика > абстракция. "999₸ vs 650₸" лучше чем "цены отличаются"
- Лёгкое возмущение работает: люди делятся тем что их удивляет или злит
- Вопросы в конце увеличивают комменты
- Без хэштегов (в Threads они не работают)
- Длина: 2-5 предложений + ссылка

Инструменты: публикация постов, ответы, планирование, статистика.
Когда просят написать пост — сначала покажи текст, потом спроси "публиковать?".
Если подтверждают — публикуй сразу.
Отвечай на языке пользователя. Будь кратким.
"""

# Инструменты для Claude tool use
TOOLS = [
    {
        "name": "publish_text_post",
        "description": "Опубликовать текстовый пост в Threads от имени пользователя",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст поста для публикации в Threads"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "publish_image_post",
        "description": "Опубликовать пост с изображением в Threads",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст поста"},
                "image_url": {"type": "string", "description": "Публичная ссылка на изображение (JPEG/PNG)"}
            },
            "required": ["text", "image_url"]
        }
    },
    {
        "name": "reply_to_threads_post",
        "description": "Ответить на конкретный пост в Threads",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "ID поста на который отвечаем"},
                "text": {"type": "string", "description": "Текст ответа"}
            },
            "required": ["post_id", "text"]
        }
    },
    {
        "name": "get_recent_threads_posts",
        "description": "Получить последние посты пользователя из Threads с лайками и статистикой",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Количество постов (по умолчанию 10)", "default": 10}
            }
        }
    },
    {
        "name": "get_post_insights",
        "description": "Получить подробную статистику конкретного поста в Threads",
        "input_schema": {
            "type": "object",
            "properties": {
                "media_id": {"type": "string", "description": "ID поста в Threads"}
            },
            "required": ["media_id"]
        }
    },
    {
        "name": "schedule_post",
        "description": "Запланировать пост для публикации в конкретное время",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст поста"},
                "scheduled_for": {"type": "string", "description": "Дата и время публикации в формате YYYY-MM-DD HH:MM"},
                "image_url": {"type": "string", "description": "Необязательная ссылка на изображение"}
            },
            "required": ["text", "scheduled_for"]
        }
    },
    {
        "name": "generate_content_plan",
        "description": "Создать контент-план для Threads на указанный период",
        "input_schema": {
            "type": "object",
            "properties": {
                "niche": {"type": "string", "description": "Ниша/тема аккаунта"},
                "period": {"type": "string", "description": "Период плана (например: неделю, месяц)", "default": "неделю"},
                "goal": {"type": "string", "description": "Цель контент-стратегии", "default": "рост аудитории"}
            },
            "required": ["niche"]
        }
    }
]


async def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Выполнить инструмент и вернуть результат строкой"""
    log_action(f"tool:{tool_name}", json.dumps(tool_input, ensure_ascii=False)[:300])

    try:
        if tool_name == "publish_text_post":
            result = await post_text(tool_input["text"])

        elif tool_name == "publish_image_post":
            result = await post_with_image(tool_input["text"], tool_input["image_url"])

        elif tool_name == "reply_to_threads_post":
            result = await reply_to_post(tool_input["post_id"], tool_input["text"])

        elif tool_name == "get_recent_threads_posts":
            result = await get_my_posts(tool_input.get("limit", 10))

        elif tool_name == "get_post_insights":
            result = await get_insights(tool_input["media_id"])

        elif tool_name == "schedule_post":
            post_id = save_scheduled_post(
                text=tool_input["text"],
                scheduled_for=tool_input["scheduled_for"],
                media_url=tool_input.get("image_url")
            )
            result = {"success": True, "scheduled_post_id": post_id, "scheduled_for": tool_input["scheduled_for"]}

        elif tool_name == "generate_content_plan":
            prompt = get_content_plan_prompt(
                niche=tool_input["niche"],
                period=tool_input.get("period", "неделю"),
                goal=tool_input.get("goal", "рост аудитории")
            )
            result = {"context": prompt}

        else:
            result = {"error": f"Неизвестный инструмент: {tool_name}"}

        log_action(f"tool_ok:{tool_name}", None, str(result)[:300])
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        log_action(f"tool_err:{tool_name}", None, str(e))
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _run_with_claude(messages: list, system: str) -> str:
    """
    Запустить агента через Claude с tool use loop.
    Возвращает финальный текстовый ответ.
    """
    total_input_tokens = 0
    total_output_tokens = 0

    while True:
        response = _get_anthropic().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        # Считаем токены
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        text_parts = []
        tool_calls = []

        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        # Финальный ответ — нет больше tool calls
        if response.stop_reason == "end_turn" or not tool_calls:
            # Логируем стоимость ($3/1M input, $15/1M output для Sonnet)
            cost_usd = (total_input_tokens / 1_000_000 * 3) + (total_output_tokens / 1_000_000 * 15)
            log_action(
                "tokens_used",
                f"in={total_input_tokens} out={total_output_tokens}",
                f"~${cost_usd:.4f}"
            )
            text = "\n".join(text_parts) if text_parts else "Готово."
            return text, cost_usd

        # Добавляем ответ ассистента
        messages.append({"role": "assistant", "content": response.content})

        # Выполняем инструменты и добавляем результаты
        tool_results = []
        for tc in tool_calls:
            result = await execute_tool(tc.name, tc.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result
            })
        messages.append({"role": "user", "content": tool_results})


async def _run_with_gemini(user_message: str, history: list, system: str) -> str:
    """
    Fallback через Gemini (без tool use — генерация контента и советы).
    При Gemini не публикуем посты автоматически, только генерируем текст.
    """
    client = _get_gemini()
    if not client:
        return "Ошибка: не настроен GEMINI_API_KEY в .env"

    # Собираем историю в один контекст
    history_text = ""
    for msg in history[:-1]:
        role = "Пользователь" if msg["role"] == "user" else "Агент"
        history_text += f"{role}: {msg['content']}\n\n"

    full_prompt = (
        system
        + "\n\nВАЖНО: В режиме fallback ты можешь только генерировать и анализировать контент. "
        "Публикация постов недоступна — сообщи об этом пользователю если он просит опубликовать.\n\n"
        + (f"История диалога:\n{history_text}\n" if history_text else "")
        + f"Пользователь: {user_message}\nАгент:"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=full_prompt,
    )
    return response.text


async def process_message(user_message: str) -> dict:
    """
    Основная точка входа — обработать сообщение пользователя.
    Возвращает {"text": str, "cost_usd": float | None, "model": str}
    """
    save_message("user", user_message)
    history = get_history(limit=30)
    system = SYSTEM_PROMPT + "\n\n" + get_marketing_context()

    # Пробуем Claude (primary)
    try:
        logger.info("Обработка через Claude...")
        text, cost_usd = await _run_with_claude(list(history), system)
        save_message("assistant", text)
        return {"text": text, "cost_usd": cost_usd, "model": "claude-sonnet-4-6"}

    except Exception as claude_err:
        logger.warning(f"Claude недоступен ({claude_err}), переключаюсь на Gemini...")
        log_action("fallback_to_gemini", str(claude_err))

        # Gemini fallback
        try:
            text = await _run_with_gemini(user_message, history, system)
            full_text = f"[Gemini fallback]\n\n{text}"
            save_message("assistant", full_text)
            return {"text": full_text, "cost_usd": None, "model": "gemini-2.5-flash"}

        except Exception as gemini_err:
            logger.error(f"Gemini тоже недоступен: {gemini_err}")
            error_msg = f"Оба AI недоступны.\nClaude: {claude_err}\nGemini: {gemini_err}"
            save_message("assistant", error_msg)
            return {"text": error_msg, "cost_usd": None, "model": "error"}
