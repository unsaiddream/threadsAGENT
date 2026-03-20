"""
Threads web scraper — ищет публичные посты через threads.net
Используется вместо Threads API keyword search (API не поддерживает поиск чужих постов)
"""
import json
import logging
import re
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.threads.net/search?q={query}&serp_type=default"


def _extract_posts_recursive(data, posts: list, seen_ids: set, max_posts: int):
    """Рекурсивно извлекает посты из JSON-ответа Threads GraphQL"""
    if len(posts) >= max_posts:
        return

    if isinstance(data, dict):
        # Проверяем: это объект поста?
        post_id = str(data.get("pk") or data.get("id") or "")
        text = data.get("text_post_app_text") or ""
        if not text and isinstance(data.get("caption"), dict):
            text = data["caption"].get("text", "")

        username = ""
        if isinstance(data.get("user"), dict):
            username = data["user"].get("username", "")

        if post_id and text and username and post_id not in seen_ids:
            posts.append({
                "id": post_id,
                "text": text[:1000],
                "username": username,
                "like_count": data.get("like_count", 0),
                "replies_count": data.get("reply_count") or data.get("replies_count", 0),
            })
            seen_ids.add(post_id)

        for v in data.values():
            _extract_posts_recursive(v, posts, seen_ids, max_posts)

    elif isinstance(data, list):
        for item in data:
            _extract_posts_recursive(item, posts, seen_ids, max_posts)


def _shortcode_to_id(shortcode: str) -> str | None:
    """Конвертирует Threads shortcode в numeric media ID"""
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        n = 0
        for char in shortcode:
            if char not in alphabet:
                return None
            n = n * 64 + alphabet.index(char)
        return str(n)
    except Exception:
        return None


async def _extract_posts_from_dom(page, seen_ids: set, limit: int) -> list[dict]:
    """Fallback: парсит посты из DOM — ищет ссылки вида /@username/post/shortcode"""
    try:
        post_data = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();
                const links = document.querySelectorAll('a[href*="/post/"]');
                for (const link of links) {
                    const match = link.href.match(/\/@([^/]+)\\/post\\/([A-Za-z0-9_-]+)/);
                    if (!match) continue;
                    const username = match[1];
                    const shortcode = match[2];
                    if (seen.has(shortcode)) continue;
                    seen.add(shortcode);

                    // Ищем текст поста в ближайшем контейнере
                    let container = link.closest('article')
                        || link.closest('[data-pressable-container]')
                        || link.parentElement?.parentElement;
                    let text = '';
                    if (container) {
                        const spans = container.querySelectorAll('span[dir], p');
                        text = Array.from(spans).map(s => s.innerText).join(' ').trim();
                    }
                    if (username && shortcode && text) {
                        results.push({ username, shortcode, text: text.slice(0, 500) });
                    }
                }
                return results;
            }
        """)

        results = []
        for p in post_data:
            sc = p.get("shortcode", "")
            username = p.get("username", "")
            text = p.get("text", "")
            if not sc or not username or not text:
                continue
            post_id = _shortcode_to_id(sc)
            if post_id and post_id not in seen_ids:
                results.append({
                    "id": post_id,
                    "text": text,
                    "username": username,
                    "like_count": 0,
                    "replies_count": 0,
                })
                seen_ids.add(post_id)
                if len(results) >= limit:
                    break

        logger.info(f"DOM fallback: нашёл {len(results)} постов")
        return results

    except Exception as e:
        logger.warning(f"DOM parsing error: {e}")
        return []


async def search_trending_posts(keywords: list[str], limit: int = 20) -> list[dict]:
    """
    Ищет публичные посты Threads по ключевым словам через браузер.
    Возвращает список: [{id, text, username, like_count, replies_count}]
    """
    all_posts = []
    seen_ids = set()
    per_keyword = max(limit // max(len(keywords), 1), 5)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Mobile Safari/537.36"
                )
            )
            page = await context.new_page()

            # Перехватываем все JSON-ответы от Threads API
            api_responses: list[dict] = []

            async def on_response(response):
                url = response.url
                if response.status == 200 and ("graphql" in url or "/api/" in url):
                    ctype = response.headers.get("content-type", "")
                    if "json" in ctype:
                        try:
                            body = await response.text()
                            api_responses.append(json.loads(body))
                        except Exception:
                            pass

            page.on("response", on_response)

            for keyword in keywords:
                if len(all_posts) >= limit:
                    break

                api_responses.clear()
                logger.info(f"Scraping Threads search: '{keyword}'")

                try:
                    url = SEARCH_URL.format(query=keyword.replace(" ", "+"))
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3000)
                    # Скроллим — загружаем больше постов
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"Ошибка загрузки поиска '{keyword}': {e}")
                    continue

                # Парсим из перехваченных API-ответов
                keyword_posts: list[dict] = []
                for resp_data in api_responses:
                    _extract_posts_recursive(resp_data, keyword_posts, seen_ids, per_keyword)
                    if len(keyword_posts) >= per_keyword:
                        break

                # Fallback: парсим DOM если API не дал результатов
                if not keyword_posts:
                    keyword_posts = await _extract_posts_from_dom(page, seen_ids, per_keyword)

                logger.info(f"'{keyword}': найдено {len(keyword_posts)} постов")
                all_posts.extend(keyword_posts)

            await browser.close()

    except Exception as e:
        logger.error(f"Ошибка scraper: {e}")

    # Сортируем по вовлечённости
    all_posts.sort(
        key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0),
        reverse=True
    )
    return all_posts[:limit]
