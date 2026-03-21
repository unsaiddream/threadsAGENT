"""
SQLite база данных — история сообщений, настройки, очередь постов
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "openclaw.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # История диалога с агентом
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Опубликованные посты в Threads
    c.execute("""
        CREATE TABLE IF NOT EXISTS threads_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT,
            text TEXT,
            media_url TEXT,
            post_type TEXT DEFAULT 'TEXT',
            status TEXT DEFAULT 'published',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Запланированные посты
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            media_url TEXT,
            post_type TEXT DEFAULT 'TEXT',
            scheduled_for TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Лог всех действий агента
    c.execute("""
        CREATE TABLE IF NOT EXISTS actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT,
            result TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Настройки автопилота
    c.execute("""
        CREATE TABLE IF NOT EXISTS autopilot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER DEFAULT 0,
            keywords TEXT DEFAULT '["цены на продукты","цены в казахстане","продукты дорожают"]',
            own_posts_count INTEGER DEFAULT 5,
            reply_posts_count INTEGER DEFAULT 10,
            run_hour INTEGER DEFAULT 10,
            niche TEXT DEFAULT 'цены на продукты и товары в Казахстане',
            last_run TEXT
        )
    """)
    # Вставить дефолтные настройки если ещё нет
    c.execute("""
        INSERT OR IGNORE INTO autopilot_settings (id) VALUES (1)
    """)
    # Обновляем reply_posts_count до 10 если он ещё 5 (старое дефолтное значение)
    c.execute("""
        UPDATE autopilot_settings SET reply_posts_count=10 WHERE id=1 AND reply_posts_count=5
    """)
    # Оптимизированные ключевые слова: 5 широких → быстрый поиск (~1 мин вместо 5+)
    import json as _json
    optimized_keywords = _json.dumps([
        "продукты", "овощи фрукты", "магазин цены", "дорогие продукты", "дорожает"
    ], ensure_ascii=False)
    c.execute("UPDATE autopilot_settings SET keywords=? WHERE id=1", (optimized_keywords,))

    # Посты на которые уже отвечали (чтобы не дублировать)
    c.execute("""
        CREATE TABLE IF NOT EXISTS replied_posts (
            post_id TEXT PRIMARY KEY,
            replied_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


def save_message(role: str, content: str):
    conn = get_conn()
    conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def get_history(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def log_action(action: str, details: str = None, result: str = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO actions_log (action, details, result) VALUES (?, ?, ?)",
        (action, details, result)
    )
    conn.commit()
    conn.close()


def save_threads_post(media_id: str, text: str, media_url: str = None, post_type: str = "TEXT"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO threads_posts (media_id, text, media_url, post_type) VALUES (?, ?, ?, ?)",
        (media_id, text, media_url, post_type)
    )
    conn.commit()
    conn.close()


def get_recent_posts(limit: int = 10) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM threads_posts ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_scheduled_post(text: str, scheduled_for: str, media_url: str = None, post_type: str = "TEXT"):
    conn = get_conn()
    cursor = conn.execute(
        "INSERT INTO scheduled_posts (text, media_url, post_type, scheduled_for) VALUES (?, ?, ?, ?)",
        (text, media_url, post_type, scheduled_for)
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return post_id


def get_pending_scheduled_posts() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_for <= datetime('now')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_scheduled_post_done(post_id: int, status: str = "published"):
    conn = get_conn()
    conn.execute("UPDATE scheduled_posts SET status=? WHERE id=?", (status, post_id))
    conn.commit()
    conn.close()


# ── Автопилот ──────────────────────────────────────────────

def get_autopilot_settings() -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM autopilot_settings WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {}
    d = dict(row)
    d["keywords"] = json.loads(d["keywords"])
    return d


def update_autopilot_settings(**kwargs):
    conn = get_conn()
    for key, value in kwargs.items():
        if key == "keywords" and isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        conn.execute(f"UPDATE autopilot_settings SET {key}=? WHERE id=1", (value,))
    conn.commit()
    conn.close()


def is_already_replied(post_id: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM replied_posts WHERE post_id=?", (post_id,)).fetchone()
    conn.close()
    return row is not None


def mark_replied(post_id: str):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO replied_posts (post_id) VALUES (?)", (post_id,))
    conn.commit()
    conn.close()
