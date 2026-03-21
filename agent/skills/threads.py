"""
Threads Meta API — публикация постов, медиа, получение статистики
Документация: https://developers.facebook.com/docs/threads
"""
import httpx
import os
from database.db import save_threads_post, log_action

THREADS_API_BASE = "https://graph.threads.net/v1.0"


def _get_token():
    return os.getenv("THREADS_ACCESS_TOKEN")


def _get_user_id():
    return os.getenv("THREADS_USER_ID")


async def post_text(text: str) -> dict:
    """Опубликовать текстовый пост в Threads"""
    token = _get_token()
    user_id = _get_user_id()

    if not token or not user_id:
        return {"error": "THREADS_ACCESS_TOKEN и THREADS_USER_ID не настроены в .env"}

    async with httpx.AsyncClient() as client:
        # Шаг 1: создать медиа-контейнер
        resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads",
            params={
                "media_type": "TEXT",
                "text": text,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            log_action("threads_post_error", text[:100], str(resp.text))
            return {"error": f"Ошибка создания поста: {resp.text}"}

        container_id = resp.json().get("id")

        # Шаг 2: опубликовать
        pub_resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": token,
            }
        )
        if pub_resp.status_code != 200:
            log_action("threads_publish_error", container_id, str(pub_resp.text))
            return {"error": f"Ошибка публикации: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        save_threads_post(media_id, text)
        log_action("threads_post_published", text[:100], media_id)
        permalink = await get_post_permalink(media_id)
        return {"success": True, "media_id": media_id, "text": text, "permalink": permalink}


async def get_post_permalink(media_id: str) -> str | None:
    """Получить ссылку на пост по media_id"""
    token = _get_token()
    if not token or not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{THREADS_API_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token}
            )
            return r.json().get("permalink")
    except Exception:
        return None


async def post_with_image(text: str, image_url: str) -> dict:
    """Опубликовать пост с картинкой"""
    token = _get_token()
    user_id = _get_user_id()

    if not token or not user_id:
        return {"error": "THREADS_ACCESS_TOKEN и THREADS_USER_ID не настроены в .env"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads",
            params={
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": text,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка создания поста с изображением: {resp.text}"}

        container_id = resp.json().get("id")

        pub_resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": token,
            }
        )
        if pub_resp.status_code != 200:
            return {"error": f"Ошибка публикации: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        save_threads_post(media_id, text, image_url, "IMAGE")
        log_action("threads_image_post_published", text[:100], media_id)
        return {"success": True, "media_id": media_id}


async def reply_to_post(post_id: str, text: str) -> dict:
    """Ответить на пост в Threads"""
    token = _get_token()
    user_id = _get_user_id()

    if not token or not user_id:
        return {"error": "Не настроены токены Threads"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads",
            params={
                "media_type": "TEXT",
                "text": text,
                "reply_to_id": post_id,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка создания ответа: {resp.text}"}

        container_id = resp.json().get("id")

        pub_resp = await client.post(
            f"{THREADS_API_BASE}/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": token,
            }
        )
        if pub_resp.status_code != 200:
            return {"error": f"Ошибка публикации ответа: {pub_resp.text}"}

        media_id = pub_resp.json().get("id")
        log_action("threads_reply_published", f"reply to {post_id}", media_id)
        return {"success": True, "media_id": media_id}


async def get_my_posts(limit: int = 10) -> dict:
    """Получить мои последние посты из Threads"""
    token = _get_token()
    user_id = _get_user_id()

    if not token or not user_id:
        return {"error": "Не настроены токены Threads"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/{user_id}/threads",
            params={
                "fields": "id,text,timestamp,like_count,replies_count",
                "limit": limit,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка получения постов: {resp.text}"}

        return resp.json()


async def get_post_replies(media_id: str, limit: int = 10) -> dict:
    """
    Получить ответы (комментарии) на конкретный пост.
    Endpoint: GET /{media_id}/replies
    """
    token = _get_token()

    if not token:
        return {"error": "Не настроен THREADS_ACCESS_TOKEN"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/{media_id}/replies",
            params={
                "fields": "id,text,timestamp,username,like_count,replies_count",
                "limit": limit,
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка получения ответов: {resp.text}"}

        return resp.json()


async def search_posts(keyword: str, limit: int = 20) -> dict:
    """
    Threads API не поддерживает глобальный поиск чужих постов.
    Эта функция возвращает пустой результат — используй get_post_replies().
    """
    log_action("threads_search_skipped", keyword, "global search not available in Threads API")
    return {"data": [], "note": "Threads API не поддерживает поиск чужих постов"}


async def get_my_username() -> str | None:
    """Получить username текущего аккаунта Threads"""
    token = _get_token()
    user_id = _get_user_id()

    if not token or not user_id:
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/{user_id}",
            params={"fields": "username", "access_token": token}
        )
        if resp.status_code == 200:
            return resp.json().get("username")
    return None


async def get_insights(media_id: str) -> dict:
    """Получить статистику поста"""
    token = _get_token()

    if not token:
        return {"error": "Не настроен THREADS_ACCESS_TOKEN"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/{media_id}/insights",
            params={
                "metric": "likes,replies,reposts,quotes,views",
                "access_token": token,
            }
        )
        if resp.status_code != 200:
            return {"error": f"Ошибка получения статистики: {resp.text}"}

        return resp.json()
