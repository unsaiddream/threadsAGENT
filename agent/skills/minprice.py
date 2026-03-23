"""
minprice.kz API — получение реальных цен на продукты в Казахстане
backend.minprice.kz/api — публичный REST API сайта

Эндпоинты:
- /api/search?q=...                   — Algolia поиск товаров
- /api/products/                      — каталог с фильтрами (brand, category, ordering)
- /api/products/best_deals/           — товары с макс. разницей мин/средней цены
- /api/products/with_multiple_stores/ — товары в нескольких магазинах (для сравнений)
- /api/carts/                         — корзины (создание, add/remove items, summary)
"""
import httpx
import logging

logger = logging.getLogger(__name__)

BASE = "https://backend.minprice.kz/api"
SITE_LINK = "https://minprice.kz/?th"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def product_link(uuid: str) -> str:
    """Прямая ссылка на товар по UUID"""
    return f"https://minprice.kz/product/{uuid}"


def product_search_link(query: str) -> str:
    """Ссылка на поиск (fallback если нет uuid)"""
    from urllib.parse import quote
    return f"https://minprice.kz/?search={quote(query)}"


def _parse_store(store: dict) -> dict:
    """Парсит данные магазина из API ответа"""
    prev = store.get("previous_price")
    price = store.get("price")
    change = None
    if prev and price and prev != price:
        diff = price - prev
        pct = round(diff / prev * 100, 1)
        change = f"{'+' if diff > 0 else ''}{diff:.0f}₸ ({'+' if pct > 0 else ''}{pct}%)"
    return {
        "store": store.get("chain_name", store.get("store_name", "?")),
        "chain_logo": store.get("chain_logo", ""),
        "price": price,
        "prev_price": prev,
        "change": change,
        "in_stock": store.get("in_stock", False),
        "url": store.get("url", ""),
    }


def _parse_product(item: dict) -> dict:
    """Парсит товар из API ответа — единый формат для всех эндпоинтов"""
    stores_raw = item.get("stores", [])
    stores = [_parse_store(s) for s in stores_raw if s.get("price")]
    stores.sort(key=lambda x: x["price"])

    min_p = stores[0]["price"] if stores else item.get("min_price", 0)
    max_p = stores[-1]["price"] if stores else item.get("max_price", 0)
    uuid = item.get("uuid", "")

    # Лучшая скидка среди магазинов (текущая vs предыдущая цена)
    best_drop = None
    for s in stores:
        if s["prev_price"] and s["prev_price"] > s["price"]:
            drop_pct = round((1 - s["price"] / s["prev_price"]) * 100, 1)
            if best_drop is None or drop_pct > best_drop["drop_pct"]:
                best_drop = {
                    "store": s["store"],
                    "price": s["price"],
                    "was": s["prev_price"],
                    "drop_pct": drop_pct,
                }

    return {
        "title": item.get("title", ""),
        "brand": item.get("brand", ""),
        "uuid": uuid,
        "image_url": item.get("image_url", ""),
        "categories": item.get("canonical_categories") or item.get("categories", []),
        "min_price": min_p,
        "max_price": max_p,
        "spread_pct": round((max_p - min_p) / min_p * 100, 1) if min_p and max_p > min_p else 0,
        "stores": stores,
        "stores_count": len(stores),
        "best_drop": best_drop,
        "link": product_link(uuid) if uuid else SITE_LINK,
    }


async def search_prices(query: str, limit: int = 5) -> list[dict]:
    """
    Algolia поиск товаров по названию.
    Возвращает товары с ценами по магазинам, UUID, image_url, ссылками.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        resp = await client.get(
            f"{BASE}/search",
            params={"q": query, "hitsPerPage": limit},
            headers=HEADERS
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

    results = []
    for hit in hits[:limit]:
        parsed = _parse_product(hit)
        if parsed["stores"]:
            results.append(parsed)

    return results


async def get_best_deals(limit: int = 10, min_score: float = 0.10) -> list[dict]:
    """
    Товары с максимальной выгодой: min_price значительно ниже средней.
    Включает image_url, uuid, previous_price для каждого магазина.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(
            f"{BASE}/products/best_deals/",
            params={"limit": limit, "min_score": min_score, "city_id": 1},
            headers=HEADERS
        )
        resp.raise_for_status()
        raw = resp.json()
        items = raw if isinstance(raw, list) else raw.get("results", [])

    return [_parse_product(item) for item in items[:limit] if item.get("stores")]


async def get_multi_store_products(limit: int = 10) -> list[dict]:
    """
    Товары доступные в нескольких магазинах — идеально для постов-сравнений.
    Сортировка по свежести обновления.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(
            f"{BASE}/products/with_multiple_stores/",
            params={"ordering": "-updated_at", "page_size": limit},
            headers=HEADERS
        )
        resp.raise_for_status()
        products = resp.json().get("results", [])

    results = []
    for p in products[:limit]:
        parsed = _parse_product(p)
        if parsed["stores_count"] >= 2 and parsed["spread_pct"] > 10:
            results.append(parsed)

    results.sort(key=lambda x: x["spread_pct"], reverse=True)
    return results


async def get_trending_products(limit: int = 10) -> list[dict]:
    """Товары с наибольшим разбросом цен"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(
            f"{BASE}/products",
            params={"ordering": "-updated_at", "page_size": 50},
            headers=HEADERS
        )
        resp.raise_for_status()
        products = resp.json().get("results", [])

    results = [_parse_product(p) for p in products if p.get("stores")]
    results = [r for r in results if r["spread_pct"] > 10]
    results.sort(key=lambda x: x["spread_pct"], reverse=True)
    return results[:limit]


async def get_price_drops(limit: int = 10) -> list[dict]:
    """
    Товары у которых цена упала — best_deals + фильтр по previous_price.
    Отличный контент: "Было X₸ → стало Y₸!"
    """
    deals = await get_best_deals(limit=30, min_score=0.05)
    drops = [d for d in deals if d.get("best_drop")]
    drops.sort(key=lambda x: x["best_drop"]["drop_pct"], reverse=True)
    return drops[:limit]


def format_product_for_prompt(p: dict) -> str:
    """Форматирует один товар для промпта Claude"""
    brand = f" ({p['brand']})" if p.get("brand") else ""
    stores_str = ", ".join(f"{s['store']}: {s['price']:.0f}₸" for s in p.get("stores", [])[:4])

    parts = [f"{p['title']}{brand}: {stores_str}"]

    if p.get("best_drop"):
        d = p["best_drop"]
        parts.append(f"СКИДКА в {d['store']}: было {d['was']:.0f}₸ → {d['price']:.0f}₸ (-{d['drop_pct']}%)")

    parts.append(f"Ссылка: {p['link']}")

    if p.get("image_url"):
        parts.append(f"Картинка: {p['image_url']}")

    return "\n".join(parts)


def format_best_deals_for_prompt(deals: list[dict]) -> str:
    """Форматирует best deals для промпта"""
    if not deals:
        return "Лучших сделок не найдено."
    lines = ["🔥 ЛУЧШИЕ СДЕЛКИ СЕГОДНЯ:"]
    for d in deals:
        lines.append(f"\n{format_product_for_prompt(d)}")
    return "\n".join(lines)


def format_price_data_for_prompt(products: list[dict]) -> str:
    """Форматирует данные о ценах для промпта"""
    if not products:
        return "Данные о ценах не найдены."
    lines = []
    for p in products:
        lines.append(format_product_for_prompt(p))
        lines.append("")
    return "\n".join(lines)
