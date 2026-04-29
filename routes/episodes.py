"""
单集相关路由
/api/episode/*, /api/episodes/*

纯 HTTP 层：解析请求 → 调 service → 返回 JSON 响应。
"""
from flask import Blueprint, request, jsonify
from services import (
    add_episode, enqueue_episodes, retry_episode, reenqueue_episode,
    open_episode_txt, dequeue_episode, pause_episode, reset_episode,
    resume_episode, get_episode,
)

episodes_bp = Blueprint("episodes", __name__, url_prefix="/api")


@episodes_bp.route("/episode/add", methods=["POST"])
def api_add_episode():
    """
    模式B：手动添加单集（统一归入"精选播客"虚拟播客）
    POST body: {"url": "https://www.xiaoyuzhoufm.com/episode/xxx"}
    """
    data = request.get_json()
    url = data.get("url", "").strip()
    result = add_episode(url)
    return jsonify(result)


@episodes_bp.route("/episodes/enqueue", methods=["POST"])
def api_enqueue_episodes():
    """
    将选中的 episode 加入队列
    POST body: {"episode_ids": [1,2,3]}
    """
    data = request.get_json()
    episode_ids = data.get("episode_ids", [])
    result = enqueue_episodes(episode_ids)
    return jsonify(result)


@episodes_bp.route("/episodes/refresh", methods=["POST"])
def api_refresh_episodes():
    """
    重新从网络获取播客集列表
    POST body: {"podcast_id": int}
    """
    data = request.get_json()
    podcast_id = int(data["podcast_id"])
    from services import refresh_podcast
    result = refresh_podcast(podcast_id)
    return jsonify(result)


@episodes_bp.route("/episode/retry/<int:episode_id>", methods=["POST"])
def api_retry_episode(episode_id: int):
    """重新处理失败的 episode"""
    result = retry_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/reenqueue/<int:episode_id>", methods=["POST"])
def api_reenqueue_episode(episode_id: int):
    """重新入队 pending 状态的任务"""
    result = reenqueue_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/open/<int:episode_id>")
def api_episode_open(episode_id: int):
    """用系统程序打开 TXT 文件"""
    result = open_episode_txt(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/<int:episode_id>", methods=["GET"])
def api_get_episode(episode_id: int):
    """获取 episode 详情（含错误信息）"""
    result = get_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/dequeue", methods=["POST"])
def api_dequeue_episode():
    """将 episode 从队列移除，恢复为 pending"""
    data = request.get_json()
    episode_id = int(data["episode_id"])
    result = dequeue_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/pause/<int:episode_id>", methods=["POST"])
def api_episode_pause(episode_id: int):
    """暂停任务：保留音频，状态改为 paused"""
    result = pause_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/reset/<int:episode_id>", methods=["POST"])
def api_episode_reset(episode_id: int):
    """重置任务：删除音频，状态改为 pending"""
    result = reset_episode(episode_id)
    return jsonify(result)


@episodes_bp.route("/episode/resume/<int:episode_id>", methods=["POST"])
def api_episode_resume(episode_id: int):
    """继续暂停的任务"""
    result = resume_episode(episode_id)
    return jsonify(result)
