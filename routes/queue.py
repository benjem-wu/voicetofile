"""
队列相关路由
/api/queue/*
"""
import threading
from flask import Blueprint, jsonify
from pathlib import Path

import db
import worker as w
import config
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
    """获取当前队列状态（DB + 转写状态文件兜底）"""
    import json, time as time_mod
    from datetime import datetime

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

    # ---- 转写状态文件兜底：Flask 重启后 worker 丢失的 transcriber 子进程 ----
    # 扫描所有 _transcribe_state_{episode_id}.json，若 status=transcribing 且近期有更新，
    # 则补充该任务（即使 DB 状态被 cleanup_stale_tasks 改成了 pending）
    active_eids = {t['eid'] for t in tasks if t['status'] in ('downloading', 'transcribing')}
    output_root = config.OUTPUT_ROOT

    if output_root.exists():
        # 遍历所有播客输出目录
        for podcast_dir in output_root.iterdir():
            if not podcast_dir.is_dir():
                continue
            for sf in podcast_dir.glob("_transcribe_state_*.json"):
                try:
                    with open(sf, encoding='utf-8') as f:
                        state = json.load(f)
                    if state.get('status') != 'transcribing':
                        continue

                    # 检查是否近期有更新（5分钟内），排除僵尸文件
                    updated_at_str = state.get('updated_at', '')
                    if updated_at_str:
                        try:
                            file_time = datetime.fromisoformat(updated_at_str.replace(' ', 'T'))
                            age_seconds = (datetime.now() - file_time).total_seconds()
                            if age_seconds > 300:
                                continue
                        except Exception:
                            pass

                    # 从文件名提取 episode_id
                    ep_id_str = sf.stem.replace('_transcribe_state_', '')
                    try:
                        episode_id = int(ep_id_str)
                    except ValueError:
                        continue

                    ep = db.get_episode_by_id(episode_id)
                    if not ep:
                        continue
                    eid = ep['eid']
                    if eid in active_eids:
                        continue

                    # 查播客名
                    conn2 = db.get_conn()
                    try:
                        p_row = conn2.execute("SELECT name FROM podcasts WHERE id = ?", (ep['podcast_id'],)).fetchone()
                        podcast_name = p_row['name'] if p_row else podcast_dir.name
                    finally:
                        conn2.close()

                    tasks.insert(0, {
                        'id': episode_id,
                        'eid': eid,
                        'name': ep['name'],
                        'podcast_name': podcast_name,
                        'status': 'transcribing',
                        'progress': state.get('progress', 0),
                        'updated_at': ep['updated_at'],
                    })
                    active_eids.add(eid)
                    print(f"[api_queue] 从状态文件补充: {podcast_name} — {ep['name']}")
                except Exception as ex:
                    print(f"[api_queue] 扫描 {sf}: {ex}")

    return jsonify({"tasks": tasks})
