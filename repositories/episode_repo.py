"""
Episode Repository
VoiceToFile — 小宇宙播客转文字
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "voicetofile.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _is_placeholder(name: str, duration: str) -> bool:
    if not name:
        return True
    if len(name) < 7:
        return True
    if " | " in name:
        parts = name.split(" | ")
        if all(len(p.strip()) < 8 for p in parts):
            return True
    return False


def add_episodes(episodes: list[dict], source: str = "subscribe"):
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


def get_episode_by_eid(podcast_id: int, eid: str) -> Optional[dict]:
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
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_episode_status(episode_id: int, status: str, txt_path: str = "", error_msg: str = ""):
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


def reset_episode_for_retry(episode_id: int):
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
    if not duration:
        return
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE episodes SET duration = ?, updated_at = ? WHERE id = ?
        """, (duration, datetime.now().isoformat(), episode_id))
        conn.commit()
    finally:
        conn.close()


def get_episodes_missing_duration(podcast_id: int) -> list[dict]:
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
    episodes = list_episodes_by_podcast(podcast_id)
    count = 0
    for ep in episodes:
        if sync_episode_txt_status(ep["id"]):
            count += 1
    return count


def cleanup_all_zombie_episodes() -> int:
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
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM episodes WHERE LENGTH(name) < 7")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_episodes_new(podcast_id: int, eids: list[str]):
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
