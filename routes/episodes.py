"""
单集相关路由
/api/episode/*, /api/episodes/*
"""
import os
import re
from pathlib import Path
from flask import Blueprint, request, jsonify
from datetime import datetime

import db
import scraper
import worker as w
from sse import addLog, task_update, broadcast_sse
import config
from services import refresh_podcast

episodes_bp = Blueprint("episodes", __name__, url_prefix="/api")


@episodes_bp.route("/episode/add", methods=["POST"])
def api_add_episode():
    """
    模式B：手动添加单集（统一归入"精选播客"虚拟播客）
    POST body: {"url": "https://www.xiaoyuzhoufm.com/episode/xxx"}
    """
    data = request.get_json()
    url = data.get("url", "").strip()

    m = re.search(r"/episode/([a-f0-9]+)", url)
    if not m:
        return jsonify({"ok": False, "error": "无法从 URL 提取 Episode ID"})
    eid = m.group(1)

    addLog(f"[单集] 正在获取: {eid}", "tag")

    try:
        ep = scraper.fetch_episode_info(eid, interval=config.COOKIE_INTERVAL)
        addLog(f"[单集] {ep.name}，音频: {ep.audio_url[:40]}...", "done")

        podcast_id = db.get_or_create_manual_podcast()

        existing_ep = db.get_episode_by_eid(podcast_id, eid)
        if existing_ep:
            if existing_ep["status"] in ("done_deleted", "failed"):
                db.reset_episode_for_retry(existing_ep["id"])
                episode_id = existing_ep["id"]
            else:
                return jsonify({
                    "ok": False,
                    "error": "该单集已在队列中或已完成",
                    "episode_id": existing_ep["id"],
                    "status": existing_ep["status"],
                })
        else:
            ep_records = [{
                "podcast_id": podcast_id,
                "eid": ep.eid,
                "name": ep.name,
                "pub_date": ep.pub_date,
                "duration": ep.duration,
                "is_paid": ep.is_paid,
                "source": "manual",
            }]
            db.add_episodes(ep_records, source="manual")
            ep_record = db.get_episode_by_eid(podcast_id, eid)
            episode_id = ep_record["id"]

        return jsonify({
            "ok": True,
            "episode_id": episode_id,
            "eid": eid,
            "name": ep.name,
            "podcast_id": podcast_id,
        })
    except Exception as e:
        addLog(f"[错误] 添加失败: {e}", "err")
        return jsonify({"ok": False, "error": str(e)})


@episodes_bp.route("/episodes/enqueue", methods=["POST"])
def api_enqueue_episodes():
    """
    将选中的 episode 加入队列
    POST body: {"episode_ids": [1,2,3]}
    """
    data = request.get_json()
    episode_ids = data.get("episode_ids", [])

    if not episode_ids:
        return jsonify({"ok": False, "error": "未选择任何集"})
    if len(episode_ids) > config.MAX_ENQUEUE:
        return jsonify({"ok": False, "error": f"最多同时入队 {config.MAX_ENQUEUE} 集"})

    added = []
    skipped = []
    for eid in episode_ids:
        ep = db.get_episode_by_id(eid)
        if not ep:
            continue
        if ep["is_paid"]:
            skipped.append(ep["name"])
            continue
        if ep["status"] in ("downloading", "transcribing", "queued"):
            skipped.append(f"{ep['name']}（状态不允许）")
            continue
        ok = db.enqueue_task(eid)
        if ok:
            added.append(ep["name"])
            task_update(ep["eid"], status="queued", progress=0)
        else:
            skipped.append(f"{ep['name']}（已在队列中）")

    msg = f"入队 {len(added)} 集"
    if skipped:
        msg += f"，跳过 {len(skipped)} 集"
    addLog(f"[队列] {msg}", "tag")
    return jsonify({"ok": True, "added": len(added), "skipped": len(skipped)})


@episodes_bp.route("/episodes/refresh", methods=["POST"])
def api_refresh_episodes():
    """
    重新从网络获取播客集列表
    POST body: {"podcast_id": int}
    """
    data = request.get_json()
    podcast_id = int(data["podcast_id"])

    result = refresh_podcast(podcast_id)

    # 广播刷新进度（供前端实时显示）
    podcast_name = result.get("podcast_name", "")
    if result["ok"]:
        if result.get("new_count", 0) > 0:
            broadcast_sse("podcast_refresh_done", {
                "type": "podcast_refresh_done",
                "podcast_id": podcast_id,
                "podcast_name": podcast_name,
                "result": "success",
                "new_count": result.get("new_count", 0),
            })
        else:
            broadcast_sse("podcast_refresh_done", {
                "type": "podcast_refresh_done",
                "podcast_id": podcast_id,
                "podcast_name": podcast_name,
                "result": "no_update",
                "new_count": 0,
            })
    else:
        broadcast_sse("podcast_refresh_done", {
            "type": "podcast_refresh_done",
            "podcast_id": podcast_id,
            "podcast_name": podcast_name,
            "result": "failed",
            "error": result.get("error", "未知错误"),
        })

    return jsonify(result)


@episodes_bp.route("/episode/retry/<int:episode_id>", methods=["POST"])
def api_retry_episode(episode_id: int):
    """重新处理失败的 episode"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    if ep["status"] not in ("failed", "done_deleted"):
        return jsonify({"ok": False, "error": f"当前状态 {ep['status']} 不支持重试"})

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


@episodes_bp.route("/episode/reenqueue/<int:episode_id>", methods=["POST"])
def api_reenqueue_episode(episode_id: int):
    """重新入队 pending 状态的任务"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    if ep["status"] != "pending":
        return jsonify({"ok": False, "error": f"当前状态 {ep['status']} 不是 pending，无法入队"})

    db.enqueue_task(episode_id)
    return jsonify({"ok": True})


@episodes_bp.route("/episode/open/<int:episode_id>")
def api_episode_open(episode_id: int):
    """用系统程序打开 TXT 文件"""
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


@episodes_bp.route("/episode/<int:episode_id>", methods=["GET"])
def api_get_episode(episode_id: int):
    """获取 episode 详情（含错误信息）"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    return jsonify({"ok": True, "episode": dict(ep)})


@episodes_bp.route("/episode/dequeue", methods=["POST"])
def api_dequeue_episode():
    """将 episode 从队列移除，恢复为 pending"""
    data = request.get_json()
    episode_id = int(data["episode_id"])
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "Episode 不存在"})
    db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
    return jsonify({"ok": True})


@episodes_bp.route("/episode/pause/<int:episode_id>", methods=["POST"])
def api_episode_pause(episode_id: int):
    """暂停任务：保留音频，状态改为 paused"""
    w.set_task_terminated()

    ep = db.get_episode_by_id(episode_id)
    if ep:
        existing_audio = ep.get("audio_path") or ""
        audio_file = w.get_current_audio_file() if w.get_current_audio_file() else existing_audio
        db.pause_episode(episode_id, audio_path=audio_file)
        addLog(f"[暂停] {ep['name'][:30]} 已暂停", "tag")

    return jsonify({"ok": True})


@episodes_bp.route("/episode/reset/<int:episode_id>", methods=["POST"])
def api_episode_reset(episode_id: int):
    """重置任务：删除音频，状态改为 pending"""
    w.set_task_terminated()

    audio_file = w.get_current_audio_file()
    if not audio_file:
        ep = db.get_episode_by_id(episode_id)
        if ep:
            audio_file = ep.get("audio_path") or ""
    if audio_file and Path(audio_file).exists():
        try:
            Path(audio_file).unlink()
        except Exception:
            pass

    ep = db.get_episode_by_id(episode_id)
    if ep:
        db.reset_episode_for_retry(episode_id)
        addLog(f"[重置] {ep['name'][:30]} 已重置", "tag")

    return jsonify({"ok": True})


@episodes_bp.route("/episode/resume/<int:episode_id>", methods=["POST"])
def api_episode_resume(episode_id: int):
    """继续暂停的任务"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return jsonify({"ok": False, "error": "任务不存在"}), 404

    audio_path = ep.get("audio_path") or ""
    audio_complete = False
    if audio_path and Path(audio_path).exists():
        audio_complete = w._verify_audio_complete(audio_path)

    if audio_complete:
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE episodes SET status = 'transcribing', audio_path = '', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), episode_id)
            )
            conn.commit()
        finally:
            conn.close()
    else:
        if audio_path:
            try:
                Path(audio_path).unlink()
            except Exception:
                pass
        db.update_episode_status(episode_id, "downloading")

    task_update(ep["eid"], status="downloading", progress=0, elapsed=0)
    return jsonify({"ok": True})
