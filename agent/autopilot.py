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
    format_product_for_prompt, product_link,
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


# Уровень 3 (score=3): пост ИДЕАЛЬНО подходит — человек спрашивает про цены / просит сравнение
_TIER3_PATTERNS = [
    "сравните цен", "сравни цен", "сравнивал цен", "кто нибудь сравнивал",
    "что с ценами", "а что с ценами", "почему цены", "куда цены",
    "дорожают продукты", "продукты дорожают", "всё дорожает",
    "где дешевле купить", "где дешевле продукты", "посоветуйте где",
    "помогите найти дешевле", "как сэкономить на продукт",
    "цены на продукты", "цены выросли", "подорожало всё", "подорожали продукты",
    "почему так дорого", "почему всё так дорого",
    "где купить дешевле", "рекомендуйте магазин", "какой магазин дешевле",
    "цены в казахстан", "инфляция продукт", "бьёт по карман",
]

# Уровень 2 (score=2): упоминает цены, экономию, магазины — хорошо для ответа
_TIER2_KEYWORDS = [
    "цен", "дорог", "дешев", "подорожа", "переплат", "экономи",
    "скидк", "акци", "чек ", "тенге", "₸", "бюджет",
    "магнум", "small", "арзан", "анвар", "galmart", "метро", "магазин",
    "инфляц", "арбуз", "arbuz", "аибф", "airbaf", "супермаркет",
    "корзин продукт", "стоимост", "прайс", "расценк",
]

# Уровень 1 (score=1): только продукты без цен — слабый контекст
_TIER1_FOOD = [
    "продукт", "молок", "хлеб", "яйц", "масл", "мясо", "куриц",
    "овощ", "фрукт", "банан", "помидор", "картош", "сахар",
    "гречк", "рис ", "корзин", "покупк", "говядин", "свинин",
    "творог", "кефир", "сметан", "колбас", "сосиск", "макарон",
]

# Хэндлы магазинов для тегирования в ответах
_STORE_HANDLES = {
    "магнум": "@magnumgo",
    "magnum": "@magnumgo",
    "арбуз": "@arbuz_kz",
    "arbuz": "@arbuz_kz",
    "airbaf": "@airbafresh",
    "аибф": "@airbafresh",
    "fresh": "@airbafresh",
}


def _score_post_relevance(text: str) -> int:
    """
    Оценивает релевантность поста по 4-уровневой шкале:
    3 = идеально (вопрос про цены / запрос сравнения)
    2 = хорошо (упоминает цены, экономию, магазины)
    1 = слабо (только продукты, без цен)
    0 = нерелевантно — пропускаем
    """
    t = text.lower()
    for pattern in _TIER3_PATTERNS:
        if pattern in t:
            return 3
    price_hits = sum(1 for kw in _TIER2_KEYWORDS if kw in t)
    food_hits = sum(1 for kw in _TIER1_FOOD if kw in t)
    if price_hits >= 2:
        return 2
    if price_hits >= 1 and food_hits >= 1:
        return 2  # цена + продукт = хороший контекст для ответа
    if price_hits >= 1 or food_hits >= 2:
        return 1
    return 0


def _is_post_relevant(text: str) -> bool:
    """Совместимость: возвращает True если score >= 1"""
    return _score_post_relevance(text) >= 1


def _detect_store_handles(text: str) -> list[str]:
    """
    Находит упомянутые в тексте магазины и возвращает их @хэндлы.
    Используется для тегирования магазинов в ответах — показываем что мы сравниваем их.
    """
    t = text.lower()
    found = []
    seen = set()
    for keyword, handle in _STORE_HANDLES.items():
        if keyword in t and handle not in seen:
            found.append(handle)
            seen.add(handle)
    return found[:2]  # не больше 2 тегов чтобы не спамить


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
    Если конкретных продуктов нет — берём лучшие сделки дня как универсальный контекст.
    Возвращает (текст_для_промпта, лучшая_ссылка_на_товар).
    """
    lines = []
    best_link = SITE_LINK

    if product_keywords:
        for kw in product_keywords:
            try:
                results = await search_prices(kw, limit=2)
                for r in results:
                    stores = r.get("stores", [])[:3]
                    store_info = ", ".join(f"{s['store']}: {s['price']:.0f}₸" for s in stores)
                    lines.append(f"{r['title']}: {store_info} → {r['link']}")
                    if best_link == SITE_LINK:
                        best_link = r["link"]
            except Exception as e:
                logger.warning(f"Цены для reply ({kw}): {e}")

    # Если по конкретным продуктам ничего не нашли — берём топ-3 сделки дня
    if not lines:
        try:
            deals = await get_best_deals(limit=3, min_score=0.05)
            for d in deals:
                stores = d.get("stores", [])[:2]
                store_info = ", ".join(f"{s['store']}: {s['price']:.0f}₸" for s in stores)
                lines.append(f"{d['title']}: {store_info} → {d['link']}")
                if best_link == SITE_LINK:
                    best_link = d["link"]
        except Exception as e:
            logger.warning(f"Fallback deals для reply: {e}")

    return "\n".join(lines), best_link


async def _generate_reply(target_text: str, score: int = 1) -> str | None:
    """
    Генерирует умный контекстный ответ на чужой пост.
    score — уровень релевантности (3=вопрос про цены, 2=упоминает цены, 1=продукты).
    """
    products = _extract_product_keywords(target_text)
    price_context, link = await _fetch_prices_for_reply(products)

    t = target_text.lower()

    # Определяем интент и формат ответа
    if any(p in t for p in ["сравни", "сравните", "сравнивал", "где дешевле", "где купить", "посоветуй", "помогите", "кто нибудь", "рекомендуй"]):
        intent_type = "REQUEST"
        intent_instruction = "Человек просит помощи найти где дешевле. Дай ПРЯМОЙ ответ: назови конкретный магазин и цену из данных ниже. Тон — как друг который реально знает цены."
    elif any(p in t for p in ["дорожают", "дорожает", "дорого", "подорожал", "подорожали", "выросли", "растут цены", "всё дорожает", "вот это цены", "бьёт по карман"]):
        intent_type = "COMPLAINT"
        intent_instruction = "Человек жалуется на цены. Покажи что есть где дешевле — дай конкретную цену и магазин из данных. Не нуди, будь полезным."
    elif any(p in t for p in ["что с ценами", "почему цены", "куда цены", "ценовая политика", "инфляция"]):
        intent_type = "QUESTION"
        intent_instruction = "Человек спрашивает про цены. Ответь конкретикой — цифры и магазины из данных. Коротко и по делу."
    elif score == 3:
        intent_type = "STRONG_SIGNAL"
        intent_instruction = "Сильный сигнал что человеку важны цены. Дай самый полезный факт из данных — конкретный магазин и цена — и ссылку."
    else:
        intent_type = "MENTION"
        intent_instruction = "Человек упоминает продукты или цены. Добавь один конкретный факт про цену из данных который его удивит."

    price_block = f"\nАКТУАЛЬНЫЕ ЦЕНЫ (используй эти данные):\n{price_context}\n" if price_context else ""

    # Пинг магазинов: тегируем упомянутые в посте или в данных магазины
    # Это показывает пользователям что мы реально сравниваем эти магазины
    mentioned_handles = _detect_store_handles(target_text)
    data_handles = _detect_store_handles(price_context)
    all_handles = list(dict.fromkeys(mentioned_handles + data_handles))[:2]
    store_tag_line = (
        f"- Упомяни магазин(ы) с тегом: {' '.join(all_handles)} — вставь тег сразу после названия (напр. «в Магнуме {all_handles[0]}»)\n"
        if all_handles else ""
    )

    examples_by_type = {
        "REQUEST": "Пример: «В Магнуме (@magnumgo) 10 яиц — 380₸, в Анваре 395₸. Если брать пачку — Магнум выгоднее, все цены: [ссылка]»",
        "COMPLAINT": "Пример: «Молоко в Арзане на 40₸ дешевле чем в Магнуме (@magnumgo) — 340₸ против 380₸. Не всё одинаково растёт: [ссылка]»",
        "QUESTION": "Пример: «Бананы в Small за неделю выросли с 290₸ до 340₸, в Galmart пока 295₸. Такая картина: [ссылка]»",
        "STRONG_SIGNAL": "Пример: «Гречка в Метро — 180₸, в Анваре 220₸, 25% разницы. Смотри сравнение: [ссылка]»",
        "MENTION": "Пример: «Яйцо в Арзане на 60₸ дешевле чем в Магнуме (@magnumgo) — 320₸ против 380₸: [ссылка]»",
    }
    example = examples_by_type.get(intent_type, examples_by_type["MENTION"])

    prompt = f"""Чужой пост в Threads:
"{target_text}"

Ты — @minimalprice_kz, сервис сравнения цен на продукты в Казахстане. Отвечаешь как реальный человек.
{price_block}
ЗАДАЧА: {intent_instruction}

{example}

Правила:
- Отвечай именно на ЭТОТ пост — не шаблонно
- {"Используй цифры из данных выше — магазин + цена" if price_context else "Упомяни что можно сравнить цены на сайте"}
{store_tag_line}- Ссылка {link} — один раз в конце, как часть фразы (не отдельной строкой)
- 1-2 предложения максимум
- НЕ начинай с: "Кстати", "Интересно", "О,", "Привет", "Да,", "Согласен"
- Пиши как казахстанец — живым разговорным языком

Если пост вообще не про еду/цены/магазины — напиши только: SKIP

Верни ТОЛЬКО текст ответа."""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )

    usage = response.usage
    reply_cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
    _generate_reply.total_cost = getattr(_generate_reply, "total_cost", 0) + reply_cost

    text = response.content[0].text.strip()

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
    Ранжирует кандидатов по релевантности (score 3→2→1).
    Fallback: комментарии под своими постами если scraper не нашёл ничего.
    """
    my_username = await get_my_username()

    # 1. Ищем чужие посты — большой буфер: count * 10 чтобы после фильтров точно хватило
    # per_keyword в scraper теперь НЕ делится на кол-во keywords, поэтому даём реальный лимит
    search_limit = count * 10
    scraped = await search_trending_posts(keywords, limit=search_limit)

    # Скорим все посты
    # - score > 0: релевантны, идут в приоритет
    # - score = 0: найдены по нашим ключевым словам → они тоже относятся к теме
    #   (scraper не ищет случайные посты), но ранжируем ниже
    high_scored = []   # score >= 2
    low_scored = []    # score == 1
    zero_scored = []   # score == 0, но из релевантного поиска

    for p in scraped:
        if is_already_replied(p["id"]):
            continue
        if p.get("username") == my_username:
            continue
        s = _score_post_relevance(p.get("text", ""))
        p["_relevance_score"] = s
        if s >= 2:
            high_scored.append(p)
        elif s == 1:
            low_scored.append(p)
        else:
            zero_scored.append(p)

    # Сортируем каждый tier по популярности
    for bucket in (high_scored, low_scored, zero_scored):
        bucket.sort(key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0), reverse=True)

    # Собираем финальный список: сначала лучшие, добираем из lower tiers до count
    scored = high_scored + low_scored + zero_scored

    dist = {3: 0, 2: 0, 1: 0, 0: 0}
    for p in scraped:
        dist[min(p.get("_relevance_score", 0), 3)] += 1
    logger.info(
        f"Scraper: raw={len(scraped)}, tier3={dist[3]}, tier2={dist[2]}, "
        f"tier1={dist[1]}, tier0={dist[0]}, отфильтровано={len(scored)}"
    )

    if len(scored) >= count:
        return scored[:count]

    logger.info(f"Scraper дал только {len(scored)} постов — добираем из комментариев под своими постами")

    # 2. Fallback: добираем из комментариев под своими постами
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
                    r["_relevance_score"] = _score_post_relevance(r.get("text", "")) or 2
                    fallback.append(r)
                    seen_ids.add(rid)
        except Exception as e:
            logger.warning(f"Fallback replies error {post['id']}: {e}")

    # Объединяем scraper результаты с fallback комментариями
    combined = scored + [f for f in fallback if f.get("id") not in {p.get("id") for p in scored}]
    return combined[:count]


async def run_test_post(notify_fn=None) -> dict:
    """
    Тестовый режим: генерирует и публикует ОДИН пост.
    Удобно проверять новые фичи без запуска полного автопилота.
    """
    logger.info("run_test_post: старт")
    if notify_fn:
        await notify_fn("🧪 Тест: генерирую один пост...", topic="posts")

    logger.info("run_test_post: загружаю товары с minprice...")
    items = await _fetch_deals_and_products()
    logger.info(f"run_test_post: получено {len(items)} товаров")
    if not items:
        msg = "❌ Нет данных о ценах для поста"
        if notify_fn:
            await notify_fn(msg, topic="errors")
        return {"success": False, "error": msg}

    item = items[0]
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
        logger.info("run_test_post: вызываю Claude API...")
        response = _claude().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        logger.info("run_test_post: Claude ответил")
        text = response.content[0].text.strip()
        if item["link"] not in text and SITE_LINK not in text:
            text = text.rstrip() + f"\n\n{item['link']}"

        image = item.get("image_url") or None
        if image and "%w" in image:
            image = image.replace("%w", "400").replace("%h", "400")

        # Постим как текст — Threads сам покажет превью ссылки из поста
        logger.info("run_test_post: публикую...")
        result = await post_text(text)

        if result.get("success"):
            permalink = result.get("permalink") or ""
            link_line = f"\n🔗 {permalink}" if permalink else ""
            if notify_fn:
                await notify_fn(
                    f"📝 Тестовый пост опубликован:\n{text[:300]}{link_line}",
                    topic="posts"
                )
            return {"success": True, "text": text, "permalink": permalink}
        else:
            err = result.get("error", "неизвестная ошибка")
            if notify_fn:
                await notify_fn(f"❌ Тест не прошёл:\n{err[:500]}", topic="errors")
            return {"success": False, "error": err}

    except Exception as e:
        if notify_fn:
            await notify_fn(f"❌ Ошибка тестового поста:\n{repr(e)}", topic="errors")
        return {"success": False, "error": repr(e)}


async def run_replies_only(notify_fn=None, count: int = 10) -> dict:
    """
    Ищет трендовые посты по ключевым словам через Playwright scraper
    и пишет контекстные комментарии со ссылкой на сайт.
    """
    settings = get_autopilot_settings()
    keywords = settings.get("keywords", [
        "цены на продукты",
        "а что с ценами",
        "дорожают продукты",
        "кто нибудь сравнивал цены",
        "сравните цены",
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
            score = target.get("_relevance_score", 1)
            reply_text = await _generate_reply(combined_text, score=score)

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
        "цены на продукты",
        "а что с ценами",
        "дорожают продукты",
        "кто нибудь сравнивал цены",
        "сравните цены",
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

            # Постим как текст — Threads сам покажет превью ссылки из поста
            result = await post_text(text)

            if result.get("success"):
                results["own_published"] += 1
                if notify_fn:
                    permalink = result.get("permalink") or ""
                    link_line = f"\n🔗 {permalink}" if permalink else ""
                    await notify_fn(
                        f"📝 Пост {i+1}/{own_count}:\n{text[:200]}...{link_line}",
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
                score = target.get("_relevance_score", 1)
                reply_text = await _generate_reply(combined_text, score=score)

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


# ── Реальное время: мониторинг свежих постов ─────────────────────────────
_monitor_active = False
_monitor_task = None


def is_monitor_active() -> bool:
    return _monitor_active


async def run_monitor_loop(notify_fn=None, interval_minutes: int = 3, replies_per_cycle: int = 3):
    """
    Мониторинг в реальном времени — ищет СВЕЖИЕ посты каждые N минут и сразу отвечает.
    Использует вкладку 'Недавние' чтобы находить посты первыми — до того как другие успеют.

    Чем раньше ответишь под свежим постом — тем выше видимость комментария.
    """
    global _monitor_active
    _monitor_active = True

    settings = get_autopilot_settings()
    keywords = settings.get("keywords", [
        "дорого", "дешевле", "цены", "подорожало",
        "продукты цены", "Магнум дешевле",
    ])

    my_username = await get_my_username()

    cycle = 0
    logger.info(f"Монитор запущен: каждые {interval_minutes} мин, до {replies_per_cycle} ответов/цикл")

    if notify_fn:
        await notify_fn(
            f"⚡ Монитор запущен!\n"
            f"Ищу свежие посты каждые {interval_minutes} мин.\n"
            f"Слова: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}",
            topic="replies"
        )

    while _monitor_active:
        cycle += 1
        logger.info(f"Монитор цикл {cycle}")

        try:
            # Ищем ТОЛЬКО свежие посты — вкладка "Недавние"
            # Берём с запасом: replies_per_cycle * 8, чтобы хватило после фильтрации
            scraped = await search_trending_posts(
                keywords,
                limit=replies_per_cycle * 8,
                recent=True
            )

            # Фильтруем: не наши, не отвеченные, ранжируем по релевантности
            candidates = []
            for p in scraped:
                if is_already_replied(p["id"]):
                    continue
                if p.get("username") == my_username:
                    continue
                s = _score_post_relevance(p.get("text", ""))
                p["_relevance_score"] = s
                candidates.append(p)

            candidates.sort(key=lambda p: p["_relevance_score"], reverse=True)
            to_reply = candidates[:replies_per_cycle]

            if not to_reply:
                logger.info(f"Цикл {cycle}: новых постов нет")
            else:
                logger.info(f"Цикл {cycle}: найдено {len(to_reply)} новых постов")
                if notify_fn:
                    sample = "\n".join([
                        f"• @{p.get('username','?')} (score={p['_relevance_score']}): {p.get('text','')[:50]}..."
                        for p in to_reply[:2]
                    ])
                    await notify_fn(f"⚡ Цикл {cycle}: {len(to_reply)} новых постов\n{sample}", topic="replies")

                replied = 0
                for target in to_reply:
                    if not _monitor_active:
                        break

                    try:
                        score = target.get("_relevance_score", 1)
                        reply_text = await _generate_reply(target.get("text", ""), score=score)

                        if reply_text is None:
                            mark_replied(target["id"])
                            continue

                        result = await _do_reply(target, reply_text)

                        if result.get("success"):
                            mark_replied(target["id"])
                            replied += 1
                            if notify_fn:
                                msg = _reply_notify_text(replied, len(to_reply), target, reply_text, result)
                                await notify_fn(f"⚡ {msg}", topic="replies")
                        else:
                            err = result.get("error", "?")
                            logger.warning(f"Monitor reply error: {err}")

                        # Минимальная пауза между ответами (не спамим)
                        if replied < len(to_reply):
                            await asyncio.sleep(random.randint(30, 60))

                    except Exception as e:
                        logger.error(f"Monitor ответ ошибка: {e}")

        except Exception as e:
            logger.error(f"Monitor цикл {cycle} ошибка: {e}")
            if notify_fn:
                await notify_fn(f"⚠️ Монитор ошибка: {repr(e)[:200]}", topic="errors")

        # Ждём следующего цикла
        if _monitor_active:
            logger.info(f"Монитор: жду {interval_minutes} мин до следующего цикла")
            await asyncio.sleep(interval_minutes * 60)

    logger.info("Монитор остановлен")
    if notify_fn:
        await notify_fn("⏹ Монитор остановлен.", topic="replies")


def stop_monitor():
    """Остановить мониторинг (следующий цикл не запустится)"""
    global _monitor_active
    _monitor_active = False
    logger.info("Монитор: получена команда остановки")
