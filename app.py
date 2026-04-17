"""
Flask 主程序
VoiceToFile — 小宇宙播客转文字
"""
import os
import sys
import signal
import ctypes
import json
import time
import logging
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, Response
)
import sqlite3

# --------------- 项目内部模块 ---------------
import db
import scraper
import downloader
import transcriber
from _utils import sanitize_filename, check_path_length, format_duration

# --------------- Flask 配置 ---------------

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.urandom(24)
app.config['JSON_AS_ASCII'] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("app")

# --------------- 全局状态（队列 v2）---------------

# 当前 subprocess 引用（用于 kill）
_proc_to_kill = None
# 标记当前任务已被 api_queue_stop 提前终止
_task_terminated = False
# 当前处理中的任务信息（用于 api_queue_stop 获取 episode_id 和 audio_file）
_current_task_info = None
_current_audio_file = None
# SSE 订阅者
sse_subscribers = []
sse_lock = threading.Lock()

# SSE 订阅者：{"event": "message", "data": {...}}
sse_subscribers = []
sse_lock = threading.Lock()

# 输出根目录（可配置）
OUTPUT_ROOT = Path("F:/outfile")
COOKIE_INTERVAL = 5  # 请求间隔（秒）

# --------------- 辅助函数 ---------------

def get_output_dir(podcast_name: str) -> Path:
    """获取播客输出目录"""
    out = OUTPUT_ROOT / sanitize_filename(podcast_name)
    out.mkdir(parents=True, exist_ok=True)
    return out


def broadcast_sse(event: str, data: dict):
    """向所有 SSE 订阅者广播消息"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for sub in sse_subscribers:
            try:
                sub.put(msg)
            except Exception:
                dead.append(sub)
        for d in dead:
            sse_subscribers.remove(d)


def addLog(text: str, log_type: str = "tag"):
    """前端日志推送"""
    broadcast_sse("log", {"text": text, "type": log_type})


def task_update(eid: str, **kwargs):
    """更新任务状态并推送 SSE（直接广播，不依赖 task_queue）"""
    broadcast_sse("task_update", {"eid": eid, **kwargs})


# --------------- SSE 路由 ---------------

@app.route("/sse/stream")
def sse_stream():
    """SSE 流，用于前端实时接收状态更新"""
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


# --------------- 页面路由 ---------------

@app.route("/")
def index():
    """主页"""
    # 确保"精选播客"虚拟播客存在
    db.get_or_create_manual_podcast()

    podcasts_raw = db.list_podcasts()
    active_tasks = db.get_active_episodes()

    # 同步所有播客的文件状态（修正文件已删除但DB状态未改的情况）
    for p in podcasts_raw:
        db.sync_podcast_episodes_status(p["id"])

    # 构建播客列表（精选播客置顶，其他按添加时间倒序）
    podcasts = []
    manual_podcast = None

    for p in podcasts_raw:
        episodes = db.list_episodes_by_podcast(p["id"])
        # 精选播客：显示所有单集（包括手动添加的）
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
        # 普通播客：至少要有一个 subscribe 来源的集
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

    # 精选播客置顶
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
        output_root=str(OUTPUT_ROOT),
        cookie_interval=COOKIE_INTERVAL,
        now=datetime.now,
        manual_podcast_id=manual_podcast_id,
        new_podcast_ids=new_podcast_ids,
    )


@app.route("/queue", methods=["GET", "POST"])
def queue_page():
    """独立的队列页面（新窗口打开）"""
    if request.method == "POST":
        # 移除按钮：恢复为 pending 状态
        episode_id = request.form.get("episode_id", type=int)
        if episode_id:
            db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
        return redirect(url_for("queue_page"))

    # 正在处理：downloading + transcribing（纯 DB）
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

    # 排队中：queued（纯 DB）
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
def podcast_page(podcast_id: int):
    """独立的播客详情页（新窗口打开）"""
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return "播客不存在", 404
    return render_template("podcast.html", podcast=dict(podcast), now=datetime.now)


@app.route("/podcast/<int:podcast_id>")
def podcast_detail(podcast_id: int):
    """播客详情页"""
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return redirect(url_for("index"))
    episodes = db.list_episodes_by_podcast(podcast_id)
    manual_podcast_id = db.get_or_create_manual_podcast()
    return render_template(
        "new_index.html",
        podcast_detail=True,
        podcast=dict(podcast),
        episodes=episodes,
        manual_podcast_id=manual_podcast_id,
    )


@app.route("/settings", methods=["POST"])
def update_settings():
    """更新设置"""
    global OUTPUT_ROOT, COOKIE_INTERVAL
    OUTPUT_ROOT = Path(request.form.get("output_root", str(OUTPUT_ROOT)))
    COOKIE_INTERVAL = int(request.form.get("cookie_interval", COOKIE_INTERVAL))
    cookie = request.form.get("cookie", "").strip()
    if cookie:
        scraper.set_cookie(cookie)
    return jsonify({"ok": True})


# --------------- API 路由 ---------------

@app.route("/api/podcast/fetch", methods=["POST"])
def api_fetch_podcast():
    """
    模式A：从 URL 或 PID 获取播客信息
    POST body: {"url": "..."} 或 {"pid": "..."}
    """
    data = request.get_json()
    url = data.get("url", "").strip()
    pid = data.get("pid", "").strip()

    if not pid:
        pid = scraper.extract_pid(url)
    if not pid:
        return jsonify({"ok": False, "error": "无法从 URL 提取 PID"})

    addLog(f"[播客] 正在获取: {pid}", "tag")

    try:
        info = scraper.fetch_podcast_info(pid, interval=COOKIE_INTERVAL)
        addLog(f"[播客] 名称: {info.name}，共 {len(info.episodes)} 集，正在验证音频...", "done")

        # 并行获取每集音频 URL，过滤无音频的占位集
        def fetch_one_audio(ep):
            # 每条线程用自己的 scraper 实例，interval=1 秒防止被限速
            detail = scraper.fetch_episode_info(ep.eid, interval=1)
            return {
                "eid": ep.eid,
                "name": ep.name,
                "pub_date": ep.pub_date,
                "duration": ep.duration,
                "is_paid": ep.is_paid,
                "paid_price": getattr(ep, "paid_price", None),
                "description": getattr(ep, "description", ""),
                "has_audio": bool(detail.audio_url),
            }

        episodes_with_audio = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fetch_one_audio, ep): ep for ep in info.episodes}
            for future in as_completed(futures):
                episodes_with_audio.append(future.result())

        # 只保留有音频的集（去除占位集如"声动早咖啡"等）
        valid_episodes = [ep for ep in episodes_with_audio if ep["has_audio"]]
        skipped = len(info.episodes) - len(valid_episodes)
        if skipped > 0:
            addLog(f"[播客] 跳过 {skipped} 集（无音频，占位集）", "done")

        # 存入 DB
        podcast_id = db.add_podcast(pid, info.name)

        ep_records = [{
            "podcast_id": podcast_id,
            "eid": ep["eid"],
            "name": ep["name"],
            "pub_date": ep["pub_date"],
            "duration": ep["duration"],
            "is_paid": ep["is_paid"],
        } for ep in valid_episodes]
        db.add_episodes(ep_records)

        return jsonify({
            "ok": True,
            "podcast_id": podcast_id,
            "pid": pid,
            "name": info.name,
            "episodes": [
                {
                    "eid": ep["eid"],
                    "name": ep["name"],
                    "pub_date": ep["pub_date"][:10] if ep["pub_date"] else "",
                    "duration": ep["duration"],
                    "duration_str": format_duration(ep["duration"]),
                    "is_paid": ep["is_paid"],
                    "paid_price": ep.get("paid_price"),
                    "description": ep.get("description", "")[:100],
                }
                for ep in valid_episodes
            ]
        })
    except Exception as e:
        addLog(f"[错误] 获取失败: {e}", "err")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/episode/add", methods=["POST"])
def api_add_episode():
    """
    模式B：手动添加单集（统一归入"精选播客"虚拟播客）
    POST body: {"url": "https://www.xiaoyuzhoufm.com/episode/xxx"}
    """
    data = request.get_json()
    url = data.get("url", "").strip()

    # 从 URL 提取 eid
    import re
    m = re.search(r"/episode/([a-f0-9]+)", url)
    if not m:
        return jsonify({"ok": False, "error": "无法从 URL 提取 Episode ID"})
    eid = m.group(1)

    addLog(f"[单集] 正在获取: {eid}", "tag")

    try:
        ep = scraper.fetch_episode_info(eid, interval=COOKIE_INTERVAL)
        addLog(f"[单集] {ep.name}，音频: {ep.audio_url[:40]}...", "done")

        # 强制使用"精选播客"虚拟播客
        podcast_id = db.get_or_create_manual_podcast()

        # 检查是否已存在
        existing_ep = db.get_episode_by_eid(podcast_id, eid)
        if existing_ep:
            if existing_ep["status"] in ("done_deleted", "failed"):
                db.reset_episode_for_retry(existing_ep["id"])
                episode_id = existing_ep["id"]
            else:
                return jsonify({
                    "ok": False,
                    "error": f"该集已在队列中（状态: {existing_ep['status']}）"
                })
        else:
            # 添加到 DB（source=manual 表示手动添加）
            ep_records = [{
                "podcast_id": podcast_id,
                "eid": eid,
                "name": ep.name,
                "pub_date": ep.pub_date,
                "duration": ep.duration,
                "is_paid": ep.is_paid,
            }]
            db.add_episodes(ep_records, source="manual")
            episode_id = db.get_episode_by_eid(podcast_id, eid)["id"]

        # 如果是付费集，拒绝
        if ep.is_paid:
            db.update_episode_status(episode_id, "failed", error_msg="付费内容，无法下载")
            return jsonify({"ok": False, "error": f"该集为付费内容（{ep.paid_price}），无法下载"})

        # 只添加到数据库，状态为 pending，等用户点"转文字"才真正处理
        return jsonify({
            "ok": True,
            "eid": eid,
            "name": ep.name,
            "episode_id": episode_id,
        })
    except Exception as e:
        addLog(f"[错误] 获取失败: {e}", "err")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/episodes/enqueue", methods=["POST"])
def api_enqueue_episodes():
    """
    将选中的 episodes 加入处理队列
    POST body: {"podcast_id": int, "eids": ["eid1", "eid2", ...]}
    """
    data = request.get_json()
    podcast_id = int(data["podcast_id"])
    eids = data["eids"]  # 用户选中的（非付费）eid 列表

    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return jsonify({"ok": False, "error": "播客不存在"})
    podcast = dict(podcast)

    # 过滤掉付费集和已经在队列中的集
    episodes = db.list_episodes_by_podcast(podcast_id)
    to_enqueue = [e for e in episodes if e["eid"] in eids and not e["is_paid"] and e["status"] in ("pending", "failed", "done_deleted")]

    if not to_enqueue:
        return jsonify({"ok": False, "error": "没有可下载的集数（全部为付费内容）"})

    # 限制每次最多入队 3 个，避免机器负载过重
    MAX_ENQUEUE = 10
    to_enqueue = to_enqueue[:MAX_ENQUEUE]

    addLog(f"[队列] 加入 {len(to_enqueue)} 集: {podcast['name']}", "tag")

    for ep in to_enqueue:
        db.enqueue_task(ep["id"])
        # 广播新任务入队（让前端及时看到）
        broadcast_sse("task_new", {
            "eid": ep["eid"],
            "name": ep["name"],
            "podcast_name": podcast["name"],
            "status": "queued",
            "progress": 0,
        })

    return jsonify({"ok": True, "count": len(to_enqueue)})


@app.route("/api/episodes/refresh", methods=["POST"])
def api_refresh_episodes():
    """
    重新从网络获取播客集列表
    POST body: {"podcast_id": int}
    返回 new_eids：本次刷新新增的 eid 列表（用于前端标记小红点）
    """
    data = request.get_json()
    podcast_id = int(data["podcast_id"])
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return jsonify({"ok": False, "error": "播客不存在"})
    podcast = dict(podcast)

    addLog(f"[刷新] 重新获取: {podcast['name']}", "tag")
    try:
        info = scraper.fetch_podcast_info(podcast["pid"], interval=COOKIE_INTERVAL)
        # 更新播客名称
        updated_name = podcast["name"]
        if info.name != podcast["name"]:
            db.add_podcast(podcast["pid"], info.name)
            updated_name = info.name

        # 刷新前记录 DB 中已有的 eid（精确对比）
        conn = db.get_conn()
        existing_eids = set(
            row["eid"] for row in conn.execute(
                "SELECT eid FROM episodes WHERE podcast_id = ?", (podcast_id,)
            ).fetchall()
        )

        # 更新 episodes
        ep_records = [{
            "podcast_id": podcast_id,
            "eid": ep.eid,
            "name": ep.name,
            "pub_date": ep.pub_date,
            "duration": ep.duration,
            "is_paid": ep.is_paid,
        } for ep in info.episodes]
        db.add_episodes(ep_records)

        # 精确计算新增的 eid（刷新前 DB 没有的）
        new_eids = [ep.eid for ep in info.episodes if ep.eid not in existing_eids]
        new_count = len(new_eids)

        # 标记新集（is_new=1），后续展开时清除
        if new_eids:
            db.mark_episodes_new(podcast_id, new_eids)

        addLog(f"[刷新] 完成，共 {len(info.episodes)} 集，新增 {new_count} 集", "done")
        return jsonify({
            "ok": True,
            "count": len(info.episodes),
            "new_count": new_count,
            "new_eids": new_eids,
            "podcast_name": updated_name
        })
    except Exception as e:
        addLog(f"[错误] 刷新失败: {e}", "err")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/podcast/delete", methods=["POST"])
def api_delete_podcast():
    """删除播客订阅"""
    data = request.get_json()
    db.delete_podcast(int(data["podcast_id"]))
    return jsonify({"ok": True})


@app.route("/api/homepage/status")
def api_homepage_status():
    """
    返回当前活跃任务的状态映射，供首页每5秒轮询同步状态用。
    返回 {episodeId: status} 格式，仅包含非终态的任务。
    """
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


@app.route("/api/podcast/<int:podcast_id>/episodes")
def api_podcast_episodes(podcast_id: int):
    """获取播客的剧集列表（用于展开子表和详情页）"""
    podcast = db.get_conn().execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not podcast:
        return jsonify({"ok": False, "error": "播客不存在"})

    # 同步检查文件存在性，自动修正状态
    db.sync_podcast_episodes_status(podcast_id)
    episodes = db.list_episodes_by_podcast(podcast_id)

    return jsonify({
        "ok": True,
        "podcast": dict(podcast),
        "episodes": [
            {
                "id": e["id"],
                "eid": e["eid"],
                "name": e["name"],
                "pub_date": e["pub_date"],
                "duration": e["duration"],
                "duration_str": format_duration(e["duration"]) if e["duration"] else "",
                "is_paid": e["is_paid"],
                "status": e["status"],
                "txt_path": e["txt_path"],
                "txt_exists": os.path.exists(e["txt_path"]) if e["txt_path"] else False,
            }
            for e in episodes
        ]
    })


@app.route("/api/episode/retry/<int:episode_id>", methods=["POST"])
def api_retry_episode(episode_id: int):
    """重新处理失败的 episode（删除可能损坏的音频，重新入队）"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    if ep["status"] not in ("failed", "done_deleted"):
        return jsonify({"ok": False, "error": f"当前状态 {ep['status']} 不支持重试"})

    # 删除可能损坏的音频文件
    for path_field in ("audio_path", "txt_path"):
        p = ep.get(path_field) or ""
        if p and Path(p).exists():
            try:
                Path(p).unlink()
            except Exception:
                pass

    db.reset_episode_for_retry(episode_id)
    db.enqueue_task(episode_id)
    return jsonify({"ok": True})


@app.route("/api/episode/reenqueue/<int:episode_id>", methods=["POST"])
def api_reenqueue_episode(episode_id: int):
    """重新入队已终止的任务（音频已保留，仅重新入队）"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    if ep["status"] != "pending":
        return jsonify({"ok": False, "error": f"当前状态 {ep['status']} 不是 pending，无法入队"})

    db.enqueue_task(episode_id)
    return jsonify({"ok": True})


@app.route("/api/episode/open/<int:episode_id>")
def api_episode_open(episode_id: int):
    """调用系统程序打开 TXT 文件"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    if not ep["txt_path"] or not os.path.exists(ep["txt_path"]):
        return jsonify({"ok": False, "error": "文字稿文件不存在"})
    try:
        os.startfile(ep["txt_path"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/podcast/open/<int:podcast_id>")
def api_podcast_open(podcast_id: int):
    """用文件资源管理器打开播客的输出文件夹"""
    p = db.get_conn().execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
    if not p:
        return jsonify({"ok": False, "error": "播客不存在"})
    folder = get_output_dir(p["name"])
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(folder)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/podcast/viewed/<int:podcast_id>", methods=["POST"])
def api_podcast_viewed(podcast_id: int):
    """用户展开播客后，清除该播客的新集标记"""
    db.mark_podcast_viewed(podcast_id)
    return jsonify({"ok": True})


@app.route("/api/podcasts/new-ids")
def api_podcasts_new_ids():
    """返回当前有 is_new 标记的播客 ID 列表"""
    return jsonify({"new_podcast_ids": db.get_podcasts_with_new()})


@app.route("/api/episode/<int:episode_id>", methods=["GET"])
def api_get_episode(episode_id: int):
    """获取 episode 详情（含错误信息）"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"}), 404
    return jsonify({"ok": True, "episode": ep})


@app.route("/api/episode/dequeue", methods=["POST"])
def api_dequeue_episode():
    """将 episode 从队列中移除，恢复为未转化状态"""
    data = request.get_json()
    episode_id = int(data["episode_id"])
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
    return jsonify({"ok": True})


def _verify_audio_complete(audio_file: str) -> bool:
    """用 ffprobe 检测音频文件是否完整（能获取到时长）"""
    if not audio_file or not Path(audio_file).exists():
        return False
    # 简单检查：文件存在 + 大于 1MB 就认为基本完整（ffprobe 是辅助验证）
    try:
        size = Path(audio_file).stat().st_size
        if size < 1024 * 1024:
            print(f"[终止] 音频文件太小 ({size} bytes)，认为不完整")
            return False
    except Exception:
        pass
    ffprobe_path = Path(__file__).parent / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffprobe.exe"
    if not ffprobe_path.exists():
        # ffprobe 不存在，但文件存在且够大，认为完整
        print(f"[终止] ffprobe 不存在，基于文件大小判断完整")
        return True
    try:
        result = subprocess.run(
            [str(ffprobe_path), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_file],
            capture_output=True, text=True, timeout=10
        )
        duration = float(result.stdout.strip() or 0)
        if duration <= 0:
            print(f"[终止] ffprobe 获取时长失败（{duration}），认为不完整")
        return duration > 0
    except Exception as e:
        print(f"[终止] ffprobe 执行异常: {e}，基于文件大小判断完整")
        return True


@app.route("/api/queue/stop", methods=["GET", "POST"])
def api_queue_stop():
    """
    终止当前正在处理的任务：ffprobe 验证音频，广播 SSE 让前端弹框。
    实际杀子进程和改 DB 由用户在弹框中选择"继续/暂停/重置"后触发。
    """
    global _task_terminated, _current_task_info, _current_audio_file

    episode_id = _current_task_info["id"] if _current_task_info else None
    eid = _current_task_info["eid"] if _current_task_info else ""
    episode_name = _current_task_info["name"] if _current_task_info else ""
    podcast_name = _current_task_info.get("podcast_name", "") if _current_task_info else ""

    # 检查音频完整性（只读，不杀进程不改DB）
    audio_file = _current_audio_file
    audio_complete = False
    if audio_file and Path(audio_file).exists():
        audio_complete = _verify_audio_complete(audio_file)
        print(f"[终止] ffprobe audio_complete={audio_complete}, file={audio_file}")
    else:
        print(f"[终止] 音频文件不存在或路径为空, audio_file={audio_file}")

    # 音频不完整时，立即设置终止标记让 worker 杀子进程
    # 音频完整时不设置——子进程继续运行，等用户在弹框中选择
    if not audio_complete:
        _task_terminated = True

    # 广播 task_stop 事件，前端弹框让用户选择
    broadcast_sse("task_stop", {
        "eid": eid,
        "episode_id": episode_id,
        "name": episode_name,
        "podcast_name": podcast_name,
        "audio_complete": audio_complete,
    })

    return jsonify({"ok": True, "audio_complete": audio_complete, "episode_id": episode_id})



@app.route("/api/queue")
def api_queue():
    """获取当前队列状态（纯 DB，无 task_queue）"""
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


@app.route("/api/episode/pause/<int:episode_id>", methods=["POST"])
def api_episode_pause(episode_id: int):
    """
    暂停任务：设置终止标记，worker finally 块杀子进程，
    audio_path 保留音频，状态改为 paused。
    """
    global _task_terminated, _current_task_info, _current_audio_file

    _task_terminated = True

    # 优先用当前任务的音频路径（worker 还在跑时），否则保留 DB 里已有的路径
    ep = db.get_episode_by_id(episode_id)
    if ep:
        existing_audio = ep.get("audio_path") or ""
        audio_file = _current_audio_file if _current_audio_file else existing_audio
        db.pause_episode(episode_id, audio_path=audio_file)
        addLog(f"[暂停] {ep['name'][:30]} 已暂停", "tag")

    return jsonify({"ok": True})


@app.route("/api/episode/reset/<int:episode_id>", methods=["POST"])
def api_episode_reset(episode_id: int):
    """
    重置任务：设置终止标记，worker finally 块杀子进程，
    删除音频，状态改为 pending。
    """
    global _task_terminated, _current_task_info, _current_audio_file

    _task_terminated = True

    # 删除音频文件（优先用 _current_audio_file，再用 DB 里的 audio_path）
    audio_file = _current_audio_file
    if not audio_file:
        ep = db.get_episode_by_id(episode_id)
        if ep:
            audio_file = ep.get("audio_path") or ""
    if audio_file and Path(audio_file).exists():
        try:
            Path(audio_file).unlink()
        except Exception:
            pass

    # 直接更新 DB（不依赖 _current_task_info，因为 worker finally 已清空）
    ep = db.get_episode_by_id(episode_id)
    if ep:
        db.reset_episode_for_retry(episode_id)
        addLog(f"[重置] {ep['name'][:30]} 已重置", "tag")

    return jsonify({"ok": True})


@app.route("/api/episode/resume/<int:episode_id>", methods=["POST"])
def api_episode_resume(episode_id: int):
    """
    继续暂停的任务：
    - audio_path 存在且完整 → 跳过下载，状态改为 transcribing
    - audio_path 不存在或不完整 → 删除路径，状态改为 downloading，重新下载
    """
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "任务不存在"}), 404

    audio_path = ep.get("audio_path") or ""
    audio_complete = False
    if audio_path and Path(audio_path).exists():
        audio_complete = _verify_audio_complete(audio_path)

    if audio_complete:
        # 音频完整，跳过下载，直接转写
        conn = db.get_conn()
        try:
            conn.execute("UPDATE episodes SET status = 'transcribing', audio_path = '', updated_at = ? WHERE id = ?",
                         (datetime.now().isoformat(), episode_id))
            conn.commit()
        finally:
            conn.close()
    else:
        # 音频不完整，删除路径，重新下载
        if audio_path:
            try:
                Path(audio_path).unlink()
            except Exception:
                pass
        db.update_episode_status(episode_id, "downloading")

    # 通知 SSE 前端更新
    task_update(ep["eid"], status="downloading", progress=0, elapsed=0)
    return jsonify({"ok": True})


# --------------- 任务队列处理（v2：DB 唯一数据源）---------------

def _process_task(task: dict):
    """
    在独立线程中处理单个 episode 的完整流程：下载 → 转写 → 清理。
    所有状态变更通过 db.* 函数，不操作 task_queue。
    """
    global _proc_to_kill, _task_terminated, _current_task_info, _current_audio_file
    episode_id = task["id"]
    eid = task["eid"]
    podcast_name = task.get("podcast_name", task.get("name", ""))
    episode_name = task["name"]
    output_dir = get_output_dir(podcast_name)
    start_time = time.time()

    # 追踪当前任务信息（供 api_queue_stop 使用）
    _current_task_info = task
    audio_file = None

    # 通知前端开始下载
    task_update(eid, status="downloading", progress=0, elapsed=0)

    try:
        # ---- 获取音频 URL ----
        addLog(f"[下载] {episode_name[:30]}...", "tag")
        ep_detail = scraper.fetch_episode_info(eid, interval=COOKIE_INTERVAL)
        if not ep_detail.audio_url:
            raise ValueError("无法获取音频 URL")
        if ep_detail.is_paid:
            raise ValueError(f"该集为付费内容（{ep_detail.paid_price}），跳过")
        if _task_terminated:
            raise ValueError("已终止")

        # ---- 下载音频 ----
        dl = downloader.Downloader(output_dir)
        dl_result = dl.download(ep_detail.audio_url, episode_name, eid, check_terminated=lambda: _task_terminated)
        if not dl_result["ok"]:
            raise ValueError(f"下载失败: {dl_result.get('error', '未知错误')}")

        if _task_terminated:
            dl.cleanup_progress(eid)
            raise ValueError("已终止")

        audio_file = dl_result["file"]
        _current_audio_file = audio_file
        elapsed_dl = time.time() - start_time
        task_update(eid, status="downloading", progress=30, elapsed=int(elapsed_dl))

        # ---- 转写 ----
        addLog(f"[转写] {episode_name[:30]}...", "tag")
        task_update(eid, status="transcribing", progress=50)
        db.update_task_progress(episode_id, 50)

        # 调用转写（子进程，带超时保护）
        sub_result = _run_transcriber_subprocess(
            audio_file, output_dir, episode_name,
            ep_detail.audio_url, eid, episode_id,
            timeout=7200  # 2小时超时
        )

        if _task_terminated:
            raise ValueError("已终止")

        if not sub_result["ok"]:
            raise ValueError(f"转写失败: {sub_result.get('error', '未知错误')}")

        txt_file = sub_result.get("file", "")
        total_elapsed = time.time() - start_time

        # ---- 更新 DB ----
        db.mark_task_done(episode_id, txt_file)
        task_update(eid, status="done_deleted", progress=100, elapsed=int(total_elapsed))
        broadcast_sse("task_done", {
            "eid": eid, "name": episode_name,
            "podcast_name": podcast_name, "status": "done_deleted"
        })
        addLog(f"[完成] {episode_name[:30]}，用时 {int(total_elapsed)}秒，已删除音频", "done")

        # ---- 清理 ----
        dl.cleanup_progress(eid)
        try:
            Path(audio_file).unlink(missing_ok=True)
        except Exception:
            pass

    except Exception as e:
        total_elapsed = time.time() - start_time
        err_msg = str(e)
        addLog(f"[失败] {episode_name[:30]}: {err_msg}", "err")

        retry_count = task.get("retry_count", 0)
        if retry_count < 2 and not _task_terminated:
            # 自动重试：重新入队
            db.enqueue_task(episode_id)
            addLog(f"[重试] {episode_name[:30]} 第{retry_count+1}次", "tag")
            broadcast_sse("task_done", {
                "eid": eid, "name": episode_name,
                "podcast_name": podcast_name, "status": "queued"
            })
            return

        if _task_terminated:
            # 已终止：由 api_queue_stop 处理 DB 状态，这里不做任何操作
            return

        # 超过2次重试，标记为失败
        db.mark_task_failed(episode_id, err_msg)
        task_update(eid, status="failed", elapsed=int(total_elapsed), error=err_msg)
        broadcast_sse("task_done", {
            "eid": eid, "name": episode_name,
            "podcast_name": podcast_name, "status": "failed"
        })

    finally:
        print(f"[finally] episode_id={episode_id} eid={eid} _task_terminated={_task_terminated} _proc_to_kill={_proc_to_kill}")
        # 等待子进程结束（防止僵尸），无论是否被终止都要等待
        if _proc_to_kill is not None:
            try:
                _proc_to_kill.kill()  # 确保终止
                _proc_to_kill.wait()  # 等待真正结束
            except Exception:
                pass
            _proc_to_kill = None

        # 如果是主动终止（_task_terminated=True），无论 proc_to_kill 是否存在，都要重置任务状态
        if _task_terminated:
            print(f"[finally] 正在重置任务 eid={eid}")
            db.reset_episode_for_retry(episode_id)
            _task_terminated = False
            # 通知前端：任务已重置为 pending（显示为"未转化"）
            task_update(eid, status="pending", progress=0, elapsed=0)
            broadcast_sse("task_done", {
                "eid": eid, "name": episode_name,
                "podcast_name": podcast_name, "status": "pending"
            })
            print(f"[finally] 任务已重置为pending eid={eid}")

        _current_task_info = None
        _current_audio_file = None


def _queue_worker():
    """后台 worker：永远只从 DB 抢任务（db.get_next_queued_task 原子操作）"""
    print("[队列] Worker 线程已启动")
    while True:
        task = db.get_next_queued_task()
        if task:
            print(f"[队列] 拾取任务: {task['name']} (id={task['id']})")
            _start_task_thread(task)
        else:
            time.sleep(2)


def _start_task_thread(task: dict):
    """启动任务处理线程"""
    t = threading.Thread(target=_process_task, args=(task,), daemon=True)
    t.start()
    t.join()


def _run_transcriber_subprocess(audio_file: str, output_dir: Path, episode_name: str, episode_url: str, eid: str, episode_id: int, timeout: int = 7200) -> dict:
    """
    启动子进程运行转写，带超时保护。
    使用线程读取 stdout，解决 Windows select() 对管道无效的问题。
    """
    global _proc_to_kill
    import queue as _queue
    transcriber_py = Path(__file__).parent / "transcriber.py"

    cmd = [
        sys.executable,
        str(transcriber_py),
        audio_file,
        str(output_dir),
        episode_name,
        episode_url,
    ]

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "HF_ENDPOINT": "https://hf-mirror.com",
        "HF_HOME": r"C:\Users\wule_\.cache\hf_test",
    }
    ffmpeg_dir = str(Path(__file__).parent / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin")
    cuda_bin = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"
    env["PATH"] = ffmpeg_dir + os.pathsep + cuda_bin + os.pathsep + os.environ.get("PATH", "")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        encoding='utf-8',
        errors='replace',
    )
    _proc_to_kill = proc

    # 用线程安全队列传递子进程的输出行
    out_queue: _queue.Queue = _queue.Queue(maxsize=100)

    def _drain_stdout():
        """后台线程：持续读取 stdout直到进程结束，不漏任何输出"""
        try:
            for line in iter(proc.stdout.readline, ''):
                if line:
                    out_queue.put(line)
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    # 启动后台读取线程
    drainer = threading.Thread(target=_drain_stdout, daemon=True)
    drainer.start()

    result = None
    start_wait = time.time()

    result = None
    start_ts = time.time()

    try:
        while True:
            if _task_terminated:
                proc.terminate()
                proc.wait()
                return {"ok": False, "error": "已终止"}

            # 检查进程是否结束
            if proc.poll() is not None:
                break

            # 从队列读取子进程输出（每0.5秒检查一次终止标志）
            try:
                line = out_queue.get(block=True, timeout=0.5)
            except _queue.Empty:
                continue

            if line.startswith("STATUS:"):
                try:
                    msg = json.loads(line[7:])
                    data = msg.get("data", "")
                    m = re.search(r'\[(\d+)%\]', str(data))
                    if m:
                        pct = int(m.group(1))
                        task_update(eid, status="transcribing", progress=pct, status_text=str(data))
                        db.update_task_progress(episode_id, pct)
                except Exception:
                    pass
            elif line.startswith("RESULT:"):
                try:
                    result = json.loads(line[7:])
                    try:
                        (output_dir / f"_transcribe_result_{proc.pid}.json").unlink(missing_ok=True)
                    except Exception:
                        pass
                except Exception:
                    pass

        # 进程已结束，等待带超时
        elapsed = int(time.time() - start_ts)
        remaining = max(1, timeout - elapsed)
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"ok": False, "error": f"转写超时（{timeout}秒）"}

        if result:
            return result
        if _task_terminated:
            return {"ok": False, "error": "已终止"}
        return {"ok": False, "error": f"转写进程异常退出: {proc.returncode}"}

    finally:
        _proc_to_kill = None


# --------------- 刷新按钮功能 ---------------

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """
    刷新按钮：关闭当前 Flask，重启
    通过启动新进程 + 退出当前实现
    """
    import threading

    def restart():
        time.sleep(0.5)
        python = sys.executable
        subprocess.Popen([python, str(Path(__file__).parent / "app.py")])
        os._exit(0)

    threading.Thread(target=restart, daemon=True).start()
    return jsonify({"ok": True})


# --------------- 单实例保护 ---------------

PID_FILE = Path(__file__).parent / ".voicetofile.pid"
LOCK_FILE = Path(__file__).parent / ".voicetofile.lock"
_lock_fd = None

def _is_process_running(pid: int) -> bool:
    """Windows 上检查进程是否存在"""
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    return kernel32.OpenProcess(SYNCHRONIZE, 0, pid) != 0

def _acquire_lock():
    """尝试获取文件锁，保证同一时刻只有一个实例运行"""
    global _lock_fd
    import msvcrt
    try:
        _lock_fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
        msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False

def _release_lock():
    """释放文件锁"""
    global _lock_fd
    import msvcrt
    if _lock_fd is not None:
        try:
            msvcrt.locking(_lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass

# --------------- 初始化 ---------------

if __name__ == "__main__":
    # 单实例保护：优先用锁文件，锁文件残留时用 PID 文件兜底
    if not _acquire_lock():
        # 锁获取失败（可能锁文件残留），检查 PID 文件里进程是否还活着
        if PID_FILE.exists():
            try:
                old_pid = int(PID_FILE.read_text().strip())
                if _is_process_running(old_pid):
                    print("[错误] VoiceToFile 已在运行中，请先关闭后再启动")
                    exit(1)
                print("[警告] 发现残留 PID 文件，已清理")
                PID_FILE.unlink()
            except (ValueError, OSError):
                if PID_FILE.exists():
                    PID_FILE.unlink()
        # 再次尝试获取锁
        if not _acquire_lock():
            print("[错误] VoiceToFile 已在运行中，请先关闭后再启动")
            exit(1)

    PID_FILE.write_text(str(os.getpid()))

    def _cleanup(signum, frame):
        _release_lock()
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        sys.exit(0)
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    # 初始化数据库并确保虚拟播客存在
    db.init_db()
    db.get_or_create_manual_podcast()

    # 启动时清理残留任务：downloading/transcribing 直接删除（进程崩溃遗留，不留后患）
    stale = db.cleanup_stale_tasks()
    if stale > 0:
        print(f"[队列] 已删除 {stale} 个残留任务")

    # 启动队列 worker（单线程，每次只处理一个任务）
    t = threading.Thread(target=_queue_worker, daemon=True, name="QueueWorker")
    t.start()
    print(f"[队列] Worker 线程已启动 (alive={t.is_alive()})")

    # 检查 ffmpeg
    if not Path(__file__).parent.joinpath("ffmpeg").exists():
        print("警告: ffmpeg 目录不存在，请从 b-site 项目复制 ffmpeg 文件夹")

    port = 18990
    print(f"VoiceToFile 启动中... http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
