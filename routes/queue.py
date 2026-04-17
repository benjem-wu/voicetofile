"""
队列相关路由
/api/queue/*
"""
from flask import Blueprint, jsonify

import db
import worker as w
from sse import broadcast_sse

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")


@queue_bp.route("/stop", methods=["GET", "POST"])
def api_queue_stop():
    """
    终止当前正在处理的任务（简单粗暴版）：
    - worker 线程内部杀进程 + 删音频文件
    - DB 状态改 pending
    - 广播 SSE，前端自动刷新
    """
    episode_id = w.terminate_current_task()

    if episode_id:
        db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")

    # 必须在 DB 更新之后再广播，否则前端 SSE 触发 loadQueue 时 DB 还是旧状态
    broadcast_sse("task_stopped", {"episode_id": episode_id})

    return jsonify({"ok": True, "episode_id": episode_id})


@queue_bp.route("")
def api_queue():
    """获取当前队列状态（纯 DB）"""
    conn = db.get_conn()
    try:
        cur = conn.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('downloading', 'transcribing', 'queued', 'done_deleted', 'failed')
            ORDER BY e.updated_at DESC
            LIMIT 50
        """)
        tasks = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"tasks": tasks})
