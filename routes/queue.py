"""
队列相关路由
/api/queue/*
"""
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
    - 等待 worker 线程真正退出
    - 强制更新 DB 状态为 pending（覆盖 worker 线程可能遗留的 downloading/transcribing）
    - 清理音频文件和 progress 文件（防止残留）
    - 广播 SSE
    """
    episode_id = w.terminate_current_task()

    # 等 worker 线程退出（确保 finally 块执行完毕）
    w.wait_for_worker_exit()

    # 强制更新 DB 状态为 pending（worker 线程检测到 _task_terminated 时不写 DB）
    if episode_id:
        db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        # 清理残留的音频和 progress 文件（terminate_current_task 已在 worker 线程中删过，
        # 这里做一次兜底，路径来自 db 而非 worker 全局变量）
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
