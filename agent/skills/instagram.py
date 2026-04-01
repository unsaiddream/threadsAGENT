"""
Instagram Graph API — публикация постов для бизнес-аккаунта.
Документация: https://developers.facebook.com/docs/instagram-api/guides/content-publishing

Схема публикации (как у Threads):
  1. POST /{user-id}/media       → создаём контейнер, получаем creation_id
  2. POST /{user-id}/media_publish → публикуем контейнер
"""
import asyncio
import os
import logging
import httpx
from database.db import log_action

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def _get_token() -> str | None:
    return os.getenv("INSTAGRAM_ACCESS_TOKEN")


def _get_user_id() -> str | None:
    return os.getenv("INSTAGRAM_USER_ID")


async def publish_photo(image_url: str, caption: str) -> dict:
    """
    Опубликовать пост с одним фото.
    image_url — публично доступная ссылка на изображение.
    """
    token = _get_token()
    user_id = _get_user_id()
    if not token or not user_id:
        return {"error": "INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID не настроены в .env"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Шаг 1: контейнер
        resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media",
            params={
                "image_url": image_url,
                "caption": caption,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            log_action("instagram_container_error", caption[:100], resp.text)
            return {"error": f"Ошибка создания контейнера: {resp.text}"}

        creation_id = resp.json().get("id")
        logger.info(f"Instagram: контейнер {creation_id}")

        # Instagram API тоже требует паузу перед публикацией
        await asyncio.sleep(5)

        # Шаг 2: публикация
        pub_resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": token,
            }
        )
        if pub_resp.status_code != 200:
            log_action("instagram_publish_error", creation_id, pub_resp.text)
            return {"error": f"Ошибка публикации: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        permalink = await get_post_permalink(media_id)
        log_action("instagram_photo_published", caption[:100], media_id)
        logger.info(f"Instagram: опубликован {permalink}")
        return {"success": True, "media_id": media_id, "permalink": permalink}


async def publish_carousel(image_urls: list[str], caption: str) -> dict:
    """
    Опубликовать карусель (2-10 фото).
    """
    token = _get_token()
    user_id = _get_user_id()
    if not token or not user_id:
        return {"error": "INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID не настроены в .env"}

    if not (2 <= len(image_urls) <= 10):
        return {"error": "Карусель: нужно 2-10 изображений"}

    async with httpx.AsyncClient(timeout=60) as client:
        # Шаг 1: создаём контейнер для каждого фото
        item_ids = []
        for url in image_urls:
            r = await client.post(
                f"{GRAPH_API_BASE}/{user_id}/media",
                params={
                    "image_url": url,
                    "is_carousel_item": "true",
                    "access_token": token,
                }
            )
            if r.status_code != 200:
                return {"error": f"Ошибка создания элемента карусели: {r.text}"}
            item_ids.append(r.json().get("id"))

        # Шаг 2: создаём контейнер карусели
        carousel_resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media",
            params={
                "media_type": "CAROUSEL",
                "children": ",".join(item_ids),
                "caption": caption,
                "access_token": token,
            }
        )
        if carousel_resp.status_code != 200:
            return {"error": f"Ошибка создания карусели: {carousel_resp.text}"}

        creation_id = carousel_resp.json().get("id")
        await asyncio.sleep(5)

        # Шаг 3: публикация
        pub_resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": token}
        )
        if pub_resp.status_code != 200:
            return {"error": f"Ошибка публикации карусели: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        permalink = await get_post_permalink(media_id)
        log_action("instagram_carousel_published", caption[:100], media_id)
        return {"success": True, "media_id": media_id, "permalink": permalink}


async def get_post_permalink(media_id: str) -> str | None:
    """Получить ссылку на опубликованный пост."""
    token = _get_token()
    if not token or not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{GRAPH_API_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token}
            )
            if r.status_code == 200:
                return r.json().get("permalink")
    except Exception:
        pass
    return None


async def get_account_insights() -> dict:
    """Получить базовую статистику аккаунта (followers, reach, impressions)."""
    token = _get_token()
    user_id = _get_user_id()
    if not token or not user_id:
        return {"error": "Токены не настроены"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GRAPH_API_BASE}/{user_id}/insights",
            params={
                "metric": "follower_count,reach,impressions,profile_views",
                "period": "day",
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка получения статистики: {resp.text}"}
        return resp.json()


async def publish_reel(video_url: str, caption: str) -> dict:
    """
    Опубликовать Reel (вертикальное видео 1080×1920).
    video_url — публично доступная ссылка на MP4.
    """
    token = _get_token()
    user_id = _get_user_id()
    if not token or not user_id:
        return {"error": "INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID не настроены в .env"}

    async with httpx.AsyncClient(timeout=120) as client:
        # Шаг 1: контейнер для Reel
        resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            log_action("instagram_reel_container_error", caption[:100], resp.text)
            return {"error": f"Ошибка создания контейнера: {resp.text}"}

        creation_id = resp.json().get("id")
        logger.info(f"Instagram Reel: контейнер {creation_id}, жду обработки...")

        # Reels обрабатываются дольше — ждём до 90 сек
        for attempt in range(18):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"{GRAPH_API_BASE}/{creation_id}",
                params={"fields": "status_code", "access_token": token}
            )
            status = status_resp.json().get("status_code", "")
            logger.info(f"  Попытка {attempt+1}: status={status}")
            if status == "FINISHED":
                break
            if status == "ERROR":
                return {"error": "Ошибка обработки видео Instagram"}

        # Шаг 2: публикация
        pub_resp = await client.post(
            f"{GRAPH_API_BASE}/{user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": token}
        )
        if pub_resp.status_code != 200:
            log_action("instagram_reel_publish_error", creation_id, pub_resp.text)
            return {"error": f"Ошибка публикации: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        permalink = await get_post_permalink(media_id)
        log_action("instagram_reel_published", caption[:100], media_id)
        logger.info(f"Instagram Reel опубликован: {permalink}")
        return {"success": True, "media_id": media_id, "permalink": permalink}


async def get_my_posts(limit: int = 10) -> dict:
    """Получить последние посты аккаунта."""
    token = _get_token()
    user_id = _get_user_id()
    if not token or not user_id:
        return {"error": "Токены не настроены"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GRAPH_API_BASE}/{user_id}/media",
            params={
                "fields": "id,caption,media_type,timestamp,like_count,comments_count,permalink",
                "limit": limit,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка: {resp.text}"}
        return resp.json()
