"""
Threads web scraper — ищет публичные посты через threads.net
Используется вместо Threads API keyword search (API не поддерживает поиск чужих постов)

Для ответов нужна сессия Threads:
- Добавь THREADS_SESSION_ID в .env (из browser cookies threads.net)
- Без неё — только парсинг постов без возможности ответить
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.threads.net/search?q={query}&serp_type=default"


def _get_auth_cookies() -> list[dict]:
    """
    Cookies для авторизации в threads.net.
    Получи sessionid: DevTools → Application → Cookies → threads.net → sessionid
    """
    session_id = os.getenv("THREADS_SESSION_ID", "")
    if not session_id:
        return []
    return [
        {"name": "sessionid", "value": session_id, "domain": ".threads.net", "path": "/"},
        {"name": "sessionid", "value": session_id, "domain": "www.threads.net", "path": "/"},
    ]


def _extract_posts_recursive(data, posts: list, seen_ids: set, max_posts: int):
    """Рекурсивно извлекает посты из JSON-ответа Threads GraphQL"""
    if len(posts) >= max_posts:
        return

    if isinstance(data, dict):
        # pk — реальный Threads media ID, валидный для reply_to_id в API
        post_id = str(data.get("pk") or "")
        text = data.get("text_post_app_text") or ""
        if not text and isinstance(data.get("caption"), dict):
            text = data["caption"].get("text", "")

        username = ""
        if isinstance(data.get("user"), dict):
            username = data["user"].get("username", "")

        shortcode = data.get("code", "")

        if post_id and text and username and post_id not in seen_ids:
            posts.append({
                "id": post_id,
                "text": text[:1000],
                "username": username,
                "shortcode": shortcode,
                "post_url": f"https://www.threads.net/@{username}/post/{shortcode}" if shortcode else "",
                "like_count": data.get("like_count", 0),
                "replies_count": data.get("reply_count") or data.get("replies_count", 0),
                "via_browser": False,  # реальный pk — используем Threads API
            })
            seen_ids.add(post_id)

        for v in data.values():
            _extract_posts_recursive(v, posts, seen_ids, max_posts)

    elif isinstance(data, list):
        for item in data:
            _extract_posts_recursive(item, posts, seen_ids, max_posts)


async def _extract_posts_from_dom(page, seen_ids: set, limit: int) -> list[dict]:
    """
    Fallback: парсит посты из DOM по shortcode.
    via_browser=True — ответ через браузерный click (нужна THREADS_SESSION_ID).
    """
    try:
        post_data = await page.evaluate(r"""
            () => {
                const results = [];
                const seen = new Set();
                const links = document.querySelectorAll('a[href*="/post/"]');
                for (const link of links) {
                    const match = link.href.match(/\/@([^/]+)\/post\/([A-Za-z0-9_-]+)/);
                    if (!match) continue;
                    const username = match[1];
                    const shortcode = match[2];
                    if (seen.has(shortcode)) continue;
                    seen.add(shortcode);

                    let container = link.closest('article')
                        || link.closest('[data-pressable-container]')
                        || link.parentElement?.parentElement?.parentElement;
                    let text = '';
                    if (container) {
                        const spans = container.querySelectorAll('span[dir="auto"]');
                        let best = '';
                        for (const s of spans) {
                            const t = s.innerText.trim();
                            if (t.length > best.length) best = t;
                        }
                        text = best;
                    }
                    if (username && shortcode && text && text.length > 20) {
                        results.push({
                            username,
                            shortcode,
                            post_url: link.href,
                            text: text.slice(0, 500)
                        });
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
            post_url = p.get("post_url", f"https://www.threads.net/@{username}/post/{sc}")
            if not sc or not username or not text:
                continue
            fake_id = f"sc:{sc}"
            if fake_id not in seen_ids:
                results.append({
                    "id": fake_id,
                    "shortcode": sc,
                    "post_url": post_url,
                    "text": text,
                    "username": username,
                    "like_count": 0,
                    "replies_count": 0,
                    "via_browser": True,  # нужен браузерный reply
                })
                seen_ids.add(fake_id)
                if len(results) >= limit:
                    break

        logger.info(f"DOM fallback: нашёл {len(results)} постов")
        return results

    except Exception as e:
        logger.warning(f"DOM parsing error: {e}")
        return []


async def reply_via_browser(post_url: str, reply_text: str) -> dict:
    """
    Отвечает на пост кликая в браузере.
    Требует THREADS_SESSION_ID в .env для авторизации.
    """
    cookies = _get_auth_cookies()
    if not cookies:
        return {"error": "Нет THREADS_SESSION_ID в .env — браузерный reply невозможен. Добавь cookie из threads.net"}

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            # Устанавливаем сессию
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                logger.info(f"Открываю пост: {post_url}")
                await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

                # Ищем поле для ответа — Threads показывает "Reply to @username..."
                reply_input = await page.query_selector(
                    '[placeholder*="Reply" i], [placeholder*="Ответ" i], '
                    '[aria-label*="Reply" i], [aria-label*="Write a reply" i]'
                )

                if not reply_input:
                    # Кликаем на кнопку comment/reply чтобы раскрыть поле
                    comment_btn = await page.query_selector(
                        'svg[aria-label*="Comment" i], svg[aria-label*="Reply" i], '
                        '[data-testid*="comment"], [data-testid*="reply"]'
                    )
                    if comment_btn:
                        await comment_btn.click()
                        await page.wait_for_timeout(2000)
                        reply_input = await page.query_selector(
                            '[placeholder*="Reply" i], [contenteditable="true"]'
                        )

                if not reply_input:
                    # Последняя попытка — любой contenteditable
                    reply_input = await page.query_selector('[contenteditable="true"]')

                if not reply_input:
                    return {"error": "Не найдено поле ввода ответа (возможно не залогинен в Threads)"}

                await reply_input.click()
                await page.wait_for_timeout(500)
                await reply_input.fill(reply_text)
                await page.wait_for_timeout(1000)

                # Кнопка Post
                post_btn = await page.query_selector(
                    'button:has-text("Post"), button:has-text("Reply"), '
                    '[data-testid*="post-button"], [aria-label*="Post" i]'
                )
                if not post_btn:
                    # Ищем активную кнопку рядом с полем ввода
                    post_btn = await page.query_selector('button[type="submit"]')

                if post_btn:
                    await post_btn.click()
                    await page.wait_for_timeout(2000)
                    logger.info(f"Ответ опубликован через браузер: {post_url}")
                    return {"success": True, "via_browser": True}
                else:
                    return {"error": "Не найдена кнопка Post для публикации ответа"}

            finally:
                await browser.close()

    except ImportError:
        return {"error": "Playwright не установлен: pip install playwright && playwright install chromium"}
    except Exception as e:
        return {"error": f"Browser reply error: {e}"}


async def search_trending_posts(keywords: list[str], limit: int = 20) -> list[dict]:
    """
    Ищет публичные посты Threads по ключевым словам.
    Возвращает список постов. via_browser=True → reply через браузер (нужен THREADS_SESSION_ID).
    """
    all_posts = []
    seen_ids = set()
    per_keyword = max(limit // max(len(keywords), 1), 5)
    cookies = _get_auth_cookies()

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            # Если есть сессия — авторизуемся (больше постов + можно reply)
            if cookies:
                await context.add_cookies(cookies)
                logger.info("Scraper: авторизован в Threads (sessionid есть)")
            else:
                logger.info("Scraper: работаю анонимно (THREADS_SESSION_ID не задан)")

            page = await context.new_page()

            # Перехватываем GraphQL — там реальные pk
            api_responses: list[dict] = []

            async def on_response(response):
                try:
                    if response.status != 200:
                        return
                    ctype = response.headers.get("content-type", "")
                    if "json" not in ctype:
                        return
                    body = await response.text()
                    # Только JSON с данными постов Threads
                    if '"pk"' in body and '"text_post_app_text"' in body:
                        api_responses.append(json.loads(body))
                except Exception:
                    pass

            page.on("response", on_response)

            for keyword in keywords:
                if len(all_posts) >= limit:
                    break

                api_responses.clear()
                logger.info(f"Scraping: '{keyword}'")

                try:
                    url = SEARCH_URL.format(query=keyword.replace(" ", "+"))
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_selector('a[href*="/post/"]', timeout=8000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(2000)
                    for _ in range(3):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(1500)
                except Exception as e:
                    logger.warning(f"Ошибка загрузки '{keyword}': {e}")
                    continue

                # 1. GraphQL — реальные pk, API reply
                keyword_posts: list[dict] = []
                for resp_data in api_responses:
                    _extract_posts_recursive(resp_data, keyword_posts, seen_ids, per_keyword)
                    if len(keyword_posts) >= per_keyword:
                        break

                if keyword_posts:
                    logger.info(f"'{keyword}': GraphQL → {len(keyword_posts)} постов (API reply)")
                else:
                    # 2. DOM fallback — браузерный reply
                    keyword_posts = await _extract_posts_from_dom(page, seen_ids, per_keyword)
                    logger.info(f"'{keyword}': DOM → {len(keyword_posts)} постов (browser reply)")

                all_posts.extend(keyword_posts)

            await browser.close()

    except ImportError:
        logger.error("Playwright не установлен! pip install playwright && playwright install chromium")
    except Exception as e:
        logger.error(f"Ошибка scraper: {e}")

    all_posts.sort(
        key=lambda p: (p.get("like_count") or 0) + (p.get("replies_count") or 0),
        reverse=True
    )
    return all_posts[:limit]
