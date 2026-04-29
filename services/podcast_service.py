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
from sse import addLog, task_update, broadcast_sse
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
        broadcast_sse("podcast_refresh_done", {
            "podcast_name": f"#{podcast_id}",
            "new_count": 0, "result": "failed", "error": "播客不存在",
        })
        return {"ok": False, "error": "播客不存在"}
    pid = p["pid"]

    info = scraper.fetch_podcast_info(pid, interval=config.COOKIE_INTERVAL)

    updated_name = p["name"]
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

    # 1. 查 DB 已有 episode：eid -> episode（用于检测 eid 漂移）
    # 必须查所有行（含 discarded=1），否则废弃 episode 的 eid 被视为"新"，INSERT 会 UNIQUE 冲突
    existing_by_eid = {  # eid -> {id, name}
        row["eid"]: {"id": row["id"], "name": row["name"]}
        for row in conn.execute(
            "SELECT id, eid, name FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    }
    existing_eids = set(existing_by_eid.keys())

    # 2. 查 DB 已有 episode：name -> episode（用于检测 name 漂移）
    existing_by_name = {
        row["name"]: {"id": row["id"], "eid": row["eid"]}
        for row in conn.execute(
            "SELECT id, eid, name FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    }

    # 3. 处理 eid 漂移（立即提交，避免被后面 add_episodes 异常打断）
    #    注意：existing_by_name 是 dict，同名 episode 只能保留一条
    #    因此同一 name 有多条时，只更新 id 最小的那条（入库最早的）
    new_eps = []
    updated_eid_count = 0
    skipped_eid_conflict = 0

    for ep in info.episodes:
        if ep.eid in existing_by_eid:
            stored_name = existing_by_eid[ep.eid]["name"]
            if stored_name != ep.name:
                skipped_eid_conflict += 1
                addLog(f"[刷新] eid {ep.eid[:12]}... 被分配给了「{ep.name[:20]}」，原有「{stored_name[:20]}」，跳过", "done")
        elif ep.name in existing_by_name:
            old_record = existing_by_name[ep.name]
            conn.execute(
                "UPDATE episodes SET eid = ?, pub_date = ?, duration = ? WHERE id = ?",
                (ep.eid, ep.pub_date, ep.duration, old_record["id"])
            )
            updated_eid_count += 1
            addLog(f"[刷新] 更新 episode eid：{ep.name[:20]} ({old_record['eid'][:12]} -> {ep.eid[:12]})", "done")
        else:
            new_eps.append(ep)

    if updated_eid_count > 0:
        conn.commit()
    addLog(f"[刷新] 网络 {len(info.episodes)} 集，已有 {len(existing_by_eid)} 集，新增 {len(new_eps)} 集，更新 eid {updated_eid_count} 集，跳过 eid 冲突 {skipped_eid_conflict} 集", "done")

    # 4. 重新构建 existing_by_name（更新后的 eid 需要同步到查找表）
    existing_by_name = {
        row["name"]: {"id": row["id"], "eid": row["eid"]}
        for row in conn.execute(
            "SELECT id, eid, name FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    }

    # 5. 只对新增集验证音频
    if new_eps:
        from scraper import fetch_episodes_audio_info
        episodes_with_audio = fetch_episodes_audio_info(new_eps)
        valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
        skipped = len(new_eps) - len(valid_episodes)
        if skipped > 0:
            addLog(f"[刷新] 跳过 {skipped} 集（无音频，占位集）", "done")
    else:
        valid_episodes = []

    # 4. 写入新增集（占位集过滤）
    podcast_name = info.name
    ep_records = []
    for ep in valid_episodes:
        if ep["name"] == podcast_name:
            addLog(f"[过滤] 跳过占位集: {ep['name'][:30]}", "done")
            continue
        ep_records.append({
            "podcast_id": podcast_id,
            "eid": ep["eid"],
            "name": ep["name"],
            "pub_date": ep["pub_date"],
            "duration": ep["duration"],
            "is_paid": ep["is_paid"],
        })
    if ep_records:
        db.add_episodes(ep_records)

    # 5. 更新已有集的 pub_date 和 duration（已有集通过步骤1的 name 匹配更新了 eid，这里补全元数据）
    for ep in info.episodes:
        if ep.eid in existing_by_eid:
            stored_name = existing_by_eid[ep.eid]["name"]
            if stored_name == ep.name and (ep.pub_date or ep.duration):
                conn.execute(
                    "UPDATE episodes SET pub_date = COALESCE(NULLIF(pub_date, ''), ?), duration = COALESCE(NULLIF(duration, ''), ?) WHERE podcast_id = ? AND eid = ?",
                    (ep.pub_date or '', ep.duration or '', podcast_id, ep.eid)
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

    # 刷新后更新 added_at，让播客升到列表顶部
    conn.execute(
        "UPDATE podcasts SET added_at = ? WHERE id = ?",
        (datetime.now().isoformat(), podcast_id)
    )
    conn.commit()

    addLog(f"[刷新] 完成，共 {len(info.episodes)} 集，新增 {new_count} 集", "done")

    broadcast_sse("podcast_refresh_done", {
        "podcast_name": updated_name,
        "new_count": new_count,
        "result": "success" if new_count > 0 else "no_update",
        "error": None,
    })

    return {
        "ok": True,
        "count": len(info.episodes),
        "new_count": new_count,
        "new_eids": new_eids,
        "podcast_name": updated_name,
    }


def delete_podcast(podcast_id: int) -> dict:
    """删除播客订阅及其所有 episodes。"""
    db.delete_podcast(podcast_id)
    return {"ok": True}


def get_podcast_episodes(podcast_id: int) -> dict:
    """
    获取播客详情及其 episode 列表（含文件存在性检查）。

    返回 dict：
      - ok: bool
      - podcast: dict
      - episodes: list[dict]
      - error: str（ok=False 时）
    """
    from _utils import format_duration

    conn = db.get_conn()
    try:
        podcast = conn.execute(
            "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
        ).fetchone()
        if not podcast:
            return {"ok": False, "error": "播客不存在"}

        db.sync_podcast_episodes_status(podcast_id)
        episodes = db.list_episodes_by_podcast(podcast_id)

        return {
            "ok": True,
            "podcast": dict(podcast),
            "episodes": [
                {
                    "id": e["id"],
                    "eid": e["eid"],
                    "name": e["name"],
                    "pub_date": e["pub_date"],
                    "duration": e["duration"],
                    "duration_str": format_duration(e["duration"]) if e["duration"] else "",
                    "is_paid": e["is_paid"],
                    "status": e["status"],
                    "txt_path": e["txt_path"],
                    "txt_exists": os.path.exists(e["txt_path"]) if e["txt_path"] else False,
                }
                for e in episodes
            ],
        }
    finally:
        conn.close()


def open_podcast_folder(podcast_id: int) -> dict:
    """打开播客输出文件夹。"""
    conn = db.get_conn()
    try:
        p = conn.execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
        if not p:
            return {"ok": False, "error": "播客不存在"}
        folder = w.get_output_dir(p["name"])
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def mark_podcast_viewed(podcast_id: int) -> dict:
    """用户展开播客后，清除该播客的新集标记。"""
    db.mark_podcast_viewed(podcast_id)
    return {"ok": True}
