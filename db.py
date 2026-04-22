"""
SQLite 数据库模块（保留基础设施 + 队列操作）
VoicToFile — 小宇宙播客转文字

业务逻辑已拆分至 repositories/ 包：
  - repositories/podcast_repo.py: 播客相关
  - repositories/episode_repo.py: 单集相关
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "voicetofile.db"

# 从 podcast_repo 重新导出，保持 import 兼容
from repositories.podcast_repo import MANUAL_PID, MANUAL_NAME

# --------------- 连接管理（供 queue 操作使用）---------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN source TEXT NOT NULL DEFAULT 'subscribe'")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN audio_path TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN discarded INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_podcast
            ON episodes(podcast_id, status)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_status
            ON episodes(status)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS podcast_details (
                podcast_id INTEGER PRIMARY KEY,
                author TEXT DEFAULT '',
                description TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                subscriber_count INTEGER DEFAULT 0,
                episode_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
    finally:
        conn.close()


# --------------- 队列（DB 唯一数据源）---------------

def get_next_queued_task() -> Optional[dict]:
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE episodes
            SET status = 'downloading', progress = 0, updated_at = ?
            WHERE id = (
                SELECT id FROM episodes
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING id, eid, name, podcast_id, status, retry_count
        """, (datetime.now().isoformat(),))
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        task = dict(row)
        cur2 = conn.execute("SELECT name FROM podcasts WHERE id = ?", (task["podcast_id"],))
        pRow = cur2.fetchone()
        task["podcast_name"] = pRow["name"] if pRow else ""
        return task
    finally:
        conn.close()


def enqueue_task(episode_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE episodes
            SET status = 'queued', retry_count = 0, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'failed')
        """, (datetime.now().isoformat(), episode_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def increment_retry_count(episode_id: int) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE episodes SET retry_count = retry_count + 1, updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), episode_id))
        conn.commit()
        cur2 = conn.execute("SELECT retry_count FROM episodes WHERE id = ?", (episode_id,))
        row = cur2.fetchone()
        return row["retry_count"] if row else 0
    finally:
        conn.close()


def update_task_progress(episode_id: int, progress: int):
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes SET progress = ?, updated_at = ? WHERE id = ?
        """, (progress, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def cleanup_stale_tasks() -> int:
    conn = get_conn()
    cleaned = 0
    now = datetime.now().isoformat()
    try:
        rows = conn.execute("""
            SELECT id, name, txt_path, podcast_id FROM episodes
            WHERE status IN ('downloading', 'transcribing')
        """).fetchall()

        for row in rows:
            ep_id = row['id']
            ep_name = row['name']
            ep_txt_path = row['txt_path'] or ''
            p_row = conn.execute("SELECT name FROM podcasts WHERE id = ?", (row['podcast_id'],)).fetchone()
            podcast_name = p_row['name'] if p_row else ''

            if not ep_txt_path or not os.path.exists(ep_txt_path):
                clean = ep_name.replace('\n', ' ').strip()
                illegal = '<>:"/\\|?*'
                for ch in illegal:
                    clean = clean.replace(ch, '_')
                clean = clean.strip(' .')
                if len(clean) > 200:
                    clean = clean[:200]
                from pathlib import Path
                import config
                out_dir = config.OUTPUT_ROOT / podcast_name
                candidates = [
                    Path(ep_txt_path) if ep_txt_path else None,
                    out_dir / f"{clean}_文字稿.txt",
                ]
                found_path = None
                for cand in candidates:
                    if cand and cand.exists():
                        found_path = str(cand)
                        break

                if found_path:
                    conn.execute("""
                        UPDATE episodes SET status = 'done_deleted', txt_path = ?, progress = 100, updated_at = ?
                        WHERE id = ?
                    """, (found_path, now, ep_id))
                    print(f"[cleanup] 转写已独立完成，标记 done_deleted: {ep_id} {ep_name}")
                    cleaned += 1
                else:
                    conn.execute("""
                        UPDATE episodes SET status = 'pending', progress = 0, updated_at = ?
                        WHERE id = ?
                    """, (now, ep_id))
                    print(f"[cleanup] 残留任务恢复为 pending: {ep_id} {ep_name}")
                    cleaned += 1
            else:
                conn.execute("""
                    UPDATE episodes SET status = 'done_deleted', progress = 100, updated_at = ?
                    WHERE id = ?
                """, (now, ep_id))
                print(f"[cleanup] txt 已存在，标记 done_deleted: {ep_id} {ep_name}")
                cleaned += 1

        conn.commit()
        return cleaned
    finally:
        conn.close()


def mark_task_done(episode_id: int, txt_path: str = ""):
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes
            SET status = 'done_deleted', progress = 100, txt_path = ?, updated_at = ?
            WHERE id = ?
        """, (txt_path, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def mark_task_failed(episode_id: int, error_msg: str = None):
    conn = get_conn()
    try:
        if error_msg:
            conn.execute("""
                UPDATE episodes
                SET status = 'failed', progress = 0,
                    error_msg = COALESCE(error_msg, ''),
                    updated_at = ?
                WHERE id = ? AND error_msg IS NULL
            """, (datetime.now().isoformat(), episode_id))
        else:
            conn.execute("""
                UPDATE episodes
                SET status = 'failed', progress = 0, updated_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def get_queue_status() -> dict:
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM episodes
            WHERE status IN ('downloading', 'transcribing', 'queued', 'done_deleted', 'failed')
            GROUP BY status
        """)
        return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


# --------------- Re-export（向后兼容）---------------
from repositories.podcast_repo import (
    MANUAL_PID, MANUAL_NAME,
    get_or_create_manual_podcast, add_podcast, upsert_podcast_details,
    get_podcast_details, get_podcast_by_pid, list_podcasts, delete_podcast,
    mark_podcast_viewed, get_podcasts_with_new,
)
from repositories.episode_repo import (
    _is_placeholder, add_episodes, get_episode_by_eid, get_episode_by_name,
    mark_episode_discarded, list_episodes_by_podcast, get_episode_by_id,
    update_episode_status, reset_episode_for_retry, pause_episode,
    update_episode_duration, get_episodes_missing_duration,
    sync_episode_txt_status, sync_podcast_episodes_status,
    cleanup_all_zombie_episodes, get_active_episodes,
    cleanup_placeholder_episodes, mark_episodes_new,
    get_pending_episodes, list_manual_episodes,
    get_recently_completed_episodes,
)

# --------------- 初始化 ---------------

if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
