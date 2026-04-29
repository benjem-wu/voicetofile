"""
单集相关路由
/api/episode/*, /api/episodes/*

纯 HTTP 层：解析请求 → 调 service → 返回 JSON 响应。
"""
import threading
from flask import Blueprint, request, jsonify
from services import (
    add_episode, enqueue_episodes, retry_episode, reenqueue_episode,
    open_episode_txt, dequeue_episode, pause_episode, reset_episode,
    resume_episode, get_episode, refresh_podcast,
)
from sse import broadcast_sse, addLog
import db

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


# --------------- 批量刷新 ---------------

@episodes_bp.route("/episodes/refresh-all", methods=["POST"])
def api_refresh_all():
    """
    批量刷新所有播客（或指定播客列表）。
    后台逐个刷新，通过 SSE 广播进度和结果。
    POST body: {}（全部）或 {"podcast_ids": [1,2,3]}（选中）
    """
    data = request.get_json() or {}
    podcast_ids = data.get("podcast_ids")

    t = threading.Thread(target=_batch_refresh, args=(podcast_ids,), daemon=True)
    t.start()
    return jsonify({"ok": True})


def _batch_refresh(podcast_ids=None):
    """后台批量刷新任务：逐个处理、广播 SSE"""
    addLog("[批量刷新] 开始", "tag")
    conn = db.get_conn()
    try:
        if podcast_ids:
            placeholders = ",".join("?" * len(podcast_ids))
            rows = conn.execute(
                f"SELECT id, name FROM podcasts WHERE id IN ({placeholders})",
                podcast_ids,
            ).fetchall()
            addLog(f"[批量刷新] 选中 {len(rows)} 个播客", "done")
        else:
            rows = conn.execute(
                "SELECT id, name FROM podcasts WHERE pid != ? ORDER BY added_at DESC",
                (db.MANUAL_PID,),
            ).fetchall()
            addLog(f"[批量刷新] 全部共 {len(rows)} 个播客", "done")
    except Exception as e:
        addLog(f"[批量刷新] 查询播客列表失败: {e}", "tag")
        return
    finally:
        conn.close()

    total = len(rows)
    details = []

    for row in rows:
        try:
            result = refresh_podcast(row["id"])
            # refresh_podcast 内部已广播 podcast_refresh_done SSE
            details.append({
                "podcast_name": row["name"],
                "result": "success" if result.get("new_count", 0) > 0 else "no_update",
                "new_count": result.get("new_count", 0),
            })
        except Exception as e:
            broadcast_sse("podcast_refresh_done", {
                "podcast_name": row["name"],
                "new_count": 0,
                "result": "failed",
                "error": str(e),
            })
            details.append({
                "podcast_name": row["name"],
                "result": "failed",
                "new_count": 0,
            })

    success_details = [d for d in details if d["result"] == "success"]
    total_new = sum(d["new_count"] for d in success_details)
    failed_count = sum(1 for d in details if d["result"] == "failed")

    broadcast_sse("refresh_all_complete", {
        "total": total,
        "total_new_episodes": total_new,
        "failed_count": failed_count,
        "details": details,
    })

    addLog(f"[批量刷新] 完成: {total} 个播客, 新增 {total_new} 集, 失败 {failed_count} 个", "done")
