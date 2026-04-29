"""
单集 Service
VoiceToFile — 小宇宙播客转文字

封装 episode 相关业务逻辑，供 routes 层调用。
"""
import os
import re
from pathlib import Path
from datetime import datetime

import db
import scraper
import worker as w
import config
from sse import addLog, task_update


def add_episode(url: str) -> dict:
    """
    模式B：手动添加单集（统一归入"精选播客"虚拟播客）

    返回 dict：
      - ok: bool
      - episode_id: int（入队后的 id）
      - eid: str
      - name: str
      - podcast_id: int
      - error: str（ok=False 时）
    """
    m = re.search(r"/episode/([a-f0-9]+)", url)
    if not m:
        return {"ok": False, "error": "无法从 URL 提取 Episode ID"}
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
                return {
                    "ok": False,
                    "error": "该单集已在队列中或已完成",
                    "episode_id": existing_ep["id"],
                    "status": existing_ep["status"],
                }
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

        return {
            "ok": True,
            "episode_id": episode_id,
            "eid": eid,
            "name": ep.name,
            "podcast_id": podcast_id,
        }
    except Exception as e:
        addLog(f"[错误] 添加失败: {e}", "err")
        return {"ok": False, "error": str(e)}


def enqueue_episodes(episode_ids: list[int]) -> dict:
    """
    批量入队 episodes（含合法性检查）。

    返回 dict：
      - ok: bool
      - added: int
      - skipped: int
    """
    if not episode_ids:
        return {"ok": False, "error": "未选择任何集"}
    if len(episode_ids) > config.MAX_ENQUEUE:
        return {"ok": False, "error": f"最多同时入队 {config.MAX_ENQUEUE} 集"}

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
    return {"ok": True, "added": len(added), "skipped": len(skipped)}


def retry_episode(episode_id: int) -> dict:
    """
    重新处理失败的 episode：删音频/TXT → 重置状态 → 入队。

    返回 dict：
      - ok: bool
      - error: str（ok=False 时）
    """
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "Episode 不存在"}
    if ep["status"] not in ("failed", "done_deleted"):
        return {"ok": False, "error": f"当前状态 {ep['status']} 不支持重试"}

    for path_field in ("audio_path", "txt_path"):
        p = ep.get(path_field) or ""
        if p and Path(p).exists():
            try:
                Path(p).unlink()
            except Exception:
                pass

    db.reset_episode_for_retry(episode_id)
    db.enqueue_task(episode_id)
    return {"ok": True}


def reenqueue_episode(episode_id: int) -> dict:
    """将 pending 状态的 episode 入队。"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "Episode 不存在"}
    if ep["status"] != "pending":
        return {"ok": False, "error": f"当前状态 {ep['status']} 不是 pending，无法入队"}

    db.enqueue_task(episode_id)
    return {"ok": True}


def open_episode_txt(episode_id: int) -> dict:
    """用系统程序打开 TXT 文件。"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "Episode 不存在"}
    if not ep["txt_path"] or not os.path.exists(ep["txt_path"]):
        return {"ok": False, "error": "文字稿文件不存在"}
    try:
        os.startfile(ep["txt_path"])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def dequeue_episode(episode_id: int) -> dict:
    """将 episode 从队列移除，恢复为 pending。"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "Episode 不存在"}
    db.update_episode_status(episode_id, "pending", txt_path="", error_msg="")
    return {"ok": True}


def pause_episode(episode_id: int) -> dict:
    """暂停任务：保留音频，状态改为 paused。"""
    w.set_task_terminated()

    ep = db.get_episode_by_id(episode_id)
    if ep:
        existing_audio = ep.get("audio_path") or ""
        audio_file = w.get_current_audio_file() if w.get_current_audio_file() else existing_audio
        db.pause_episode(episode_id, audio_path=audio_file)
        addLog(f"[暂停] {ep['name'][:30]} 已暂停", "tag")

    return {"ok": True}


def reset_episode(episode_id: int) -> dict:
    """重置任务：删除音频，状态改为 pending。"""
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

    return {"ok": True}


def resume_episode(episode_id: int) -> dict:
    """
    继续暂停的任务。
    音频完整则跳到转写阶段，不完整则重下。
    """
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "任务不存在"}

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
    return {"ok": True}


def get_episode(episode_id: int) -> dict:
    """获取 episode 详情（含错误信息）。"""
    ep = db.get_episode_by_id(episode_id)
    if not ep:
        return {"ok": False, "error": "Episode 不存在"}
    return {"ok": True, "episode": dict(ep)}
