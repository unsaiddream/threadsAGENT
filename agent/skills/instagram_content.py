"""
Генерация контента для Instagram через Claude.
Адаптировано под Instagram-формат (в отличие от Threads — здесь важны хэштеги).
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

INSTAGRAM_CAPTION_PROMPT = """
Ты — SMM-специалист для Instagram. Пишешь посты для minprice.kz — агрегатора цен в Казахстане.

Задача: написать подпись к посту с топ-{count} дешёвыми товарами дня.

Товары:
{products}

Требования к подписи:
- Начни с цепляющей строки с эмодзи (привлечь внимание в ленте)
- Упомяни 2-3 конкретных товара с ценами
- Призыв перейти на minprice.kz за полным списком
- 5-7 хэштегов в конце: #цены #казахстан #алматы #экономия #minprice + тематические
- Язык: русский, живой разговорный тон
- Длина: 150-250 слов

Верни ТОЛЬКО текст подписи, без кавычек.
"""

INSTAGRAM_PROMO_PROMPT = """
Ты — SMM-специалист для Instagram аккаунта minprice.kz (агрегатор цен, Казахстан).

Напиши вовлекающий пост на тему: "{topic}"

Требования:
- Первая строка = крючок с эмодзи
- Конкретные цифры / факты о ценах в Казахстане
- Упомяни minprice.kz как решение
- 5-8 хэштегов в конце
- Язык: русский
- Длина: 150-300 слов

Верни ТОЛЬКО текст поста.
"""


async def generate_daily_caption(products: list[dict]) -> str:
    """
    Сгенерировать подпись к ежедневному посту с топ-продуктами.
    products = [{"name": "Молоко", "price": 450, "store": "Magnum"}, ...]
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_caption(products)

    products_text = "\n".join(
        f"- {p['name']}: {p['price']}₸ ({p.get('store', '?')})"
        for p in products[:5]
    )
    prompt = INSTAGRAM_CAPTION_PROMPT.format(
        count=len(products[:5]),
        products=products_text
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            if resp.status_code == 200:
                return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Instagram caption generation error: {e}")

    return _fallback_caption(products)


async def generate_promo_post(topic: str) -> str:
    """Сгенерировать промо-пост на произвольную тему."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return f"Следи за ценами на minprice.kz! 🛒\n#{topic.replace(' ', '_')}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": INSTAGRAM_PROMO_PROMPT.format(topic=topic)}],
                }
            )
            if resp.status_code == 200:
                return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Instagram promo generation error: {e}")

    return f"Следи за ценами на minprice.kz! 🛒"


def _fallback_caption(products: list[dict]) -> str:
    """Fallback если Claude недоступен."""
    lines = ["🛒 Топ дешёвых товаров сегодня:"]
    for p in products[:5]:
        lines.append(f"• {p['name']} — {p['price']}₸ ({p.get('store', '')})")
    lines.append("\nПолный список на minprice.kz 👆")
    lines.append("\n#цены #казахстан #алматы #экономия #minprice #продукты #скидки")
    return "\n".join(lines)
