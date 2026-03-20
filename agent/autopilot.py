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

from agent.skills.threads import post_text, reply_to_post, search_posts, get_my_username
from agent.skills.minprice import (
    search_prices, get_trending_products, get_best_deals,
    format_price_data_for_prompt, format_best_deals_for_prompt, SITE_LINK
)
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
    """
    Получает актуальные цены для контента:
    1. Best deals с minprice.kz (товары с максимальной выгодой)
    2. Случайные продукты из ежедневного списка
    """
    parts = []

    # 1. Best deals — лучший материал для вирусных постов
    try:
        deals = await get_best_deals(limit=8, min_score=0.10)
        if deals:
            parts.append(format_best_deals_for_prompt(deals))
    except Exception as e:
        logger.warning(f"Не смог получить best deals: {e}")

    # 2. Случайные продукты для разнообразия
    products_to_check = random.sample(DAILY_PRODUCTS, min(4, len(DAILY_PRODUCTS)))
    all_data = []
    for product in products_to_check:
        try:
            results = await search_prices(product, limit=3)
            all_data.extend(results)
        except Exception as e:
            logger.warning(f"Не смог получить цены на {product}: {e}")

    if all_data:
        parts.append(format_price_data_for_prompt(all_data))

    return "\n\n".join(parts) if parts else ""


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


async def run_replies_only(notify_fn=None, count: int = 10) -> dict:
    """Только ответы на чужие посты — без публикации своих"""
    settings = get_autopilot_settings()
    keywords = settings.get("keywords", ["цены на продукты", "цены в казахстане"])

    # Узнаём свой username чтобы не отвечать самому себе
    my_username = await get_my_username()
    logger.info(f"Мой username: {my_username}")

    if notify_fn:
        await notify_fn(f"Ищу посты по ключевым словам:\n{', '.join(keywords)}")

    candidate_posts = []
    search_errors = []
    seen_ids = set()
    for keyword in keywords:
        try:
            data = await search_posts(keyword, limit=20)
            if data.get("error"):
                err_msg = f"'{keyword}': {data['error']}"
                search_errors.append(err_msg)
            else:
                found = data.get("data", [])
                logger.info(f"Поиск '{keyword}': найдено {len(found)} постов")
                for p in found:
                    post_id = p.get("id")
                    username = p.get("username", "")
                    # Фильтруем: только чужие, только новые, только с текстом
                    if (post_id
                            and post_id not in seen_ids
                            and not is_already_replied(post_id)
                            and p.get("text")
                            and username != my_username):
                        candidate_posts.append(p)
                        seen_ids.add(post_id)
        except Exception as e:
            search_errors.append(f"'{keyword}': {e}")

    if search_errors and notify_fn:
        await notify_fn(f"⚠️ Ошибки поиска:\n" + "\n".join(search_errors))

    if not candidate_posts:
        note = "своих постов" if my_username else "постов"
        if notify_fn:
            await notify_fn(
                f"Не найдено чужих постов для ответов.\n"
                f"(Поиск вернул только {note} или результатов нет)"
            )
        return {"replies_published": 0, "errors": search_errors}

    # Показываем что нашли — для диагностики
    if notify_fn:
        sample = candidate_posts[:3]
        sample_info = "\n".join(
            [f"• @{p.get('username','?')}: {p.get('text','')[:60]}..." for p in sample]
        )
        await notify_fn(f"Найдено {len(candidate_posts)} чужих постов. Примеры:\n{sample_info}")

    candidate_posts.sort(
        key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0),
        reverse=True
    )
    to_reply = candidate_posts[:count]

    if notify_fn:
        await notify_fn(f"Найдено {len(to_reply)} постов. Начинаю отвечать...")

    results = {"replies_published": 0, "errors": []}

    for i, target in enumerate(to_reply):
        try:
            reply_text = await _generate_reply(target["text"])
            result = await reply_to_post(target["id"], reply_text)

            if result.get("success"):
                mark_replied(target["id"])
                results["replies_published"] += 1
                if notify_fn:
                    await notify_fn(
                        f"✅ Ответ {i+1}/{len(to_reply)} → @{target.get('username','?')}:\n"
                        f"{reply_text[:120]}..."
                    )
            else:
                err = result.get("error", "неизвестная ошибка")
                results["errors"].append(f"Ответ {i+1}: {err}")
                if notify_fn:
                    await notify_fn(f"❌ Ответ {i+1} не удался: {err}")
        except Exception as e:
            results["errors"].append(f"Ответ {i+1}: {e}")

        if i < len(to_reply) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            await asyncio.sleep(delay)

    if notify_fn:
        await notify_fn(
            f"Ответы завершены!\n"
            f"Опубликовано: {results['replies_published']}/{len(to_reply)}\n"
            + (f"Ошибок: {len(results['errors'])}" if results["errors"] else "")
        )
    return results


async def run_autopilot(notify_fn=None, force: bool = False) -> dict:
    """Главная функция автопилота"""
    settings = get_autopilot_settings()
    if not force and not settings.get("enabled"):
        return {"skipped": "автопилот выключен"}

    niche = settings.get("niche", "цены на продукты и товары в Казахстане")
    keywords = settings.get("keywords", ["цены на продукты", "цены в казахстане"])
    own_count = settings.get("own_posts_count", 5)
    reply_count = settings.get("reply_posts_count", 10)

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
    my_username = await get_my_username()
    logger.info(f"Мой username: {my_username}")

    candidate_posts = []
    search_errors = []
    seen_ids = set()
    for keyword in keywords:
        try:
            data = await search_posts(keyword, limit=15)
            if data.get("error"):
                err_msg = f"Поиск '{keyword}': {data['error']}"
                logger.warning(err_msg)
                search_errors.append(err_msg)
            else:
                found = data.get("data", [])
                logger.info(f"Поиск '{keyword}': найдено {len(found)} постов")
                for p in found:
                    post_id = p.get("id")
                    username = p.get("username", "")
                    if (post_id
                            and post_id not in seen_ids
                            and not is_already_replied(post_id)
                            and p.get("text")
                            and username != my_username):
                        candidate_posts.append(p)
                        seen_ids.add(post_id)
        except Exception as e:
            logger.warning(f"Поиск '{keyword}': {e}")
            search_errors.append(str(e))

    # Сортируем по вовлечённости
    candidate_posts.sort(
        key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0),
        reverse=True
    )
    to_reply = candidate_posts[:reply_count]

    if not to_reply:
        if search_errors:
            msg = f"Ошибка поиска постов:\n{chr(10).join(search_errors[:2])}"
        else:
            msg = "Нет новых постов для ответов"
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
