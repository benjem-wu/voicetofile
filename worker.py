"""
队列 Worker 模块
VoiceToFile — 小宇宙播客转文字

包含：任务处理线程、队列 worker 循环、转写子进程管理。
"""
import json
import re
import sys
import os
import time
import queue
import threading
import subprocess
from pathlib import Path
from datetime import datetime

import db
import scraper
import downloader
from sse import broadcast_sse, task_update, addLog
from _utils import sanitize_filename
import config

# --------------- 全局状态（进程级别）---------------

# 当前 subprocess 引用（用于 kill）
_proc_to_kill = None

# 标记当前任务已被 api_queue_stop 提前终止
_task_terminated = False

# 当前处理中的任务信息（供 api_queue_stop 获取 episode_id 和 audio_file）
_current_task_info = None

# 当前音频文件路径（下载完成后记录，供终止时验证）
_current_audio_file = None

# 当前输出目录（供终止时清理 progress 文件）
_current_output_dir = None


# --------------- 路径辅助 ---------------

def get_output_dir(podcast_name: str) -> Path:
    """获取播客输出目录"""
    out = config.OUTPUT_ROOT / sanitize_filename(podcast_name)
    out.mkdir(parents=True, exist_ok=True)
    return out


# --------------- 音频验证 ---------------

def _verify_audio_complete(audio_file: str) -> bool:
    """用 ffprobe 检测音频文件是否完整（能获取到时长）"""
    if not audio_file or not Path(audio_file).exists():
        return False
    try:
        size = Path(audio_file).stat().st_size
        if size < 1024 * 1024:
            print(f"[终止] 音频文件太小 ({size} bytes)，认为不完整")
            return False
    except Exception:
        pass
    ffprobe_path = config.FFPROBE_PATH
    if not ffprobe_path.exists():
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


# --------------- 任务处理 ---------------

def _process_task(task: dict):
    """处理单个 episode：下载 → 转写 → 清理。"""
    global _proc_to_kill, _task_terminated, _current_task_info, _current_audio_file, _current_output_dir

    episode_id = task["id"]
    eid = task["eid"]
    podcast_name = task.get("podcast_name", task.get("name", ""))
    episode_name = task["name"]
    output_dir = get_output_dir(podcast_name)
    start_time = time.time()

    _current_task_info = task
    _current_output_dir = output_dir
    audio_file = None

    task_update(eid, status="downloading", progress=0, elapsed=0)

    try:
        # ---- 获取音频 URL ----
        addLog(f"[下载] {episode_name[:30]}...", "tag")
        ep_detail = scraper.fetch_episode_info(eid, interval=config.COOKIE_INTERVAL)
        if not ep_detail.audio_url:
            raise ValueError("无法获取音频 URL")
        if ep_detail.is_paid:
            raise ValueError(f"该集为付费内容（{ep_detail.paid_price}），跳过")
        if _task_terminated:
            raise ValueError("已终止")

        # ---- 下载音频 ----
        dl = downloader.Downloader(output_dir)
        dl_result = dl.download(ep_detail.audio_url, episode_name, eid,
                               check_terminated=lambda: _task_terminated)
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

        sub_result = _run_transcriber_subprocess(
            audio_file, output_dir, episode_name,
            ep_detail.audio_url, eid, episode_id,
            timeout=config.TRANSCRIBE_TIMEOUT
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
            db.enqueue_task(episode_id)
            addLog(f"[重试] {episode_name[:30]} 第{retry_count+1}次", "tag")
            broadcast_sse("task_done", {
                "eid": eid, "name": episode_name,
                "podcast_name": podcast_name, "status": "queued"
            })
            return

        if _task_terminated:
            return

        db.mark_task_failed(episode_id, err_msg)
        task_update(eid, status="failed", elapsed=int(total_elapsed), error=err_msg)
        broadcast_sse("task_done", {
            "eid": eid, "name": episode_name,
            "podcast_name": podcast_name, "status": "failed"
        })

    finally:
        print(f"[finally] episode_id={episode_id} eid={eid} _task_terminated={_task_terminated}")
        # 杀子进程（如果还在运行）
        if _proc_to_kill is not None:
            try:
                _proc_to_kill.kill()
                _proc_to_kill.wait()
            except Exception:
                pass
            _proc_to_kill = None

        # _task_terminated=True 时，DB 状态和广播由 terminate_current_task() 统一处理
        # 这里只清状态
        _task_terminated = False
        _current_task_info = None
        _current_audio_file = None
        _current_output_dir = None


def _run_transcriber_subprocess(audio_file: str, output_dir: Path, episode_name: str,
                                 episode_url: str, eid: str, episode_id: int,
                                 timeout: int = 7200) -> dict:
    """
    启动子进程运行转写，带超时保护。
    使用线程读取 stdout，解决 Windows select() 对管道无效的问题。
    """
    global _proc_to_kill
    transcriber_py = config.PROJECT_ROOT / "transcriber.py"

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
        "HF_ENDPOINT": config.HF_ENDPOINT,
        "HF_HOME": config.HF_HOME,
    }
    env["PATH"] = str(config.FFMPEG_DIR) + os.pathsep + config.CUDA_BIN + os.pathsep + os.environ.get("PATH", "")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        encoding='utf-8',
        errors='replace',
    )
    _proc_to_kill = proc

    out_queue: queue.Queue = queue.Queue(maxsize=100)

    def _drain_stdout():
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

    drainer = threading.Thread(target=_drain_stdout, daemon=True)
    drainer.start()

    result = None
    start_ts = time.time()

    try:
        while True:
            if _task_terminated:
                proc.terminate()
                proc.wait()
                return {"ok": False, "error": "已终止"}

            if proc.poll() is not None:
                break

            try:
                line = out_queue.get(block=True, timeout=0.5)
            except queue.Empty:
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


# --------------- Worker 循环 ---------------

def _queue_worker():
    """后台 worker：永远只从 DB 抢任务"""
    print("[队列] Worker 线程已启动")
    while True:
        task = db.get_next_queued_task()
        if task:
            print(f"[队列] 拾取任务: {task['name']} (id={task['id']})")
            _start_task_thread(task)
        else:
            time.sleep(config.WORKER_POLL_INTERVAL)


def _start_task_thread(task: dict):
    """启动任务处理线程（t.join() 阻塞，等待任务完全结束）"""
    t = threading.Thread(target=_process_task, args=(task,), daemon=True)
    t.start()
    t.join()


# --------------- Worker 线程管理（供 api_queue_stop 等待终止完成）---------------

_current_worker_thread = None


def _start_task_thread(task: dict):
    """启动任务处理线程"""
    global _current_worker_thread
    t = threading.Thread(target=_process_task, args=(task,), daemon=True)
    _current_worker_thread = t
    t.start()
    t.join()
    _current_worker_thread = None


def wait_for_worker_exit():
    """等待当前任务处理线程退出（供 api_queue_stop 调用）"""
    global _current_worker_thread
    if _current_worker_thread and _current_worker_thread.is_alive():
        _current_worker_thread.join(timeout=60)


def terminate_subprocess():
    """杀掉转写子进程（供 api_queue_stop 调用）"""
    global _proc_to_kill
    if _proc_to_kill is not None:
        try:
            _proc_to_kill.kill()
            _proc_to_kill.wait()
        except Exception:
            pass
        _proc_to_kill = None


def reset_termination_state():
    """重置终止状态（供 api_queue_stop 在等待完成后调用）"""
    global _task_terminated, _current_task_info, _current_audio_file, _current_output_dir
    _task_terminated = False
    _current_task_info = None
    _current_audio_file = None
    _current_output_dir = None


# --------------- 对外暴露的 worker 控制函数（供 routes 调用）---------------

def get_current_task_info():
    return _current_task_info


def get_current_audio_file():
    return _current_audio_file


def set_task_terminated():
    global _task_terminated
    _task_terminated = True


def is_task_terminated():
    return _task_terminated


def terminate_current_task():
    """
    在 worker 线程内部执行终止逻辑（杀进程 + 删文件 + 返回 episode_id）。
    供 routes/queue.py 的 api_queue_stop 调用。
    """
    global _task_terminated, _proc_to_kill, _current_task_info, _current_audio_file, _current_output_dir

    _task_terminated = True

    episode_id = _current_task_info.get("id") if _current_task_info else None

    # 杀子进程
    if _proc_to_kill is not None:
        try:
            _proc_to_kill.kill()
            _proc_to_kill.wait()
        except Exception:
            pass
        _proc_to_kill = None

    # 删音频文件
    audio_path = _current_audio_file
    if audio_path and Path(audio_path).exists():
        try:
            Path(audio_path).unlink()
            print(f"[终止] 已删除音频: {audio_path}")
        except Exception as e:
            print(f"[终止] 删除音频失败: {e}")

    # 删临时文件（从正确的 output_dir，用 glob 匹配未知子进程 PID）
    out_dir = _current_output_dir
    if out_dir:
        for pattern in ["_download_progress_*.txt", "_transcribe_progress_*.txt"]:
            for f in out_dir.glob(pattern):
                try:
                    f.unlink()
                    print(f"[终止] 已删除: {f.name}")
                except Exception:
                    pass

    return episode_id
