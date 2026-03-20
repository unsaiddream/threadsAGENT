"""
Автопилот — каждый день автоматически:
1. Берёт реальные цены с minprice.kz
2. Генерирует 5 вирусных постов с конкретными цифрами + ссылкой
3. Ищет посты по ключевым словам и пишет 5 умных ответов со ссылкой
Паузы между публикациями — имитируем живого человека.
"""
import asyncio
import logging
import random
import anthropic
import os

from agent.skills.threads import post_text, reply_to_post, search_posts
from agent.skills.minprice import search_prices, get_trending_products, format_price_data_for_prompt, SITE_LINK
from database.db import (
    get_autopilot_settings, update_autopilot_settings,
    is_already_replied, mark_replied, log_action
)
from datetime import datetime

logger = logging.getLogger(__name__)

MIN_DELAY_SEC = 30
MAX_DELAY_SEC = 90

# Продукты для мониторинга цен каждый день
DAILY_PRODUCTS = [
    "бананы", "молоко", "яйца", "хлеб", "помидоры",
    "огурцы", "курица", "говядина", "масло", "гречка",
    "рис", "сахар", "картофель", "лук", "морковь"
]


def _claude():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def _fetch_price_context() -> str:
    """Получает актуальные цены на 3-5 случайных продуктов для контента"""
    products_to_check = random.sample(DAILY_PRODUCTS, min(5, len(DAILY_PRODUCTS)))
    all_data = []

    for product in products_to_check:
        try:
            results = await search_prices(product, limit=3)
            all_data.extend(results)
        except Exception as e:
            logger.warning(f"Не смог получить цены на {product}: {e}")

    return format_price_data_for_prompt(all_data) if all_data else ""


async def _generate_own_posts(price_context: str, niche: str, count: int) -> list[str]:
    """Генерирует N вирусных постов на основе реальных цен"""

    prompt = f"""Ты — автор @minimalprice_kz в Threads. Сайт: {SITE_LINK}

Вот РЕАЛЬНЫЕ данные о ценах прямо сейчас:
{price_context}

Напиши {count} РАЗНЫХ вирусных постов используя эти данные. Правила:

ОБЯЗАТЕЛЬНО:
- Каждый пост ДОЛЖЕН заканчиваться ссылкой {SITE_LINK}
- Используй конкретные цифры из данных выше (люди реагируют на конкретику)
- Каждый пост про РАЗНЫЙ продукт или угол зрения

ФОРМАТЫ (используй разные):
1. Шок-факт: "Бананы в Магнуме 999₸. В Airba — 750₸. Разница 249₸ на кг. За месяц семья теряет 1 000₸+"
2. Провокация: "Почему Магнум дороже Арбуза на одни и те же яйца? Я знаю ответ."
3. Лайфхак: "Правило которое экономит 15 000₸ в месяц: [совет]. Проверяй цены тут:"
4. Сравнение: "[Продукт]: дешевле всего в [магазин], дороже всего в [магазин]. Разница [X]₸"
5. Вопрос: "Вы всё ещё покупаете [продукт] в [дорогом магазине]? Покажу где на [X]₸ дешевле:"

СТИЛЬ: разговорный, честный, немного возмутительный. Без хэштегов. 2-4 предложения + ссылка.

Верни ТОЛЬКО {count} постов, каждый отделён строкой "---"
"""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    # Считаем токены
    usage = response.usage
    cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
    log_action("autopilot_tokens_own", f"in={usage.input_tokens} out={usage.output_tokens}", f"~${cost:.4f}")

    raw = response.content[0].text
    posts = [p.strip() for p in raw.split("---") if p.strip()]

    # Убеждаемся что ссылка есть в каждом посте
    final = []
    for p in posts[:count]:
        if SITE_LINK not in p:
            p = p.rstrip() + f"\n\nВсе цены: {SITE_LINK}"
        final.append(p)

    return final


async def _generate_reply(target_text: str) -> str:
    """Генерирует ответ на чужой пост — полезный + ссылка"""

    prompt = f"""Ты автор @minimalprice_kz — сайта сравнения цен в Казахстане ({SITE_LINK}).

Чужой пост:
"{target_text}"

Напиши короткий ответ (1-3 предложения) который:
- Добавляет ценную инфу про цены/экономию в Казахстане
- Органично упоминает или прикрепляет ссылку {SITE_LINK}
- Звучит как живой человек, не реклама
- Если пост про конкретный продукт — дай конкретный факт о ценах на него

ОБЯЗАТЕЛЬНО включи ссылку {SITE_LINK} в ответ.
Верни ТОЛЬКО текст ответа.
"""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    # Страховка — если ссылки нет, добавляем
    if SITE_LINK not in text:
        text = text.rstrip(".") + f". Цены: {SITE_LINK}"
    return text


async def run_autopilot(notify_fn=None) -> dict:
    """Главная функция автопилота"""
    settings = get_autopilot_settings()
    if not settings.get("enabled"):
        return {"skipped": "автопилот выключен"}

    niche = settings.get("niche", "цены на продукты и товары в Казахстане")
    keywords = settings.get("keywords", ["цены на продукты", "цены в казахстане"])
    own_count = settings.get("own_posts_count", 5)
    reply_count = settings.get("reply_posts_count", 5)

    results = {"own_published": 0, "replies_published": 0, "errors": []}

    if notify_fn:
        await notify_fn(f"Автопилот запустился\nПлан: {own_count} постов + {reply_count} ответов\nЗагружаю цены с minprice.kz...")

    log_action("autopilot_start", f"own={own_count} replies={reply_count}")

    # ── Получаем реальные цены ─────────────────────────────
    price_context = await _fetch_price_context()
    if notify_fn and price_context:
        await notify_fn(f"Данные о ценах получены. Генерирую посты...")

    # ── 1. Свои посты ─────────────────────────────────────
    try:
        own_posts = await _generate_own_posts(price_context, niche, own_count)
    except Exception as e:
        logger.error(f"Ошибка генерации постов: {e}")
        results["errors"].append(f"Генерация: {e}")
        own_posts = []

    for i, post in enumerate(own_posts):
        try:
            result = await post_text(post)
            if result.get("success"):
                results["own_published"] += 1
                if notify_fn:
                    await notify_fn(f"Пост {i+1}/{own_count}:\n{post[:150]}...")
            else:
                results["errors"].append(f"Пост {i+1}: {result.get('error')}")
        except Exception as e:
            results["errors"].append(f"Пост {i+1}: {e}")

        if i < len(own_posts) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            if notify_fn:
                await notify_fn(f"Пауза {delay//60} мин перед следующим постом...")
            await asyncio.sleep(delay)

    # ── 2. Ответы на чужие посты ──────────────────────────
    candidate_posts = []
    for keyword in keywords:
        try:
            data = await search_posts(keyword, limit=15)
            for p in data.get("data", []):
                if p.get("id") and not is_already_replied(p["id"]) and p.get("text"):
                    candidate_posts.append(p)
        except Exception as e:
            logger.warning(f"Поиск '{keyword}': {e}")

    # Сортируем по вовлечённости
    candidate_posts.sort(
        key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0),
        reverse=True
    )
    to_reply = candidate_posts[:reply_count]

    if not to_reply:
        msg = "Нет новых постов для ответов (нужен пермишен threads_keyword_search)"
        if notify_fn:
            await notify_fn(msg)
    else:
        for i, target in enumerate(to_reply):
            try:
                delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
                if notify_fn:
                    await notify_fn(f"Пауза {delay//60} мин перед ответом {i+1}...")
                await asyncio.sleep(delay)

                reply_text = await _generate_reply(target["text"])
                result = await reply_to_post(target["id"], reply_text)

                if result.get("success"):
                    mark_replied(target["id"])
                    results["replies_published"] += 1
                    if notify_fn:
                        await notify_fn(f"Ответ {i+1}/{reply_count} на @{target.get('username','?')}:\n{reply_text[:120]}...")
                else:
                    results["errors"].append(f"Ответ {i+1}: {result.get('error')}")
            except Exception as e:
                results["errors"].append(f"Ответ {i+1}: {e}")

    update_autopilot_settings(last_run=datetime.now().isoformat())
    log_action("autopilot_done", None, str(results))

    summary = (
        f"Автопилот завершён!\n"
        f"Постов: {results['own_published']}/{own_count}\n"
        f"Ответов: {results['replies_published']}/{reply_count}"
    )
    if results["errors"]:
        summary += f"\nОшибок: {len(results['errors'])}"
    if notify_fn:
        await notify_fn(summary)

    return results
