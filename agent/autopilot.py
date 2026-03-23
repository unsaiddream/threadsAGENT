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

from agent.skills.threads import post_text, post_with_image, reply_to_post, get_my_posts, get_post_replies, get_my_username
from agent.skills.threads_scraper import search_trending_posts, reply_via_browser
from agent.skills.minprice import (
    search_prices, get_trending_products, get_best_deals,
    get_multi_store_products, get_price_drops,
    format_product_for_prompt, product_link, product_search_link,
    SITE_LINK
)
from database.db import (
    get_autopilot_settings, update_autopilot_settings,
    is_already_replied, mark_replied, log_action
)
from datetime import datetime

logger = logging.getLogger(__name__)

MIN_DELAY_SEC = 45
MAX_DELAY_SEC = 120

# Продукты для мониторинга цен каждый день
DAILY_PRODUCTS = [
    "бананы", "молоко", "яйца", "хлеб", "помидоры",
    "огурцы", "курица", "говядина", "масло", "гречка",
    "рис", "сахар", "картофель", "лук", "морковь"
]


def _claude():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def _fetch_deals_and_products() -> list[dict]:
    """
    Загружает лучшие товары для постов из 3 источников:
    1. Best deals — товары с макс. выгодой
    2. Price drops — товары у которых цена упала
    3. Multi-store — товары в нескольких магазинах с большим разбросом
    Каждый товар уже содержит uuid, image_url, link, stores, best_drop.
    """
    items = []
    seen_uuids = set()

    def _add(products):
        for p in products:
            uid = p.get("uuid", "")
            if uid and uid not in seen_uuids:
                seen_uuids.add(uid)
                items.append(p)

    # 1. Best deals
    try:
        _add(await get_best_deals(limit=8, min_score=0.10))
    except Exception as e:
        logger.warning(f"best_deals: {e}")

    # 2. Price drops — "было X₸ → стало Y₸"
    try:
        _add(await get_price_drops(limit=5))
    except Exception as e:
        logger.warning(f"price_drops: {e}")

    # 3. Multi-store comparisons
    try:
        _add(await get_multi_store_products(limit=5))
    except Exception as e:
        logger.warning(f"multi_store: {e}")

    random.shuffle(items)
    return items


async def _generate_own_posts(price_context: str, niche: str, count: int) -> list[dict]:
    """
    Генерирует N вирусных постов.
    Возвращает list[dict] с ключами: text, image_url (может быть None).
    """

    items = await _fetch_deals_and_products()
    if not items:
        logger.error("Нет данных о ценах для постов")
        return []

    posts = []
    for i in range(min(count, len(items))):
        item = items[i]
        deal_info = format_product_for_prompt(item)

        has_drop = bool(item.get("best_drop"))
        has_spread = item.get("spread_pct", 0) > 15

        if has_drop:
            angle = "ЦЕНА УПАЛА — покажи было/стало и где именно"
        elif has_spread:
            angle = "РАЗНИЦА ЦЕН между магазинами — покажи где дешевле, а где переплата"
        else:
            angle = "ФАКТ ПРО ЦЕНУ — удиви конкретной цифрой"

        prompt = f"""Ты — @minimalprice_kz в Threads. Пишешь про цены на продукты в Казахстане.

ТОВАР:
{deal_info}

УГОЛ ПОСТА: {angle}

Напиши один короткий вирусный пост (100-200 символов + ссылка).

Правила:
- Первая строка = крючок: шок-факт, вопрос, или провокация
- КОНКРЕТНЫЕ цифры и названия магазинов из данных
- Ссылка: {item['link']} — один раз, в конце
- Вопрос в конце → люди отвечают → охват растёт
- 3-4 хештега: #цены #Казахстан #экономия #продукты #Алматы #инфляция #лайфхак #тенге
- Пиши как человек, не как маркетолог

Верни ТОЛЬКО текст поста.
"""

        try:
            response = _claude().messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )

            usage = response.usage
            cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
            _generate_own_posts.last_cost = getattr(_generate_own_posts, "last_cost", 0) + cost

            text = response.content[0].text.strip()

            if item["link"] not in text and SITE_LINK not in text:
                text = text.rstrip() + f"\n\n{item['link']}"

            # Картинка товара — посты с картинкой получают на 60% больше охвата
            image = item.get("image_url") or None
            # Чистим url картинки от placeholder параметров
            if image and "%w" in image:
                image = image.replace("%w", "400").replace("%h", "400")

            posts.append({"text": text, "image_url": image})
        except Exception as e:
            logger.error(f"Ошибка генерации поста {i+1}: {e}")

    log_action("autopilot_tokens_own", f"posts={len(posts)}", f"~${getattr(_generate_own_posts, 'last_cost', 0):.4f}")
    return posts


async def _do_reply(target: dict, reply_text: str) -> dict:
    """
    Отправляет ответ:
    - Если via_browser=True (shortcode из DOM) → Playwright браузерный reply
    - Иначе → официальный Threads API reply (по реальному pk)
    """
    if target.get("via_browser") and target.get("post_url"):
        return await reply_via_browser(target["post_url"], reply_text)
    else:
        return await reply_to_post(target["id"], reply_text)


def _reply_notify_text(idx: int, total: int, target: dict, reply_text: str, result: dict) -> str:
    """
    Формирует HTML-уведомление об ответе.
    🌐 — кликабельная ссылка на пост куда ответили.
    """
    import html as _html
    username = target.get("username", "?")
    target_url = target.get("post_url", "")
    reply_permalink = result.get("permalink") or target_url

    if reply_permalink:
        mode_icon = f'<a href="{reply_permalink}">🌐</a>'
    else:
        mode_icon = "🌐" if target.get("via_browser") else "📡"

    safe_text = _html.escape(reply_text)
    return (
        f"✅ {mode_icon} Ответ {idx}/{total} → @{username}:\n"
        f"{safe_text}"
    )


# Слова-маркеры: пост ОБЯЗАН содержать хотя бы одно чтобы считаться релевантным
RELEVANCE_KEYWORDS = [
    "цен", "цена", "цены", "дорог", "дешев", "подорожа", "стоит", "стоимост",
    "магазин", "магнум", "small", "арзан", "анвар", "galmart",
    "переплат", "экономи", "скидк", "акци", "бюджет",
    "продукт", "молок", "хлеб", "яйц", "масл", "мясо", "куриц",
    "овощ", "фрукт", "банан", "помидор", "картош", "сахар",
    "гречк", "рис ", "корзин", "чек ", "тенге", "₸",
]


def _is_post_relevant(text: str) -> bool:
    """Быстрая проверка релевантности поста БЕЗ вызова AI"""
    text_lower = text.lower()
    matches = sum(1 for kw in RELEVANCE_KEYWORDS if kw in text_lower)
    return matches >= 2  # минимум 2 совпадения


def _extract_product_keywords(text: str) -> list[str]:
    """Извлекает названия продуктов из текста поста для поиска на minprice.kz"""
    product_map = {
        "молок": "молоко", "хлеб": "хлеб", "яйц": "яйца", "масл": "масло",
        "мясо": "мясо", "куриц": "курица", "говядин": "говядина",
        "банан": "бананы", "помидор": "помидоры", "огурц": "огурцы",
        "картош": "картофель", "сахар": "сахар", "гречк": "гречка",
        "рис ": "рис", "лук ": "лук", "морков": "морковь",
        "овощ": "овощи", "фрукт": "фрукты", "творог": "творог",
        "сметан": "сметана", "кефир": "кефир", "сыр ": "сыр",
        "колбас": "колбаса", "макарон": "макароны", "мук": "мука",
    }
    text_lower = text.lower()
    found = []
    for marker, product in product_map.items():
        if marker in text_lower and product not in found:
            found.append(product)
    return found[:3]  # максимум 3 продукта


async def _fetch_prices_for_reply(product_keywords: list[str]) -> tuple[str, str]:
    """
    Загружает реальные цены для продуктов из поста.
    Возвращает (текст_для_промпта, лучшая_ссылка_на_товар).
    """
    if not product_keywords:
        return "", SITE_LINK

    lines = []
    best_link = SITE_LINK
    for kw in product_keywords:
        try:
            results = await search_prices(kw, limit=2)
            for r in results:
                stores = r.get("stores", [])[:3]
                store_info = ", ".join(f"{s['store']}: {s['price']:.0f}₸" for s in stores)
                lines.append(f"{r['title']}: {store_info} → {r['link']}")
                if best_link == SITE_LINK:
                    best_link = r["link"]  # uuid-ссылка на первый найденный товар
        except Exception as e:
            logger.warning(f"Цены для reply ({kw}): {e}")

    return "\n".join(lines), best_link


async def _generate_reply(target_text: str) -> str | None:
    """
    Генерирует умный контекстный ответ на чужой пост.

    1. Код проверяет релевантность (без AI)
    2. Извлекает продукты из текста
    3. Загружает реальные цены с minprice.kz
    4. Claude пишет ответ с конкретными фактами и ссылкой на продукт
    """

    # Шаг 1: Проверяем релевантность без AI
    if not _is_post_relevant(target_text):
        logger.info(f"SKIP (код): нет ключевых слов в посте")
        return None

    # Шаг 2: Какие продукты упоминаются?
    products = _extract_product_keywords(target_text)

    # Шаг 3: Грузим реальные цены и получаем uuid-ссылку
    price_context, link = await _fetch_prices_for_reply(products)

    prompt = f"""Чужой пост в Threads:
"{target_text}"

Ты ведёшь аккаунт @minimalprice_kz — сервис сравнения цен на продукты по магазинам Казахстана.

{"РЕАЛЬНЫЕ ЦЕНЫ ПРЯМО СЕЙЧАС:" + chr(10) + price_context + chr(10) if price_context else ""}
Напиши ответ к этому посту:
- Используй КОНКРЕТНЫЕ цифры из данных выше если они есть
- Ссылка на продукт: {link}
- 1-2 предложения максимум, разговорный тон
- Звучи как обычный человек который делится находкой, НЕ как бот
- НЕ начинай с "Кстати", "Интересно", "О,"

Верни ТОЛЬКО текст ответа. Без объяснений.
"""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )

    usage = response.usage
    reply_cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
    _generate_reply.total_cost = getattr(_generate_reply, "total_cost", 0) + reply_cost

    text = response.content[0].text.strip()

    # Защита от вывода рассуждений
    if text.upper().startswith("SKIP"):
        return None
    if "---" in text:
        text = text.split("---")[-1].strip()
    if not text:
        return None

    return text


async def _collect_reply_candidates(keywords: list[str], count: int) -> list[dict]:
    """
    Ищет трендовые посты по ключевым словам через Playwright scraper.
    Fallback: комментарии под своими постами если scraper не нашёл ничего.
    """
    my_username = await get_my_username()

    # 1. Ищем чужие посты через браузер
    scraped = await search_trending_posts(keywords, limit=count * 2)

    # Фильтруем: только чужие, не отвеченные, и РЕЛЕВАНТНЫЕ (про цены/продукты)
    candidates = [
        p for p in scraped
        if not is_already_replied(p["id"])
        and p.get("username") != my_username
        and _is_post_relevant(p.get("text", ""))
    ]

    if candidates:
        logger.info(f"Scraper нашёл {len(candidates)} постов для ответов")
        return candidates[:count]

    # 2. Fallback: отвечаем на комментарии под своими постами
    logger.info("Scraper не нашёл постов — используем комментарии под своими постами")
    posts_data = await get_my_posts(limit=20)
    my_posts = posts_data.get("data", [])
    posts_with_replies = [p for p in my_posts if (p.get("replies_count") or 0) > 0]

    fallback = []
    seen_ids = set()
    for post in posts_with_replies:
        if len(fallback) >= count:
            break
        try:
            replies_data = await get_post_replies(post["id"], limit=20)
            for r in replies_data.get("data", []):
                rid = r.get("id")
                if (rid and rid not in seen_ids
                        and not is_already_replied(rid)
                        and r.get("text")
                        and r.get("username") != my_username):
                    r["_parent_post_text"] = post.get("text", "")
                    fallback.append(r)
                    seen_ids.add(rid)
        except Exception as e:
            logger.warning(f"Fallback replies error {post['id']}: {e}")

    return fallback[:count]


async def run_replies_only(notify_fn=None, count: int = 10) -> dict:
    """
    Ищет трендовые посты по ключевым словам через Playwright scraper
    и пишет контекстные комментарии со ссылкой на сайт.
    """
    settings = get_autopilot_settings()
    keywords = settings.get("keywords", [
        "цены на продукты", "цены в казахстане", "продукты дорожают",
        "продукты", "овощи фрукты", "магазин цены", "дорогие продукты", "дорожает"
    ])

    if notify_fn:
        await notify_fn(f"🔍 Ищу трендовые посты:\n{', '.join(keywords)}", topic="replies")

    candidates = await _collect_reply_candidates(keywords, count)

    if not candidates:
        if notify_fn:
            await notify_fn("Не найдено постов для ответов.", topic="replies")
        return {"replies_published": 0, "errors": []}

    if notify_fn:
        sample_info = "\n".join(
            [f"• @{p.get('username','?')}: {p.get('text','')[:60]}..." for p in candidates[:3]]
        )
        await notify_fn(f"Найдено {len(candidates)} постов:\n{sample_info}", topic="replies")

    results = {"replies_published": 0, "errors": []}

    replied_count = 0
    for i, target in enumerate(candidates):
        try:
            context = target.get("_parent_post_text", "")
            combined_text = f"{target['text']}\n[пост: {context[:100]}]" if context else target["text"]
            reply_text = await _generate_reply(combined_text)

            if reply_text is None:
                mark_replied(target["id"])
                continue

            result = await _do_reply(target, reply_text)

            if result.get("success"):
                mark_replied(target["id"])
                results["replies_published"] += 1
                replied_count += 1
                if notify_fn:
                    msg = _reply_notify_text(replied_count, len(candidates), target, reply_text, result)
                    await notify_fn(msg, topic="replies")
            else:
                err = result.get("error", "?")
                results["errors"].append(f"Ответ {i+1}: {err}")
                if notify_fn:
                    await notify_fn(f"❌ Ответ {i+1}:\n{err[:500]}", topic="errors")
        except Exception as e:
            results["errors"].append(f"Ответ {i+1}: {repr(e)}")
            if notify_fn:
                await notify_fn(f"❌ Ответ {i+1}:\n{repr(e)}", topic="errors")

        if i < len(candidates) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            await asyncio.sleep(delay)

    if notify_fn:
        await notify_fn(
            f"Ответы завершены!\n"
            f"Опубликовано: {results['replies_published']}/{len(candidates)}\n"
            + (f"Ошибок: {len(results['errors'])}" if results["errors"] else ""),
            topic="summary"
        )
    return results


async def run_autopilot(notify_fn=None, force: bool = False) -> dict:
    """Главная функция автопилота"""
    settings = get_autopilot_settings()
    if not force and not settings.get("enabled"):
        return {"skipped": "автопилот выключен"}

    niche = settings.get("niche", "цены на продукты и товары в Казахстане")
    keywords = settings.get("keywords", [
        "цены на продукты", "цены в казахстане", "продукты дорожают",
        "продукты", "овощи фрукты", "магазин цены", "дорогие продукты", "дорожает"
    ])
    own_count = settings.get("own_posts_count", 5)
    reply_count = settings.get("reply_posts_count", 10)

    results = {"own_published": 0, "replies_published": 0, "errors": []}

    if notify_fn:
        await notify_fn(
            f"Автопилот запустился\nПлан: {own_count} постов + {reply_count} ответов\nЗагружаю цены с minprice.kz...",
            topic="summary"
        )

    log_action("autopilot_start", f"own={own_count} replies={reply_count}")

    # ── 1. Свои посты (внутри загружаются deals + prices) ──
    try:
        own_posts = await _generate_own_posts("", niche, own_count)
    except Exception as e:
        logger.error(f"Ошибка генерации постов: {e}")
        results["errors"].append(f"Генерация: {e}")
        own_posts = []
        if notify_fn:
            await notify_fn(f"❌ Генерация постов: {repr(e)}", topic="errors")

    for i, post_data in enumerate(own_posts):
        try:
            text = post_data["text"]
            image_url = post_data.get("image_url")

            # Публикуем с картинкой, fallback на текст
            result = None
            post_type = "📝"
            if image_url:
                result = await post_with_image(text, image_url)
                post_type = "🖼"
                # Если картинка не прошла — пробуем текстом
                if not result.get("success"):
                    logger.warning(f"Image post failed, fallback to text: {result.get('error')}")
                    result = await post_text(text)
                    post_type = "📝"
            else:
                result = await post_text(text)

            if result.get("success"):
                results["own_published"] += 1
                if notify_fn:
                    permalink = result.get("permalink") or ""
                    link_line = f"\n🔗 {permalink}" if permalink else ""
                    await notify_fn(
                        f"{post_type} Пост {i+1}/{own_count}:\n{text[:200]}...{link_line}",
                        topic="posts"
                    )
            else:
                err = result.get("error", "неизвестная ошибка")
                results["errors"].append(f"Пост {i+1}: {err}")
                if notify_fn:
                    await notify_fn(f"❌ Пост {i+1}:\n{err[:500]}", topic="errors")
        except Exception as e:
            results["errors"].append(f"Пост {i+1}: {repr(e)}")
            if notify_fn:
                await notify_fn(f"❌ Пост {i+1} exception:\n{repr(e)}", topic="errors")

        if i < len(own_posts) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            await asyncio.sleep(delay)

    # ── 2. Ищем трендовые посты и пишем контекстные ответы ─
    if notify_fn:
        await notify_fn(f"🔍 Ищу трендовые посты:\n{', '.join(keywords)}", topic="replies")

    to_reply = await _collect_reply_candidates(keywords, reply_count)

    if not to_reply:
        if notify_fn:
            await notify_fn("Не найдено постов для ответов.", topic="replies")
    else:
        if notify_fn:
            sample = "\n".join([f"• @{p.get('username','?')}: {p.get('text','')[:50]}..." for p in to_reply[:3]])
            await notify_fn(f"Найдено {len(to_reply)} постов:\n{sample}", topic="replies")
        replied_count = 0
        for i, target in enumerate(to_reply):
            try:
                delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
                await asyncio.sleep(delay)

                context = target.get("_parent_post_text", "")
                combined_text = f"{target['text']}\n[пост: {context[:100]}]" if context else target["text"]
                reply_text = await _generate_reply(combined_text)

                if reply_text is None:
                    mark_replied(target["id"])
                    continue

                result = await _do_reply(target, reply_text)

                if result.get("success"):
                    mark_replied(target["id"])
                    results["replies_published"] += 1
                    replied_count += 1
                    if notify_fn:
                        msg = _reply_notify_text(replied_count, reply_count, target, reply_text, result)
                        await notify_fn(msg, topic="replies")
                else:
                    err = result.get("error", "?")
                    results["errors"].append(f"Ответ {i+1}: {err}")
                    if notify_fn:
                        await notify_fn(f"❌ Ответ {i+1}:\n{err[:500]}", topic="errors")
            except Exception as e:
                results["errors"].append(f"Ответ {i+1}: {repr(e)}")
                if notify_fn:
                    await notify_fn(f"❌ Ответ {i+1}:\n{repr(e)}", topic="errors")

    update_autopilot_settings(last_run=datetime.now().isoformat())
    log_action("autopilot_done", None, str(results))

    total_cost = (
        getattr(_generate_own_posts, "last_cost", 0) +
        getattr(_generate_reply, "total_cost", 0)
    )
    _generate_own_posts.last_cost = 0
    _generate_reply.total_cost = 0

    summary = (
        f"✅ Автопилот завершён!\n"
        f"📝 Постов: {results['own_published']}/{own_count}\n"
        f"💬 Ответов: {results['replies_published']}/{reply_count}\n"
        f"💰 Потрачено токенов: ~${total_cost:.4f}"
    )
    if results["errors"]:
        summary += f"\n⚠️ Ошибок: {len(results['errors'])}"
    if notify_fn:
        await notify_fn(summary, topic="summary")

    return results
