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
from scraper import fetch_episode_info
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

    # 订阅时验证所有集的音频（新增播客，需要确认每个集都能用）
    from scraper import fetch_episodes_audio_info
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

    优化：只对新增集验证音频，已存在集直接用播客页面的元数据，不发请求。

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

    conn = db.get_conn()

    # 1. 先查 DB 已有的 eid（在发请求之前）
    existing_eids = set(
        row["eid"] for row in conn.execute(
            "SELECT eid FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    )

    # 2. 分离新增集和已有集
    new_eps = [ep for ep in info.episodes if ep.eid not in existing_eids]
    old_eps = [ep for ep in info.episodes if ep.eid in existing_eids]

    addLog(f"[刷新] 网络 {len(info.episodes)} 集，已有 {len(existing_eids)} 集，新增 {len(new_eps)} 集", "done")

    # 3. 只对新增集验证音频
    if new_eps:
        from scraper import fetch_episodes_audio_info
        episodes_with_audio = fetch_episodes_audio_info(new_eps)
        valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
        skipped = len(new_eps) - len(valid_episodes)
        if skipped > 0:
            addLog(f"[刷新] 跳过 {skipped} 集（无音频，占位集）", "done")
    else:
        valid_episodes = []

    # 4. 写入所有集（INSERT OR IGNORE，新集入库）
    all_ep_records = []
    # 新增集：用 fetch 后的详细数据
    for ep in valid_episodes:
        all_ep_records.append({
            "podcast_id": podcast_id,
            "eid": ep["eid"],
            "name": ep["name"],
            "pub_date": ep["pub_date"],
            "duration": ep["duration"],
            "is_paid": ep["is_paid"],
        })
    # 已有集：用播客页面的元数据（可能标题/日期有变化）
    for ep in old_eps:
        all_ep_records.append({
            "podcast_id": podcast_id,
            "eid": ep.eid,
            "name": ep.name,
            "pub_date": ep.pub_date,
            "duration": "",
            "is_paid": ep.is_paid,
        })

    if all_ep_records:
        db.add_episodes(all_ep_records)

    # 5. 更新已有集的时长（新增集如果在 fetch 时拿到了时长也要更新）
    for ep in valid_episodes:
        if ep["eid"] in existing_eids and ep["duration"]:
            conn.execute(
                "UPDATE episodes SET duration = ? WHERE podcast_id = ? AND eid = ? AND duration != ?",
                (ep["duration"], podcast_id, ep["eid"], ep["duration"])
            )
    # 已有集（未 fetch）的时长：如果播客页面有，用播客页面的（但不从详情页 fetch）
    for ep in old_eps:
        if ep.duration:
            conn.execute(
                "UPDATE episodes SET duration = ? WHERE podcast_id = ? AND eid = ? AND (duration = '' OR duration IS NULL)",
                (ep.duration, podcast_id, ep.eid)
            )
    conn.commit()

    # 6. 同步已有 episode 的文件状态：txt 文件存在但 status 非 done_deleted → 修正
    podcast_dir = config.OUTPUT_ROOT / p["name"]
    existing_eps_rows = list(conn.execute(
        "SELECT id, name, status, txt_path FROM episodes WHERE podcast_id = ? AND status != 'done_deleted'",
        (podcast_id,)
    ).fetchall())
    fixed_count = 0
    for ep_row in existing_eps_rows:
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

    new_eids = [ep["eid"] for ep in valid_episodes]
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
