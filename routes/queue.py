"""
队列相关路由
/api/queue/*
"""
from pathlib import Path
from flask import Blueprint, request, jsonify

import db
import worker as w
from sse import broadcast_sse

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")


@queue_bp.route("/stop", methods=["GET", "POST"])
def api_queue_stop():
    """
    终止当前正在处理的任务：ffprobe 验证音频，广播 SSE 让前端弹框。
    实际杀子进程和改 DB 由用户在弹框中选择"继续/暂停/重置"后触发。
    """
    task_info = w.get_current_task_info()

    episode_id = task_info["id"] if task_info else None
    eid = task_info["eid"] if task_info else ""
    episode_name = task_info["name"] if task_info else ""
    podcast_name = task_info.get("podcast_name", "") if task_info else ""

    audio_file = w.get_current_audio_file()
    audio_complete = False
    if audio_file and Path(audio_file).exists():
        audio_complete = w._verify_audio_complete(audio_file)
        print(f"[终止] ffprobe audio_complete={audio_complete}, file={audio_file}")
    else:
        print(f"[终止] 音频文件不存在或路径为空, audio_file={audio_file}")

    # 音频不完整时，立即设置终止标记让 worker 杀子进程
    # 音频完整时不设置——子进程继续运行，等用户在弹框中选择
    if not audio_complete:
        w.set_task_terminated()

    broadcast_sse("task_stop", {
        "eid": eid,
        "episode_id": episode_id,
        "name": episode_name,
        "podcast_name": podcast_name,
        "audio_complete": audio_complete,
    })

    return jsonify({"ok": True, "audio_complete": audio_complete, "episode_id": episode_id})


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
