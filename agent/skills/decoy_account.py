"""
Decoy аккаунт Threads — создаёт "жалобные" посты о ценах
чтобы генерировать органический трафик для основного аккаунта.

Флоу: decoy создаёт пост → основной бот на него отвечает.
"""
import httpx
import os
import logging

logger = logging.getLogger(__name__)

THREADS_API_BASE = "https://graph.threads.net/v1.0"

# Кэшируем user_id чтобы не запрашивать каждый раз
_decoy_user_id_cache: str | None = None


def _get_decoy_token() -> str | None:
    return os.getenv("DECOY_THREADS_ACCESS_TOKEN")


async def get_decoy_user_id() -> str | None:
    """Получить user_id decoy аккаунта (через API или .env кэш)"""
    global _decoy_user_id_cache

    # Сначала смотрим в .env
    env_id = os.getenv("DECOY_THREADS_USER_ID", "").strip()
    if env_id:
        return env_id

    # Потом в памяти
    if _decoy_user_id_cache:
        return _decoy_user_id_cache

    token = _get_decoy_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{THREADS_API_BASE}/me",
                params={"fields": "id,username", "access_token": token}
            )
            if r.status_code == 200:
                data = r.json()
                uid = data.get("id")
                username = data.get("username", "?")
                if uid:
                    _decoy_user_id_cache = uid
                    logger.info(f"Decoy аккаунт: @{username} (id={uid})")
                    return uid
            else:
                logger.warning(f"Decoy: не удалось получить user_id: {r.text}")
    except Exception as e:
        logger.error(f"Decoy: ошибка получения user_id: {e}")
    return None


async def post_as_decoy(text: str) -> dict:
    """
    Опубликовать текстовый пост от имени decoy аккаунта.
    Возвращает media_id и permalink созданного поста.
    """
    token = _get_decoy_token()
    if not token:
        return {"error": "DECOY_THREADS_ACCESS_TOKEN не задан в .env"}

    user_id = await get_decoy_user_id()
    if not user_id:
        return {"error": "Не удалось получить user_id decoy аккаунта — проверь токен"}

    logger.info(f"Decoy: создаю пост ({len(text)} символов)")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
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
                logger.error(f"Decoy post container error: {resp.text}")
                return {"error": f"Ошибка создания контейнера: {resp.text}"}

            container_id = resp.json().get("id")
            logger.info(f"Decoy: контейнер создан {container_id}")

            # Шаг 2: опубликовать
            pub_resp = await client.post(
                f"{THREADS_API_BASE}/{user_id}/threads_publish",
                params={
                    "creation_id": container_id,
                    "access_token": token,
                }
            )
            if pub_resp.status_code != 200:
                logger.error(f"Decoy publish error: {pub_resp.text}")
                return {"error": f"Ошибка публикации: {pub_resp.text}"}

            media_id = pub_resp.json().get("id")
            logger.info(f"Decoy: пост опубликован media_id={media_id}")

            # Получаем permalink
            permalink = await _get_permalink(media_id, token)
            logger.info(f"Decoy: permalink={permalink}")

            return {
                "success": True,
                "media_id": media_id,
                "permalink": permalink,
                "text": text,
            }

    except Exception as e:
        logger.error(f"Decoy: исключение при публикации: {e}")
        return {"error": f"Исключение: {e}"}


async def _get_permalink(media_id: str, token: str) -> str | None:
    """Получить permalink поста по media_id"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{THREADS_API_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token}
            )
            if r.status_code == 200:
                return r.json().get("permalink")
    except Exception:
        pass
    return None
