"""
SQLite 数据库模块
VoicToFile — 小宇宙播客转文字
"""
import os
import re
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
        # 兼容已有数据库：progress 列可能不存在
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 兼容已有数据库：is_new 列可能不存在（新集标记，展开后清除）
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 兼容已有数据库：audio_path 列可能不存在（暂停时存储音频路径）
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN audio_path TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 兼容已有数据库：discarded 列可能不存在（废弃标记，保留最长版）
        try:
            cur.execute("ALTER TABLE episodes ADD COLUMN discarded INTEGER NOT NULL DEFAULT 0")
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

        # podcast_details 表（播客元数据，与 podcasts 一对一）
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


def upsert_podcast_details(
    podcast_id: int,
    author: str = "",
    description: str = "",
    cover_url: str = "",
    subscriber_count: int = 0,
    episode_count: int = 0,
):
    """
    插入或更新播客详情。
    podcast_details 与 podcasts 是 1:1 关系，主键 podcast_id。
    """
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
        cur.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        conn.commit()
    finally:
        conn.close()


# --------------- Episodes ---------------

def _is_placeholder(name: str, duration: str) -> bool:
    """
    判断是否为无音频的占位集
    - 空名称
    - 很短（7字符以下）
    - 格式 "短名 | 短名" 的分类标题（如"半拿铁 | 商业沉浮录"）
    """
    if not name:
        return True
    if len(name) < 7:
        return True
    # "xxx | xxx" 格式通常是播客内部分类标题，不是真实集
    if " | " in name:
        parts = name.split(" | ")
        if all(len(p.strip()) < 8 for p in parts):
            return True
    return False


def add_episodes(episodes: list[dict], source: str = "subscribe"):
    """
    批量添加 episodes（已存在于 DB 则忽略）
    过滤逻辑：满足以下条件之一即可入库
      - 条件A：有 audio_url（非空字符串）
      - 条件B：非占位集（name >= 7 字，且含有效标题）
    episodes 格式：[{
        "podcast_id": int,
        "eid": str,
        "name": str,
        "pub_date": str,
        "duration": str,
        "is_paid": bool,
        "audio_url": str,   # 可选，有则用条件A
    }]
    source: 'subscribe'（订阅列表）或 'manual'（手动添加）
    """
    # 过滤占位集：必须有 audio_url 或名称符合有效集标准
    valid = [
        ep for ep in episodes
        if ep.get("audio_url") or not _is_placeholder(ep.get("name", ""), ep.get("duration", ""))
    ]

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


def _parse_duration_to_minutes(duration: str) -> int:
    """将 ISO 8601  duration (PT28M) 转为分钟整数，无法解析返回 0"""
    if not duration:
        return 0
    m = re.search(r'(\d+)M', duration)
    if m:
        return int(m.group(1))
    return 0


def get_episode_by_name(podcast_id: int, name: str) -> Optional[dict]:
    """根据 podcast_id + name 查找未废弃的 episode"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM episodes WHERE podcast_id = ? AND name = ? AND discarded = 0",
            (podcast_id, name)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_episode_discarded(episode_id: int):
    """标记 episode 为废弃（不展示，但保留记录）"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE episodes SET discarded = 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), episode_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_episodes_by_podcast(podcast_id: int) -> list[dict]:
    """列出某播客的所有 episodes（排除已废弃），按发布日期倒序"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM episodes
            WHERE podcast_id = ? AND discarded = 0
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
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes
            SET status = 'pending', txt_path = '', error_msg = '',
                audio_path = '', updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def pause_episode(episode_id: int, audio_path: str = ""):
    """暂停 episode：状态改为 paused，存储音频路径"""
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes
            SET status = 'paused', audio_path = ?, updated_at = ?
            WHERE id = ?
        """, (audio_path, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def update_episode_duration(episode_id: int, duration: str):
    """更新 episode 的 duration（用于补全或刷新时长数据）"""
    if not duration:
        return
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes
            SET duration = ?, updated_at = ?
            WHERE id = ?
        """, (duration, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def get_episodes_missing_duration(podcast_id: int) -> list[dict]:
    """返回某播客在 DB 中 duration 为空的 episodes"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, eid, name, duration FROM episodes
            WHERE podcast_id = ? AND (duration IS NULL OR duration = '')
            ORDER BY pub_date DESC
        """, (podcast_id,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


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


def mark_podcast_viewed(podcast_id: int):
    """用户展开某播客后，清除该播客所有集的新集标记（is_new=0）"""
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


def mark_episodes_new(podcast_id: int, eids: list[str]):
    """标记指定 eid 列表为新集（is_new=1）"""
    if not eids:
        return
    conn = get_conn()
    try:
        placeholders = ','.join('?' * len(eids))
        conn.execute(f"""
            UPDATE episodes
            SET is_new = 1, updated_at = ?
            WHERE podcast_id = ? AND eid IN ({placeholders})
        """, (datetime.now().isoformat(), podcast_id, *eids))
        conn.commit()
    finally:
        conn.close()


def get_podcasts_with_new() -> list[int]:
    """获取所有包含新集（is_new=1）的 podcast_id 列表"""
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT DISTINCT podcast_id FROM episodes WHERE is_new = 1
        """)
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_pending_episodes():
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


# --------------- 队列 v2（DB 唯一数据源）---------------

def get_next_queued_task() -> Optional[dict]:
    """
    原子操作：抢一个 queued 任务，标记为 downloading，同时返回任务信息。
    使用 UPDATE ... RETURNING（SQLite 3.35+），保证并发安全。
    如果没有 queued 任务，返回 None。
    """
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
            RETURNING id, eid, name, podcast_id, status
        """, (datetime.now().isoformat(),))
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        task = dict(row)
        # 补充 podcast_name（get_output_dir 需要）
        cur2 = conn.execute("SELECT name FROM podcasts WHERE id = ?", (task["podcast_id"],))
        pRow = cur2.fetchone()
        task["podcast_name"] = pRow["name"] if pRow else ""
        return task
    finally:
        conn.close()


def enqueue_task(episode_id: int) -> bool:
    """
    将 pending/failed 状态改为 queued（入队）。
    返回 True 表示入队成功，False 表示状态不允许（如已在队列中）。
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE episodes
            SET status = 'queued', updated_at = ?
            WHERE id = ? AND status IN ('pending', 'failed')
        """, (datetime.now().isoformat(), episode_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_task_progress(episode_id: int, progress: int):
    """更新转写进度（0-100）"""
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes SET progress = ?, updated_at = ?
            WHERE id = ?
        """, (progress, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def cleanup_stale_tasks() -> int:
    """
    Flask 启动时调用。
    将 downloading/transcribing 状态的残留任务恢复为 pending（进程崩溃/强制退出后遗留）。
    音频/显存可能已泄漏，但 DB 记录保留，供用户手动重新入队。
    返回清理的任务数量。
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE episodes
            SET status = 'pending', progress = 0, updated_at = ?
            WHERE status IN ('downloading', 'transcribing')
        """, (datetime.now().isoformat(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_task_done(episode_id: int, txt_path: str = ""):
    """处理完成：downloading/transcribing → done_deleted，progress=100，同时记录 txt 路径"""
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
    """处理失败：downloading/transcribing → failed，progress=0"""
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
    """返回各状态的数量统计，供前端展示用"""
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


# --------------- 初始化 ---------------

if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
