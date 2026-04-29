"""
播客数据库操作
"""
from typing import Optional
from datetime import datetime

from .connection import get_conn

MANUAL_PID = "__manual__"
MANUAL_NAME = "精选播客"


def get_or_create_manual_podcast() -> int:
    """获取或创建"精选播客"虚拟播客，用于存放手动添加的单集"""
    return add_podcast(MANUAL_PID, MANUAL_NAME)


def add_podcast(pid: str, name: str) -> int:
    """添加播客，如已存在则更新名称，返回 podcast_id"""
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
    """插入或更新播客详情。"""
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
    """获取播客详情，不存在则返回 None"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM podcast_details WHERE podcast_id = ?", (podcast_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_podcast_by_pid(pid: str) -> Optional[dict]:
    """根据 pid 查找播客"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM podcasts WHERE pid = ?", (pid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_podcasts() -> list[dict]:
    """列出所有已订阅播客，按添加时间倒序"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM podcasts ORDER BY added_at DESC")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_podcast(podcast_id: int):
    """删除播客及其所有 episodes（级联）"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))
        cur.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        conn.commit()
    finally:
        conn.close()
