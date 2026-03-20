"""
minprice.kz API — получение реальных цен на продукты в Казахстане
backend.minprice.kz/api — публичный REST API сайта
"""
import httpx
import logging

logger = logging.getLogger(__name__)

BASE = "https://backend.minprice.kz/api"
SITE_LINK = "https://minprice.kz/?th"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


async def search_prices(query: str, limit: int = 5) -> list[dict]:
    """
    Ищет продукт и возвращает список с ценами по магазинам.
    Пример: search_prices("бананы") → [{title, min_price, max_price, stores:[{name,price,prev_price}]}]
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        resp = await client.get(
            f"{BASE}/search",
            params={"q": query},
            headers=HEADERS
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

    results = []
    for hit in hits[:limit]:
        stores_data = []
        for store in hit.get("stores", []):
            if store.get("price"):
                prev = store.get("previous_price")
                change = None
                if prev and prev != store["price"]:
                    diff = store["price"] - prev
                    pct = round(diff / prev * 100, 1)
                    change = f"{'+' if diff > 0 else ''}{diff:.0f}₸ ({'+' if pct > 0 else ''}{pct}%)"
                stores_data.append({
                    "store": store.get("chain_name", store.get("store_name", "?")),
                    "price": store["price"],
                    "prev_price": prev,
                    "change": change,
                    "in_stock": store.get("in_stock", False),
                })

        if stores_data:
            # Сортируем по цене
            stores_data.sort(key=lambda x: x["price"])
            results.append({
                "title": hit["title"],
                "min_price": hit.get("min_price"),
                "max_price": hit.get("max_price"),
                "stores": stores_data,
                "link": SITE_LINK,
            })

    return results


async def get_trending_products(limit: int = 10) -> list[dict]:
    """
    Получает список продуктов с наибольшим разбросом цен между магазинами
    — хороший материал для постов "где купить дешевле"
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(
            f"{BASE}/products",
            params={"ordering": "-updated_at", "page_size": 50},
            headers=HEADERS
        )
        resp.raise_for_status()
        products = resp.json().get("results", [])

    interesting = []
    for p in products:
        mn = p.get("min_price")
        mx = p.get("max_price")
        if mn and mx and mx > mn:
            spread_pct = round((mx - mn) / mn * 100, 1)
            if spread_pct > 10:  # Разброс больше 10% — интересно
                interesting.append({
                    "title": p["title"],
                    "min_price": mn,
                    "max_price": mx,
                    "spread_pct": spread_pct,
                    "stores_count": p.get("linked_stores_count", 0),
                    "link": SITE_LINK,
                })

    interesting.sort(key=lambda x: x["spread_pct"], reverse=True)
    return interesting[:limit]


async def get_best_deals(limit: int = 10, min_score: float = 0.10) -> list[dict]:
    """
    Получает лучшие сделки с minprice.kz — товары где минимальная цена
    значительно ниже средней (deal_score = 1 - min_price/avg_price).
    min_score=0.10 значит минимум 10% выгода.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(
            f"{BASE}/products/best_deals/",
            params={"limit": limit, "min_score": min_score, "city_id": 1},
            headers=HEADERS
        )
        resp.raise_for_status()
        items = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", [])

    deals = []
    for item in items[:limit]:
        stores_data = []
        for store in item.get("stores", []):
            if store.get("price") and store.get("in_stock"):
                stores_data.append({
                    "store": store.get("chain_name", store.get("store_name", "?")),
                    "price": store["price"],
                    "prev_price": store.get("previous_price"),
                    "url": store.get("url"),
                })
        if stores_data:
            stores_data.sort(key=lambda x: x["price"])
            min_p = stores_data[0]["price"]
            max_p = max(s["price"] for s in stores_data)
            discount_pct = round((1 - min_p / ((min_p + max_p) / 2)) * 100, 1) if max_p > min_p else 0
            deals.append({
                "title": item["title"],
                "brand": item.get("brand", ""),
                "min_price": min_p,
                "max_price": max_p,
                "discount_pct": discount_pct,
                "stores": stores_data,
                "image_url": item.get("image_url"),
                "link": SITE_LINK,
            })

    return deals


def format_best_deals_for_prompt(deals: list[dict]) -> str:
    """Форматирует best deals для промпта — лучший контент для вирусных постов"""
    if not deals:
        return "Лучших сделок не найдено."

    lines = ["🔥 ЛУЧШИЕ СДЕЛКИ СЕГОДНЯ:"]
    for d in deals:
        brand = f" ({d['brand']})" if d.get("brand") else ""
        discount = f" — выгода ~{d['discount_pct']}%" if d.get("discount_pct") else ""
        lines.append(f"\n📦 {d['title']}{brand}{discount}")
        for s in d["stores"][:3]:
            prev = f" (было {s['prev_price']:.0f}₸)" if s.get("prev_price") and s["prev_price"] != s["price"] else ""
            lines.append(f"  {s['store']}: {s['price']:.0f}₸{prev}")
        if d.get("link"):
            lines.append(f"  {d['link']}")

    return "\n".join(lines)


def format_price_data_for_prompt(products: list[dict]) -> str:
    """Форматирует данные о ценах в читаемый текст для промпта Claude"""
    if not products:
        return "Данные о ценах не найдены."

    lines = []
    for p in products:
        lines.append(f"📦 {p['title']}")
        for s in p.get("stores", []):
            stock = "✓" if s.get("in_stock") else "✗"
            change_str = f" [{s['change']}]" if s.get("change") else ""
            lines.append(f"  {s['store']}: {s['price']:.0f}₸{change_str} {stock}")
        if p.get("link"):
            lines.append(f"  Подробнее: {p['link']}")
        lines.append("")

    return "\n".join(lines)
