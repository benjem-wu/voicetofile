"""
Podcast Repository
VoiceToFile — 小宇宙播客转文字
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "voicetofile.db"

MANUAL_PID = "__manual__"
MANUAL_NAME = "精选播客"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_or_create_manual_podcast() -> int:
    """获取或创建"精选播客"虚拟播客"""
    return add_podcast(MANUAL_PID, MANUAL_NAME)


def add_podcast(pid: str, name: str) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO podcasts (pid, name)
            VALUES (?, ?)
            ON CONFLICT(pid) DO UPDATE SET name = excluded.name
        """, (pid, name))
        conn.commit()
        cur.execute("SELECT id FROM podcasts WHERE pid = ?", (pid,))
        row = cur.fetchone()
        return row["id"]
    finally:
        conn.close()


def upsert_podcast_details(
    podcast_id: int,
    author: str = "",
    description: str = "",
    cover_url: str = "",
    subscriber_count: int = 0,
    episode_count: int = 0,
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        cur.execute("""
            INSERT INTO podcast_details
                (podcast_id, author, description, cover_url, subscriber_count, episode_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(podcast_id) DO UPDATE SET
                author = excluded.author,
                description = excluded.description,
                cover_url = excluded.cover_url,
                subscriber_count = excluded.subscriber_count,
                episode_count = excluded.episode_count,
                updated_at = excluded.updated_at
        """, (podcast_id, author, description, cover_url, subscriber_count, episode_count, now))
        conn.commit()
    finally:
        conn.close()


def get_podcast_details(podcast_id: int) -> Optional[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM podcast_details WHERE podcast_id = ?", (podcast_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_podcast_by_pid(pid: str) -> Optional[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM podcasts WHERE pid = ?", (pid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_podcasts() -> list[dict]:
    """列出所有已订阅播客，按最新集时间倒序，精选播客置顶"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.*, MAX(e.pub_date) as latest_date
            FROM podcasts p
            LEFT JOIN episodes e ON e.podcast_id = p.id
            GROUP BY p.id
            ORDER BY
                CASE WHEN p.pid = '__manual__' THEN 0 ELSE 1 END,
                latest_date DESC,
                p.added_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_podcast(podcast_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))
        cur.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        conn.commit()
    finally:
        conn.close()


def mark_podcast_viewed(podcast_id: int):
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes
            SET is_new = 0, updated_at = ?
            WHERE podcast_id = ? AND is_new = 1
        """, (datetime.now().isoformat(), podcast_id))
        conn.commit()
    finally:
        conn.close()


def get_podcasts_with_new() -> list[int]:
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT DISTINCT podcast_id FROM episodes WHERE is_new = 1
        """)
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
