"""
Flask 主程序
VoiceToFile — 小宇宙播客转文字

职责：
- 创建 Flask app
- 注册路由蓝图
- 页面路由（index / queue_page / podcast_detail）
- 单实例保护
- 启动 worker 线程
"""
import os
import sys
import time
import ctypes
import logging
import signal
import msvcrt
import threading
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, Response
)

import db
import scraper
import config
from routes import register_routes
import worker as w

# --------------- Flask 配置 ---------------

config.init_config()

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = config.SECRET_KEY
app.config['JSON_AS_ASCII'] = False

logger = logging.getLogger("app")

# 注册 API 蓝图
register_routes(app)


# --------------- 单实例保护 ---------------

_lock_fd = None


def _is_process_running(pid: int) -> bool:
    """Windows：检查进程是否存在"""
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(0x0400, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _acquire_lock():
    """获取文件锁，防止重复启动"""
    global _lock_fd
    lock_path = config.LOCK_FILE
    pid_path = config.PID_FILE

    try:
        _lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        pid_path.write_text(str(os.getpid()), encoding='utf-8')
        return True
    except FileExistsError:
        # 检查旧进程是否还活着
        try:
            old_pid = int(pid_path.read_text(encoding='utf-8').strip())
            if _is_process_running(old_pid):
                print(f"[启动] VoiceToFile 已在运行中 (PID={old_pid})")
                return False
            # 旧进程已死，强制重新获取锁
            lock_path.unlink(missing_ok=True)
            pid_path.unlink(missing_ok=True)
            _lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            pid_path.write_text(str(os.getpid()), encoding='utf-8')
            return True
        except Exception:
            return False
    except Exception:
        return False


def _release_lock():
    """释放锁文件"""
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.close(_lock_fd)
        except Exception:
            pass
        _lock_fd = None
    try:
        config.LOCK_FILE.unlink(missing_ok=True)
        config.PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# --------------- 页面路由 ---------------

@app.route("/")
def index():
    """主页"""
    db.get_or_create_manual_podcast()

    podcasts_raw = db.list_podcasts()
    active_tasks = db.get_active_episodes()

    for p in podcasts_raw:
        db.sync_podcast_episodes_status(p["id"])

    podcasts = []
    manual_podcast = None

    for p in podcasts_raw:
        episodes = db.list_episodes_by_podcast(p["id"])
        if p["pid"] == db.MANUAL_PID:
            total = len(episodes)
            done = sum(1 for e in episodes if e["status"] == "done_deleted")
            latest_pub = ""
            for e in episodes:
                if e["pub_date"]:
                    latest_pub = e["pub_date"]
                    break
            manual_podcast = {
                **p,
                "total_episodes": total,
                "done_episodes": done,
                "latest_pub_date": latest_pub,
            }
            continue
        sub_eps = [e for e in episodes if e.get("source") == "subscribe"]
        if not sub_eps:
            continue
        total = len(episodes)
        done = sum(1 for e in episodes if e["status"] == "done_deleted")
        latest_pub = ""
        for e in episodes:
            if e["pub_date"]:
                latest_pub = e["pub_date"]
                break
        podcasts.append({
            **p,
            "total_episodes": total,
            "done_episodes": done,
            "latest_pub_date": latest_pub,
        })

    if manual_podcast:
        podcasts.insert(0, manual_podcast)
        manual_podcast_id = db.get_or_create_manual_podcast()
    else:
        manual_podcast_id = 0
    new_podcast_ids = db.get_podcasts_with_new()
    return render_template(
        "new_index.html",
        podcasts=podcasts,
        active_tasks=active_tasks,
        output_root=str(config.OUTPUT_ROOT),
        cookie_interval=config.COOKIE_INTERVAL,
        now=datetime.now,
        manual_podcast_id=manual_podcast_id,
        new_podcast_ids=new_podcast_ids,
    )


@app.route("/queue", methods=["GET", "POST"])
def queue_page():
    """独立的队列页面（新窗口打开）"""
    if request.method == "POST":
        episode_id = request.form.get("episode_id", type=int)
        if episode_id:
            db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        return redirect(url_for("queue_page"))

    conn = db.get_conn()
    try:
        cur = conn.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('downloading', 'transcribing')
        """)
        active = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    conn = db.get_conn()
    try:
        cur = conn.execute("""
            SELECT e.*, p.name as podcast_name
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status = 'queued'
            ORDER BY e.created_at ASC
        """)
        in_queue = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    done = db.get_recently_completed_episodes(limit=20)
    return render_template("queue.html", active=active, pending=in_queue, in_queue=in_queue, done=done)


@app.route("/podcast/<int:podcast_id>")
def podcast_detail(podcast_id: int):
    """播客详情页"""
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return redirect(url_for("index"))
    return render_template("podcast_detail.html", podcast=dict(podcast), new_podcast_ids=[])


@app.route("/settings", methods=["POST"])
def update_settings():
    """更新设置"""
    new_root = request.form.get("output_root", "").strip()
    if new_root:
        config.OUTPUT_ROOT = Path(new_root)
    cookie = request.form.get("cookie", "").strip()
    if cookie:
        scraper.set_cookie(cookie)
    return jsonify({"ok": True})


# --------------- 启动 ---------------

if __name__ == "__main__":
    # ---- 日志文件配置 ----
    LOG_FILE = Path(__file__).parent / "voicetofile.log"

    # ---- 启动时清理 3 天前的日志文件 ----
    import time
    from pathlib import Path
    _three_days_ago = time.time() - 3 * 86400
    for _lf in Path(__file__).parent.glob("*.log"):
        try:
            if _lf.stat().st_mtime < _three_days_ago:
                _lf.unlink()
                print(f"[清理] 已删除过期日志: {_lf.name}")
        except Exception:
            pass

    # 创建一个 Tee：同时写文件 + stdout
    class TeeWriter:
        def __init__(self, file, stdout):
            self.file = file
            self.stdout = stdout
        def write(self, msg):
            if msg.strip():
                self.file.write(msg)
                self.file.flush()
            try:
                self.stdout.write(msg)
                self.stdout.flush()
            except UnicodeEncodeError:
                # Windows 终端可能是 GBK，无法编码非 ASCII 字符
                try:
                    self.stdout.buffer.write(msg.encode(self.stdout.encoding or "utf-8", errors="replace"))
                    self.stdout.buffer.flush()
                except Exception:
                    pass
        def flush(self):
            self.file.flush()
            try:
                self.stdout.flush()
            except Exception:
                pass

    log_fd = open(LOG_FILE, "a", encoding="utf-8")
    sys.stdout = TeeWriter(log_fd, sys.__stdout__)
    sys.stderr = TeeWriter(log_fd, sys.__stderr__)

    # ---- 初始化数据库 ----
    db.init_db()

    # 清理残留任务
    deleted = db.cleanup_stale_tasks()
    if deleted > 0:
        logging.info(f"[队列] 已删除 {deleted} 个残留任务")

    # 单实例保护
    if not _acquire_lock():
        sys.exit(1)

    # 注册信号处理
    def _cleanup(signum, frame):
        _release_lock()
        sys.exit(0)
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    # 启动 worker 线程
    worker_thread = threading.Thread(target=w._queue_worker, daemon=True)
    worker_thread.start()
    print(f"[队列] Worker 线程已启动 (alive={worker_thread.is_alive()})")

    # 启动 Flask
    print(f"VoiceToFile 启动中... http://127.0.0.1:{config.PORT}")
    app.run(host="0.0.0.0", port=config.PORT, debug=False, threaded=True)

    _release_lock()
