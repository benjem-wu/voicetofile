"""
系统路由
/sse/stream, /api/homepage/status, /api/refresh, /api/podcasts/new-ids
"""
import os
import sys
import time
import threading
from flask import Blueprint, request, jsonify, Response

import db
import config
from sse import sse_subscribers, sse_lock

system_bp = Blueprint("system", __name__)


@system_bp.route("/sse/stream")
def sse_stream():
    """SSE 流"""
    q = __import__('queue').Queue(maxsize=100)

    def emit(q):
        while True:
            try:
                msg = q.get(timeout=30)
                yield msg
            except __import__('queue').Empty:
                yield f"event: ping\ndata: {{}}\n\n"

    with sse_lock:
        sse_subscribers.append(q)

    return Response(
        emit(q),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )


@system_bp.route("/api/homepage/status")
def api_homepage_status():
    statuses = {}
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, status FROM episodes
            WHERE status IN ('downloading', 'transcribing', 'queued')
        """)
        for row in cur.fetchall():
            statuses[str(row["id"])] = row["status"]
    finally:
        conn.close()
    return jsonify({"statuses": statuses})


@system_bp.route("/api/podcasts/new-ids")
def api_podcasts_new_ids():
    return jsonify({"new_podcast_ids": db.get_podcasts_with_new()})


@system_bp.route("/api/refresh", methods=["POST"])
def api_refresh():
    def restart():
        time.sleep(0.5)
        python = sys.executable
        os.execv(python, [python, str(config.PROJECT_ROOT / "app.py")])

    t = threading.Thread(target=restart, daemon=True)
    t.start()
    return jsonify({"ok": True})
