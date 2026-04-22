"""
播客 Service
VoiceToFile — 小宇宙播客转文字
"""
import os
from datetime import datetime
from pathlib import Path

import db
import scraper
import config
from scraper import fetch_episodes_audio_info
from sse import addLog, task_update
from _utils import get_txt_path


def subscribe_podcast(url: str, pid: str) -> dict:
    """
    模式A：订阅播客（从 URL 或 PID）

    返回 dict：
      - ok: bool
      - podcast_id: int
      - name: str
      - episodes: list[dict]
      - error: str（ok=False 时）
    """
    if not pid:
        pid = scraper.extract_pid(url)
    if not pid:
        return {"ok": False, "error": "无法从 URL 提取 PID"}

    addLog(f"[播客] 正在获取: {pid}", "tag")

    info = scraper.fetch_podcast_info(pid, interval=config.COOKIE_INTERVAL)
    addLog(f"[播客] 名称: {info.name}，共 {len(info.episodes)} 集，正在验证音频...", "done")

    episodes_with_audio = fetch_episodes_audio_info(info.episodes)
    valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
    skipped = len(info.episodes) - len(valid_episodes)
    if skipped > 0:
        addLog(f"[播客] 跳过 {skipped} 集（无音频，占位集）", "done")

    podcast_id = db.add_podcast(pid, info.name)

    if info.author or info.subscriber_count:
        db.upsert_podcast_details(
            podcast_id=podcast_id,
            author=info.author,
            description=info.description,
            cover_url=info.cover_url,
            subscriber_count=info.subscriber_count,
            episode_count=info.episode_count,
        )

    ep_records = [{
        "podcast_id": podcast_id,
        "eid": ep["eid"],
        "name": ep["name"],
        "pub_date": ep["pub_date"],
        "duration": ep["duration"],
        "is_paid": ep["is_paid"],
    } for ep in valid_episodes]
    db.add_episodes(ep_records)

    from _utils import format_duration
    return {
        "ok": True,
        "podcast_id": podcast_id,
        "pid": pid,
        "name": info.name,
        "episodes": [
            {
                "eid": ep["eid"],
                "name": ep["name"],
                "pub_date": ep["pub_date"][:10] if ep["pub_date"] else "",
                "duration": ep["duration"],
                "duration_str": format_duration(ep["duration"]),
                "is_paid": ep["is_paid"],
                "paid_price": ep.get("paid_price"),
                "description": ep.get("description", "")[:100],
            }
            for ep in valid_episodes
        ],
    }


def refresh_podcast(podcast_id: int) -> dict:
    """
    刷新播客：从网络获取最新集列表

    返回 dict：
      - ok: bool
      - count: int（总集数）
      - new_count: int（新增集数）
      - new_eids: list[str]
      - podcast_name: str
      - error: str（ok=False 时）
    """
    p = db.get_conn().execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
    if not p:
        return {"ok": False, "error": "播客不存在"}
    pid = p["pid"]

    info = scraper.fetch_podcast_info(pid, interval=config.COOKIE_INTERVAL)

    if info.name:
        db.add_podcast(pid, info.name)
        updated_name = info.name

    if info.author or info.subscriber_count:
        db.upsert_podcast_details(
            podcast_id=podcast_id,
            author=info.author,
            description=info.description,
            cover_url=info.cover_url,
            subscriber_count=info.subscriber_count,
            episode_count=info.episode_count,
        )

    episodes_with_audio = fetch_episodes_audio_info(info.episodes)
    valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
    skipped = len(info.episodes) - len(valid_episodes)
    if skipped > 0:
        addLog(f"[刷新] 跳过 {skipped} 集（无音频，占位集）", "done")

    # 刷新前记录 DB 中已有的 eid
    conn = db.get_conn()
    existing_eids = set(
        row["eid"] for row in conn.execute(
            "SELECT eid FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    )

    ep_records = []
    for ep in valid_episodes:
        ep_records.append({
            "podcast_id": podcast_id,
            "eid": ep["eid"],
            "name": ep["name"],
            "pub_date": ep["pub_date"],
            "duration": ep["duration"],
            "is_paid": ep["is_paid"],
        })

    db.add_episodes(ep_records)

    # 更新已有记录的时长（INSERT OR IGNORE 不更新现有记录）
    for ep in valid_episodes:
        if ep["eid"] in existing_eids and ep["duration"]:
            conn.execute(
                "UPDATE episodes SET duration = ? WHERE podcast_id = ? AND eid = ? AND duration != ?",
                (ep["duration"], podcast_id, ep["eid"], ep["duration"])
            )
    conn.commit()

    # 同步已有 episode 的文件状态：txt 文件存在但 status 非 done_deleted → 修正
    podcast_dir = config.OUTPUT_ROOT / p["name"]
    existing_eps = list(conn.execute(
        "SELECT id, name, status, txt_path FROM episodes WHERE podcast_id = ? AND status != 'done_deleted'",
        (podcast_id,)
    ).fetchall())
    fixed_count = 0
    for ep_row in existing_eps:
        ep_id, ep_name, ep_status, ep_txt_path = ep_row
        if ep_txt_path and os.path.exists(ep_txt_path):
            continue
        txt_file = get_txt_path(podcast_dir, ep_name)
        if txt_file.exists():
            conn.execute(
                "UPDATE episodes SET status = 'done_deleted', txt_path = ?, updated_at = ? WHERE id = ?",
                (str(txt_file), datetime.now().isoformat(), ep_id)
            )
            fixed_count += 1
    if fixed_count > 0:
        conn.commit()
        addLog(f"[刷新] 修正 {fixed_count} 个 episode 状态（文件存在但 DB 未同步）", "done")

    new_eids = [ep["eid"] for ep in valid_episodes if ep["eid"] not in existing_eids]
    new_count = len(new_eids)

    if new_eids:
        db.mark_episodes_new(podcast_id, new_eids)

    addLog(f"[刷新] 完成，共 {len(info.episodes)} 集，新增 {new_count} 集", "done")
    return {
        "ok": True,
        "count": len(info.episodes),
        "new_count": new_count,
        "new_eids": new_eids,
        "podcast_name": updated_name,
    }
