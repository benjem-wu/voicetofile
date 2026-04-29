"""
单集数据库操作（含队列 v2）
"""
import os
import re
from typing import Optional
from datetime import datetime
from pathlib import Path

from .connection import get_conn
from . import connection as _conn_mod


# --------------- 内部 helper ---------------

def _is_placeholder(name: str, duration: str) -> bool:
    """
    判断是否为无音频的占位集
    - 空名称
    - 很短（7字符以下）
    - 格式 "短名 | 短名" 的分类标题
    """
    if not name:
        return True
    if len(name) < 7:
        return True
    if " | " in name:
        parts = name.split(" | ")
        if all(len(p.strip()) < 8 for p in parts):
            return True
    return False


def _parse_duration_to_minutes(duration: str) -> int:
    """将 ISO 8601 duration (PT28M) 转为分钟整数，无法解析返回 0"""
    if not duration:
        return 0
    m = re.search(r'(\d+)M', duration)
    if m:
        return int(m.group(1))
    return 0


# --------------- 基本 CRUD ---------------

def add_episodes(episodes: list[dict], source: str = "subscribe"):
    """
    批量添加 episodes（已存在于 DB 则忽略）
    过滤逻辑：满足以下条件之一即可入库
      - 条件A：有 audio_url（非空字符串）
      - 条件B：非占位集
    """
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
    """标记 episode 为废弃"""
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
    """更新 episode 的 duration"""
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


# --------------- 状态同步 ---------------

def sync_episode_txt_status(episode_id: int) -> bool:
    """
    检查 episode 的 txt 文件是否真实存在。
    返回 True if status was changed.
    """
    ep = get_episode_by_id(episode_id)
    if not ep:
        return False
    txt = ep.get("txt_path") or ""
    txt_exists = bool(txt) and os.path.exists(txt)

    changed = False
    if ep["status"] == "transcribing" and not txt_exists:
        update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        changed = True
    elif ep["status"] == "done_deleted" and not txt_exists:
        update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        changed = True
    return changed


def sync_podcast_episodes_status(podcast_id: int) -> int:
    """对某播客下所有 episode 检查文件存在性并修正状态。返回修正的 episode 数量。"""
    episodes = list_episodes_by_podcast(podcast_id)
    count = 0
    for ep in episodes:
        if sync_episode_txt_status(ep["id"]):
            count += 1
    return count


def cleanup_all_zombie_episodes() -> int:
    """全局清理：所有 status=transcribing 且无 txt_path 的 episode 重置为 pending。"""
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


# --------------- 查询 ---------------

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


def get_pending_episodes(limit: int = 50) -> list[dict]:
    """获取所有排队中的任务（queued），按加入时间升序"""
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
    """获取最近完成/失败的任务，按完成时间倒序，最多 limit 条"""
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


# --------------- 队列 v2 ---------------

def get_next_queued_task() -> Optional[dict]:
    """
    原子操作：抢一个 queued 任务，标记为 downloading。
    使用 UPDATE ... RETURNING（SQLite 3.35+），保证并发安全。
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
    """
    将 pending/failed 状态改为 queued（入队）。
    同时重置 retry_count = 0。
    返回 True 表示入队成功。
    """
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
    """将 episode 的 retry_count 加 1，返回新的计数"""
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
    处理 downloading/transcribing 残留任务。
    """
    # 延迟导入避免循环依赖
    import config

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
    """处理完成：downloading/transcribing → done_deleted"""
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
    """处理失败：downloading/transcribing → failed"""
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
