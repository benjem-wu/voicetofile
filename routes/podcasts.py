"""
播客相关路由
/api/podcast/*
"""
import os
from flask import Blueprint, request, jsonify

import db
import scraper
from _utils import format_duration
import worker as w

podcasts_bp = Blueprint("podcasts", __name__, url_prefix="/api/podcast")


@podcasts_bp.route("/fetch", methods=["POST"])
def api_fetch_podcast():
    """
    模式A：从 URL 或 PID 获取播客信息
    POST body: {"url": "..."} 或 {"pid": "..."}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sse import addLog
    import config

    data = request.get_json()
    url = data.get("url", "").strip()
    pid = data.get("pid", "").strip()

    if not pid:
        pid = scraper.extract_pid(url)
    if not pid:
        return jsonify({"ok": False, "error": "无法从 URL 提取 PID"})

    addLog(f"[播客] 正在获取: {pid}", "tag")

    try:
        info = scraper.fetch_podcast_info(pid, interval=config.COOKIE_INTERVAL)
        addLog(f"[播客] 名称: {info.name}，共 {len(info.episodes)} 集，正在验证音频...", "done")

        def fetch_one_audio(ep):
            detail = scraper.fetch_episode_info(ep.eid, interval=1)
            return {
                "eid": ep.eid,
                "name": ep.name,
                "pub_date": ep.pub_date,
                "duration": ep.duration,
                "is_paid": ep.is_paid,
                "paid_price": getattr(ep, "paid_price", None),
                "description": getattr(ep, "description", ""),
                "has_audio": bool(detail.audio_url),
            }

        episodes_with_audio = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fetch_one_audio, ep): ep for ep in info.episodes}
            for future in as_completed(futures):
                episodes_with_audio.append(future.result())

        valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
        skipped = len(info.episodes) - len(valid_episodes)
        if skipped > 0:
            addLog(f"[播客] 跳过 {skipped} 集（无音频，占位集）", "done")

        podcast_id = db.add_podcast(pid, info.name)

        ep_records = [{
            "podcast_id": podcast_id,
            "eid": ep["eid"],
            "name": ep["name"],
            "pub_date": ep["pub_date"],
            "duration": ep["duration"],
            "is_paid": ep["is_paid"],
        } for ep in valid_episodes]
        db.add_episodes(ep_records)

        return jsonify({
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
            ]
        })
    except Exception as e:
        addLog(f"[错误] 获取失败: {e}", "err")
        return jsonify({"ok": False, "error": str(e)})


@podcasts_bp.route("/delete", methods=["POST"])
def api_delete_podcast():
    """删除播客订阅"""
    data = request.get_json()
    db.delete_podcast(int(data["podcast_id"]))
    return jsonify({"ok": True})


@podcasts_bp.route("/<int:podcast_id>/episodes")
def api_podcast_episodes(podcast_id: int):
    """获取播客的剧集列表"""
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return jsonify({"ok": False, "error": "播客不存在"})

    db.sync_podcast_episodes_status(podcast_id)
    episodes = db.list_episodes_by_podcast(podcast_id)

    return jsonify({
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
        ]
    })


@podcasts_bp.route("/open/<int:podcast_id>")
def api_podcast_open(podcast_id: int):
    """打开播客输出文件夹"""
    p = db.get_conn().execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
    if not p:
        return jsonify({"ok": False, "error": "播客不存在"})
    folder = w.get_output_dir(p["name"])
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(folder)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@podcasts_bp.route("/viewed/<int:podcast_id>", methods=["POST"])
def api_podcast_viewed(podcast_id: int):
    """用户展开播客后，清除该播客的新集标记"""
    db.mark_podcast_viewed(podcast_id)
    return jsonify({"ok": True})
