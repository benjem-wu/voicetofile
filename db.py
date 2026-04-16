"""
SQLite 数据库模块
VoicToFile — 小宇宙播客转文字
"""
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_PATH = Path(__file__).parent / "voicetofile.db"

# --------------- 连接管理 ---------------

def get_conn() -> sqlite3.Connection:
    """获取数据库连接（每次新建，自动设置 row_factory）"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库，创建表（如不存在）"""
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pid TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                podcast_id INTEGER NOT NULL,
                eid TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                pub_date TEXT DEFAULT '',
                duration TEXT DEFAULT '',
                is_paid INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                txt_path TEXT DEFAULT '',
                error_msg TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'subscribe',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                UNIQUE(podcast_id, eid)
            )
        """)
        # 兼容已有数据库：source 列可能不存在
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN source TEXT NOT NULL DEFAULT 'subscribe'")
        except sqlite3.OperationalError:
            pass  # 列已存在

        # 索引加速查询
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_podcast
            ON episodes(podcast_id, status)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_status
            ON episodes(status)
        """)

        conn.commit()
    finally:
        conn.close()


# --------------- Podcasts ---------------

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
        cur.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        conn.commit()
    finally:
        conn.close()


# --------------- Episodes ---------------

def _is_placeholder(name: str, duration: str) -> bool:
    """判断是否为无音频的占位集（如'声动早咖啡'、'资讯早7点'等预告类标题）"""
    if not name:
        return True
    # 名称很短（7字符以下）的通常是占位符
    if len(name) < 7:
        return True
    return False


def add_episodes(episodes: list[dict], source: str = "subscribe"):
    """
    批量添加 episodes（已存在于 DB 则忽略）
    过滤掉占位集（名称很短、无音频的条目）
    episodes 格式：[{
        "podcast_id": int,
        "eid": str,
        "name": str,
        "pub_date": str,
        "duration": str,
        "is_paid": bool,
    }]
    source: 'subscribe'（订阅列表）或 'manual'（手动添加）
    """
    # 过滤占位集
    valid = [ep for ep in episodes if not _is_placeholder(ep.get("name", ""), ep.get("duration", ""))]

    conn = get_conn()
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        for ep in valid:
            cur.execute("""
                INSERT OR IGNORE INTO episodes
                (podcast_id, eid, name, pub_date, duration, is_paid, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ep["podcast_id"],
                ep["eid"],
                ep["name"],
                ep.get("pub_date", ""),
                ep.get("duration", ""),
                int(ep.get("is_paid", False)),
                source,
                now,
                now,
            ))
        conn.commit()
    finally:
        conn.close()


def update_episode_status(
    episode_id: int,
    status: str,
    txt_path: str = "",
    error_msg: str = ""
):
    """更新 episode 状态"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE episodes
            SET status = ?, txt_path = ?, error_msg = ?, updated_at = ?
            WHERE id = ?
        """, (status, txt_path, error_msg, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def get_episode_by_eid(podcast_id: int, eid: str) -> Optional[dict]:
    """根据 podcast_id + eid 查找 episode"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM episodes WHERE podcast_id = ? AND eid = ?",
            (podcast_id, eid)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_episodes_by_podcast(podcast_id: int) -> list[dict]:
    """列出某播客的所有 episodes，按发布日期倒序"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM episodes
            WHERE podcast_id = ?
            ORDER BY pub_date DESC
        """, (podcast_id,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_episode_by_id(episode_id: int) -> Optional[dict]:
    """根据 episode id 查找"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def reset_episode_for_retry(episode_id: int):
    """重置 episode 状态为 pending，用于重新处理"""
    update_episode_status(episode_id, "pending", txt_path="", error_msg="")


def sync_episode_txt_status(episode_id: int) -> bool:
    """
    检查 episode 的 txt 文件是否真实存在。
    如果 status=done_deleted 但文件不存在，或 status=transcribing 但无文件，自动重置为 pending。
    返回 True if status was changed.
    """
    ep = get_episode_by_id(episode_id)
    if not ep:
        return False
    txt = ep.get("txt_path") or ""
    txt_exists = bool(txt) and os.path.exists(txt)

    changed = False
    if ep["status"] == "transcribing" and not txt_exists:
        # 僵尸状态（进程崩溃遗留），重置
        update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        changed = True
    elif ep["status"] == "done_deleted" and not txt_exists:
        # 文件被删了，重置为 pending 可重新转
        update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        changed = True
    return changed


def sync_podcast_episodes_status(podcast_id: int) -> int:
    """
    对某播客下所有 episode 检查文件存在性并修正状态。
    返回修正的 episode 数量。
    """
    episodes = list_episodes_by_podcast(podcast_id)
    count = 0
    for ep in episodes:
        if sync_episode_txt_status(ep["id"]):
            count += 1
    return count


def cleanup_all_zombie_episodes() -> int:
    """
    全局清理：所有 status=transcribing 且无 txt_path 的 episode 重置为 pending。
    返回清理数量。
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE episodes
            SET status = 'pending', txt_path = '', error_msg = '', updated_at = ?
            WHERE status = 'transcribing'
            AND (txt_path IS NULL OR txt_path = '' OR NOT EXISTS (
                SELECT 1 FROM episodes e2 WHERE e2.id = episodes.id AND e2.txt_path IS NOT NULL AND e2.txt_path != ''
            ))
        """, (datetime.now().isoformat(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_active_episodes() -> list[dict]:
    """获取所有 pending/downloading/transcribing/failed 状态的 episodes"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status NOT IN ('done_deleted')
            ORDER BY e.created_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def cleanup_placeholder_episodes() -> int:
    """删除所有名称很短（<7字符）的占位集，返回删除数量"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM episodes WHERE LENGTH(name) < 7")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_pending_episodes() -> list[dict]:
    """获取所有 pending  episodes（用于队列展示）"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('pending', 'downloading', 'transcribing')
            ORDER BY e.created_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_manual_episodes() -> list[dict]:
    """获取所有手动添加的单集（source='manual'），按发布时间倒序"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.source = 'manual'
            ORDER BY e.pub_date DESC
        """)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_recently_completed_episodes(limit: int = 20) -> list[dict]:
    """获取最近完成/失败的任务（done_deleted 或 failed），按完成时间倒序，最多 limit 条"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('done_deleted', 'failed')
            ORDER BY e.updated_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_pending_episodes(limit: int = 50) -> list[dict]:
    """获取所有排队中的任务（pending），按加入时间升序"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status = 'queued'
            ORDER BY e.created_at ASC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# --------------- 初始化 ---------------

if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
