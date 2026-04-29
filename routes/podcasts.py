"""
播客相关路由
/api/podcast/*

纯 HTTP 层：解析请求 → 调 service → 返回 JSON 响应。
"""
from flask import Blueprint, request, jsonify
from services import subscribe_podcast, delete_podcast, get_podcast_episodes
from services import open_podcast_folder, mark_podcast_viewed

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
    return jsonify(result)


@podcasts_bp.route("/delete", methods=["POST"])
def api_delete_podcast():
    """删除播客订阅"""
    data = request.get_json()
    result = delete_podcast(int(data["podcast_id"]))
    return jsonify(result)


@podcasts_bp.route("/<int:podcast_id>/episodes")
def api_podcast_episodes(podcast_id: int):
    """获取播客的剧集列表"""
    result = get_podcast_episodes(podcast_id)
    return jsonify(result)


@podcasts_bp.route("/open/<int:podcast_id>")
def api_podcast_open(podcast_id: int):
    """打开播客输出文件夹"""
    result = open_podcast_folder(podcast_id)
    return jsonify(result)


@podcasts_bp.route("/viewed/<int:podcast_id>", methods=["POST"])
def api_podcast_viewed(podcast_id: int):
    """用户展开播客后，清除该播客的新集标记"""
    result = mark_podcast_viewed(podcast_id)
    return jsonify(result)
