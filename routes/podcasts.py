"""
播客相关路由
/api/podcast/*
"""
import os
from flask import Blueprint, request, jsonify

import db
import worker as w
from _utils import format_duration
from services import subscribe_podcast

podcasts_bp = Blueprint("podcasts", __name__, url_prefix="/api/podcast")


@podcasts_bp.route("/fetch", methods=["POST"])
def api_fetch_podcast():
    """
    模式A：从 URL 或 PID 获取播客信息
    POST body: {"url": "..."} 或 {"pid": "..."}
    """
    data = request.get_json()
    url = data.get("url", "").strip()
    pid = data.get("pid", "").strip()

    result = subscribe_podcast(url, pid)
    if result["ok"]:
        return jsonify(result)
    return jsonify(result)


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
