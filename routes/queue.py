"""
队列相关路由
/api/queue/*
"""
import threading
from flask import Blueprint, jsonify
from pathlib import Path

import db
import worker as w
from sse import broadcast_sse

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")


@queue_bp.route("/stop", methods=["GET", "POST"])
def api_queue_stop():
    """
    终止当前正在处理的任务：
    - 设置终止标记 + 杀子进程（触发 worker 线程退出）
    - 等待 worker 线程退出（最多等 5 秒，避免 API 永久阻塞）
    - 强制更新 DB 状态为 pending
    - 清理音频文件和 progress 文件
    - 广播 SSE
    """
    episode_id = w.terminate_current_task()

    # 等 worker 线程退出（最多等 5 秒，避免在网络请求卡住时永久阻塞）
    # 如果超时，worker 线程继续在后台运行，最终会检测到 _task_terminated 并自行退出
    t = threading.Thread(target=w.wait_for_worker_exit, daemon=True)
    t.start()
    t.join(timeout=5)

    # 强制更新 DB 状态为 pending
    if episode_id:
        db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        # 清理残留的音频文件（terminate_current_task 已在 worker 线程中删过）
        ep = db.get_episode_by_id(episode_id)
        if ep:
            for path_field in ("audio_path",):
                p = ep.get(path_field) or ""
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass

    # 重置 worker 状态（供下次任务使用）
    w.reset_termination_state()

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
              AND e.discarded = 0
            ORDER BY e.updated_at DESC
            LIMIT 50
        """)
        tasks = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"tasks": tasks})
