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

from agent.skills.threads import post_text, reply_to_post, get_my_posts, get_post_replies, get_my_username
from agent.skills.threads_scraper import search_trending_posts, reply_via_browser
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

РЕАЛЬНЫЕ цены прямо сейчас:
{price_context}

Напиши {count} РАЗНЫХ вирусных постов. Каждый пост — отдельная идея, разный продукт.

━━ АЛГОРИТМ THREADS: что даёт охват ━━
• Первая строка = крючок. Стоп-скролл. Без неё пост мёртв.
• Эмоция важнее информации: возмущение, удивление, узнавание себя
• Конкретные цифры и названия магазинов → люди сохраняют и пересылают
• Вопрос в конце → провоцирует ответы (ответы = охват х2)
• 100–200 символов в основном тексте, потом ссылка

━━ ФОРМАТЫ (чередуй) ━━
1. ШОК-ФАКТ: "[Продукт] в [Магазин А] — 999₸. В [Магазин Б] — 650₸. Это 35% переплаты за тот же товар. Проверяй: {SITE_LINK}"
2. ПРОВОКАЦИЯ: "Магазины знают что ты не сравниваешь цены. Поэтому [факт]. Вот где реально дешевле: {SITE_LINK}"
3. ЛАЙФХАК: "Сэкономил [X]₸ за месяц одним правилом: [правило]. Работает для [продукт]. {SITE_LINK}"
4. СРАВНЕНИЕ: "[Продукт]: мин. [X]₸ / макс. [Y]₸. Разница [Z]₸ — это [аналогия]. Где дешевле → {SITE_LINK}"
5. ВОПРОС-БОЛЬ: "Вы вообще знаете сколько [продукт] стоит в [магазин]? Я не знал пока не проверил. [Факт]. {SITE_LINK}"
6. СОЦИАЛЬНОЕ ДОКАЗАТЕЛЬСТВО: "[X] человек уже сравнили цены на [продукт] сегодня. Самый дешёвый вариант: {SITE_LINK}"

━━ ТЕГИ (добавь в конец каждого поста) ━━
Выбери 3–4 из: #цены #Казахстан #экономия #продукты #Алматы #инфляция #лайфхак #тенге

━━ ОБЯЗАТЕЛЬНО ━━
- Ссылка {SITE_LINK} только ОДИН РАЗ, в конце
- Конкретные цифры из данных выше
- Разговорный стиль, без официоза

Верни ТОЛЬКО {count} постов, каждый отделён строкой "---"
"""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    # Считаем токены (Sonnet 4.6: $3/1M input, $15/1M output)
    usage = response.usage
    cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
    log_action("autopilot_tokens_own", f"in={usage.input_tokens} out={usage.output_tokens}", f"~${cost:.4f}")
    _generate_own_posts.last_cost = getattr(_generate_own_posts, "last_cost", 0) + cost

    raw = response.content[0].text
    posts = [p.strip() for p in raw.split("---") if p.strip()]

    # Убеждаемся что ссылка есть в каждом посте
    final = []
    for p in posts[:count]:
        if SITE_LINK not in p:
            p = p.rstrip() + f"\n\nВсе цены: {SITE_LINK}"
        final.append(p)

    return final


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


async def _generate_reply(target_text: str) -> str:
    """Генерирует ответ на чужой пост — полезный + ссылка"""

    prompt = f"""Ты автор @minimalprice_kz — сайта сравнения цен в Казахстане.

Чужой пост в Threads:
"{target_text}"

Напиши ответ который:
- Звучит как живой человек, а не реклама — первая реакция, потом ценность
- Добавляет конкретный факт или инсайт про цены в КЗ (если есть в данных поста)
- Провоцирует автора ответить тебе — задай вопрос или скажи что-то неожиданное
- В самом конце — одна ссылка: {SITE_LINK}

ДЛИНА: 1-2 предложения + ссылка. Короткие ответы получают больше охвата.
ТОНАЛЬНОСТЬ: живо, с характером. Не "полезно и информативно", а как отвечает реальный человек.
Ссылка — ОДИН РАЗ, в конце, без слова "Цены:".

Верни ТОЛЬКО текст ответа.
"""

    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    usage = response.usage
    reply_cost = (usage.input_tokens / 1_000_000 * 3) + (usage.output_tokens / 1_000_000 * 15)
    _generate_reply.total_cost = getattr(_generate_reply, "total_cost", 0) + reply_cost

    text = response.content[0].text.strip()
    # Страховка — если ссылки нет, добавляем одну в конце
    if SITE_LINK not in text:
        text = text.rstrip(".") + f" {SITE_LINK}"
    return text


async def _collect_reply_candidates(keywords: list[str], count: int) -> list[dict]:
    """
    Ищет трендовые посты по ключевым словам через Playwright scraper.
    Fallback: комментарии под своими постами если scraper не нашёл ничего.
    """
    my_username = await get_my_username()

    # 1. Ищем чужие посты через браузер
    scraped = await search_trending_posts(keywords, limit=count * 2)

    # Фильтруем: только чужие и ещё не отвеченные
    candidates = [
        p for p in scraped
        if not is_already_replied(p["id"]) and p.get("username") != my_username
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
        "тенге", "цены на молоко", "цены на хлеб", "мясо подорожало"
    ])

    if notify_fn:
        await notify_fn(f"🔍 Ищу трендовые посты через браузер:\n{', '.join(keywords)}")

    candidates = await _collect_reply_candidates(keywords, count)

    if not candidates:
        if notify_fn:
            await notify_fn("Не найдено постов для ответов.")
        return {"replies_published": 0, "errors": []}

    if notify_fn:
        sample_info = "\n".join(
            [f"• @{p.get('username','?')}: {p.get('text','')[:60]}..." for p in candidates[:3]]
        )
        await notify_fn(f"Найдено {len(candidates)} постов. Начинаю отвечать:\n{sample_info}")

    results = {"replies_published": 0, "errors": []}

    for i, target in enumerate(candidates):
        try:
            # Генерируем ответ с учётом контекста родительского поста
            context = target.get("_parent_post_text", "")
            combined_text = f"{target['text']}\n[пост: {context[:100]}]" if context else target["text"]
            reply_text = await _generate_reply(combined_text)
            result = await _do_reply(target, reply_text)

            if result.get("success"):
                mark_replied(target["id"])
                results["replies_published"] += 1
                mode = "🌐" if target.get("via_browser") else "📡"
                if notify_fn:
                    await notify_fn(
                        f"✅ {mode} Ответ {i+1}/{len(candidates)} → @{target.get('username','?')}:\n"
                        f"{reply_text[:120]}..."
                    )
            else:
                err = result.get("error", "неизвестная ошибка")
                results["errors"].append(f"Ответ {i+1}: {err}")
                if notify_fn:
                    await notify_fn(f"❌ Ответ {i+1} не удался: {err}")
        except Exception as e:
            results["errors"].append(f"Ответ {i+1}: {e}")

        if i < len(candidates) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            await asyncio.sleep(delay)

    if notify_fn:
        await notify_fn(
            f"Ответы завершены!\n"
            f"Опубликовано: {results['replies_published']}/{len(candidates)}\n"
            + (f"Ошибок: {len(results['errors'])}" if results["errors"] else "")
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
        "тенге", "цены на молоко", "цены на хлеб", "мясо подорожало"
    ])
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
                    permalink = result.get("permalink") or ""
                    link_line = f"\n🔗 {permalink}" if permalink else ""
                    await notify_fn(f"📝 Пост {i+1}/{own_count}:\n{post[:200]}...{link_line}")
            else:
                results["errors"].append(f"Пост {i+1}: {result.get('error')}")
        except Exception as e:
            results["errors"].append(f"Пост {i+1}: {e}")

        if i < len(own_posts) - 1:
            delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
            if notify_fn:
                await notify_fn(f"Пауза {delay//60} мин перед следующим постом...")
            await asyncio.sleep(delay)

    # ── 2. Ищем трендовые посты и пишем контекстные ответы ─
    if notify_fn:
        await notify_fn(f"🔍 Ищу трендовые посты:\n{', '.join(keywords)}")

    to_reply = await _collect_reply_candidates(keywords, reply_count)

    if not to_reply:
        if notify_fn:
            await notify_fn("Не найдено постов для ответов.")
    else:
        if notify_fn:
            sample = "\n".join([f"• @{p.get('username','?')}: {p.get('text','')[:50]}..." for p in to_reply[:3]])
            await notify_fn(f"Найдено {len(to_reply)} постов. Примеры:\n{sample}")
        for i, target in enumerate(to_reply):
            try:
                delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
                if notify_fn:
                    await notify_fn(f"Пауза {delay//60} мин перед ответом {i+1}...")
                await asyncio.sleep(delay)

                context = target.get("_parent_post_text", "")
                combined_text = f"{target['text']}\n[пост: {context[:100]}]" if context else target["text"]
                reply_text = await _generate_reply(combined_text)
                result = await _do_reply(target, reply_text)

                if result.get("success"):
                    mark_replied(target["id"])
                    results["replies_published"] += 1
                    mode = "🌐" if target.get("via_browser") else "📡"
                    if notify_fn:
                        await notify_fn(f"✅ {mode} Ответ {i+1}/{reply_count} на @{target.get('username','?')}:\n{reply_text[:120]}...")
                else:
                    results["errors"].append(f"Ответ {i+1}: {result.get('error')}")
            except Exception as e:
                results["errors"].append(f"Ответ {i+1}: {e}")

    update_autopilot_settings(last_run=datetime.now().isoformat())
    log_action("autopilot_done", None, str(results))

    # Суммарные расходы на токены за этот запуск
    total_cost = (
        getattr(_generate_own_posts, "last_cost", 0) +
        getattr(_generate_reply, "total_cost", 0)
    )
    # Сбрасываем счётчики для следующего запуска
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
        await notify_fn(summary)

    return results
