"""
Decoy аккаунты Threads — создают "жалобные" посты о ценах
чтобы генерировать трафик для основного аккаунта.

Поддерживает несколько аккаунтов через .env:
  DECOY_THREADS_ACCESS_TOKEN   + DECOY_THREADS_USER_ID
  DECOY_THREADS_ACCESS_TOKEN_2 + DECOY_THREADS_USER_ID_2
  ... и т.д.
"""
import asyncio
import httpx
import os
import logging

logger = logging.getLogger(__name__)

THREADS_API_BASE = "https://graph.threads.net/v1.0"

# Кэш user_id по токену (ключ = первые 20 символов токена)
_user_id_cache: dict[str, str] = {}


def get_decoy_tokens() -> list[str]:
    """Возвращает список всех настроенных decoy токенов."""
    tokens = []
    # Первый аккаунт (без суффикса)
    t = os.getenv("DECOY_THREADS_ACCESS_TOKEN", "").strip()
    if t:
        tokens.append(t)
    # Дополнительные аккаунты: _2, _3, ...
    i = 2
    while True:
        t = os.getenv(f"DECOY_THREADS_ACCESS_TOKEN_{i}", "").strip()
        if not t:
            break
        tokens.append(t)
        i += 1
    return tokens


async def _get_user_id(token: str) -> str | None:
    """Получить user_id для конкретного токена (с кэшем)."""
    key = token[:20]

    # Сначала проверяем .env кэш по индексу
    tokens = get_decoy_tokens()
    idx = tokens.index(token) if token in tokens else -1
    if idx == 0:
        env_id = os.getenv("DECOY_THREADS_USER_ID", "").strip()
    elif idx > 0:
        env_id = os.getenv(f"DECOY_THREADS_USER_ID_{idx + 1}", "").strip()
    else:
        env_id = ""

    if env_id:
        return env_id

    # Из памяти
    if key in _user_id_cache:
        return _user_id_cache[key]

    # Запрашиваем через API
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
                    _user_id_cache[key] = uid
                    logger.info(f"Decoy аккаунт #{idx + 1}: @{username} (id={uid})")
                    return uid
            else:
                logger.warning(f"Decoy: не удалось получить user_id (idx={idx}): {r.text[:200]}")
    except Exception as e:
        logger.error(f"Decoy: ошибка получения user_id: {e}")
    return None


async def post_as_decoy(text: str, token: str | None = None) -> dict:
    """
    Опубликовать текстовый пост от имени decoy аккаунта.
    token=None → использует первый настроенный аккаунт.
    """
    if token is None:
        tokens = get_decoy_tokens()
        if not tokens:
            return {"error": "DECOY_THREADS_ACCESS_TOKEN не задан в .env"}
        token = tokens[0]

    user_id = await _get_user_id(token)
    if not user_id:
        return {"error": "Не удалось получить user_id decoy аккаунта — проверь токен"}

    logger.info(f"Decoy: создаю пост ({len(text)} символов)")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{THREADS_API_BASE}/{user_id}/threads",
                params={"media_type": "TEXT", "text": text, "access_token": token}
            )
            if resp.status_code != 200:
                logger.error(f"Decoy container error: {resp.text}")
                return {"error": f"Ошибка создания контейнера: {resp.text}"}

            container_id = resp.json().get("id")
            logger.info(f"Decoy: контейнер {container_id}")

            # Threads API требует паузу перед публикацией
            await asyncio.sleep(5)

            pub_resp = await client.post(
                f"{THREADS_API_BASE}/{user_id}/threads_publish",
                params={"creation_id": container_id, "access_token": token}
            )
            if pub_resp.status_code != 200:
                logger.error(f"Decoy publish error: {pub_resp.text}")
                return {"error": f"Ошибка публикации: {pub_resp.text}"}

            media_id = pub_resp.json().get("id")
            permalink = await _get_permalink(media_id, token)
            logger.info(f"Decoy: опубликован {permalink}")

            return {"success": True, "media_id": media_id, "permalink": permalink, "text": text}

    except Exception as e:
        logger.error(f"Decoy: исключение: {e}")
        return {"error": f"Исключение: {e}"}


async def _get_permalink(media_id: str, token: str) -> str | None:
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
