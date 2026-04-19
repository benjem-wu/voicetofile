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

# 当前 subprocess 引用（分开存储，终止时杀正确的进程）
_download_proc = None   # download() 内部的 yt-dlp subprocess
_transcribe_proc = None # _run_transcriber_subprocess 的 transcriber subprocess

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
    global _download_proc, _transcribe_proc, _task_terminated, _current_task_info, _current_audio_file, _current_output_dir

    episode_id = task["id"]
    eid = task["eid"]
    podcast_name = task.get("podcast_name", task.get("name", ""))
    episode_name = task["name"]
    output_dir = get_output_dir(podcast_name)
    start_time = time.time()

    # ---- 无效 eid 快速过滤（防止脏数据进入网络请求）----
    if not eid or eid.startswith("test_") or len(eid) < 10:
        addLog(f"[脏数据] episode_id={episode_id} eid={eid!r} 无效，跳过", "err")
        db.mark_task_failed(episode_id, f"无效eid: {eid}")
        return

    _current_task_info = task
    _current_output_dir = output_dir
    audio_file = None

    task_update(eid, status="downloading", progress=0, elapsed=0)

    # ---- fetch_episode_info 总超时保护（防止网络卡死导致任务永远卡住）----
    _fetch_done = False
    _fetch_result = [None]  # [0] = (ep_detail or exception)
    def _fetch_target():
        global _fetch_done
        try:
            _fetch_result[0] = scraper.fetch_episode_info(eid, interval=config.COOKIE_INTERVAL)
        except Exception as ex:
            _fetch_result[0] = ex
        finally:
            _fetch_done = True

    fetch_thread = threading.Thread(target=_fetch_target, daemon=True)
    fetch_thread.start()
    fetch_thread.join(timeout=90)  # 最多等 90 秒
    if _fetch_result[0] is None:
        # 超时了，fetch_thread 还在跑，但我们已经不等了
        raise ValueError(f"获取音频 URL 超时（90秒），网络可能有问题")
    if isinstance(_fetch_result[0], Exception):
        raise _fetch_result[0]
    ep_detail = _fetch_result[0]

    try:
        # ---- 获取音频 URL ----
        addLog(f"[下载] {episode_name[:30]}...", "tag")
        print(f"[DEBUG] fetch_episode_info OK: audio_url={bool(ep_detail.audio_url)}", flush=True)
        if not ep_detail.audio_url:
            raise ValueError("无法获取音频 URL")
        if ep_detail.is_paid:
            raise ValueError(f"该集为付费内容（{ep_detail.paid_price}），跳过")
        if _task_terminated:
            raise ValueError("已终止")

        # ---- 下载音频 ----
        dl = downloader.Downloader(output_dir)
        dl_proc_ref = {}
        dl_result = dl.download(ep_detail.audio_url, episode_name, eid,
                               check_terminated=lambda: _task_terminated,
                               proc_ref=dl_proc_ref,
                               timeout=config.DOWNLOAD_TIMEOUT)
        if dl_result["ok"]:
            _download_proc = dl_proc_ref.get("proc")
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
        db.update_episode_status(episode_id, "transcribing")

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

        # ---- 精准清理：只留文字稿，删除所有临时文件 ----
        dl.cleanup_progress(eid)
        try:
            Path(audio_file).unlink(missing_ok=True)
        except Exception:
            pass
        try:
            # 转写状态文件（按 episode_id 精准删除）
            (output_dir / f"_transcribe_state_{episode_id}.json").unlink(missing_ok=True)
            (output_dir / f"_transcribe_state_{episode_id}.tmp").unlink(missing_ok=True)
            # 转写结果文件（所有 PID 的结果文件，转写子进程已结束，可以全量删）
            for f in output_dir.glob("_transcribe_result_*.json"):
                f.unlink(missing_ok=True)
            # 下载进度文件（双重保险）
            (output_dir / f"_download_progress_{eid}.txt").unlink(missing_ok=True)
            # 原始音频文件残留（双重保险）
            clean_name = episode_name.replace('\n', ' ').strip()[:80]
            illegal = '<>:"/\\|?*'
            for ch in illegal:
                clean_name = clean_name.replace(ch, '_')
            (output_dir / f"{clean_name}.m4a").unlink(missing_ok=True)
        except Exception as ex:
            print(f"[清理] 残留文件清理失败: {ex}")

    except Exception as e:
        total_elapsed = time.time() - start_time
        err_msg = str(e)
        addLog(f"[失败] {episode_name[:30]}: {err_msg}", "err")
        print(f"[DEBUG] _process_task exception: {err_msg}", flush=True)

        if _task_terminated:
            print(f"[DEBUG] _task_terminated=True, returning without retry", flush=True)
            return

        # 先加 retry_count，再决定是否重试（持久化到 DB，防止无限重试）
        retry_count = db.increment_retry_count(episode_id)
        print(f"[DEBUG] retry_count={retry_count}, threshold=2, will_retry={retry_count <= 2}", flush=True)
        if retry_count <= 2:
            # 重试：改回 queued，worker loop 下一次 poll 会重新拾取
            db.update_episode_status(episode_id, "queued", txt_path="", error_msg="")
            addLog(f"[重试] {episode_name[:30]} 第{retry_count}次", "tag")
            broadcast_sse("task_done", {
                "eid": eid, "name": episode_name,
                "podcast_name": podcast_name, "status": "queued"
            })
            return

        db.mark_task_failed(episode_id, err_msg)
        task_update(eid, status="failed", elapsed=int(total_elapsed), error=err_msg)
        broadcast_sse("task_done", {
            "eid": eid, "name": episode_name,
            "podcast_name": podcast_name, "status": "failed"
        })

    finally:
        print(f"[finally] episode_id={episode_id} eid={eid} _task_terminated={_task_terminated}")
        # 杀子进程（如果还在运行）- 用 poll() 非阻塞检查避免僵尸进程无限阻塞
        if _download_proc is not None:
            try:
                if _download_proc.poll() is None:
                    _download_proc.kill()
                    _download_proc.wait()
            except Exception:
                pass
            _download_proc = None
        if _transcribe_proc is not None:
            try:
                if _transcribe_proc.poll() is None:
                    _transcribe_proc.kill()
                    _transcribe_proc.wait()
            except Exception:
                pass
            _transcribe_proc = None

        # _task_terminated=True 时，DB 状态和广播由 terminate_current_task() 统一处理
        # 这里只清状态
        _task_terminated = False
        _current_task_info = None
        _current_audio_file = None
        _current_output_dir = None


def _read_transcribe_state(output_dir: Path, episode_id: int) -> dict | None:
    """
    读取转写状态文件，返回 dict 或 None。
    状态文件路径：_transcribe_state_{episode_id}.json
    """
    state_file = output_dir / f"_transcribe_state_{episode_id}.json"
    if not state_file.exists():
        return None
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _poll_transcribe_state(output_dir: Path, episode_id: int, eid: str,
                            last_progress: list) -> int:
    """
    轮询转写状态文件，更新 DB + SSE。
    last_progress: [int]，传出最新 progress，供调用方追踪。
    返回最新的 progress 值（未变化返回 last_progress[0]）。
    """
    state = _read_transcribe_state(output_dir, episode_id)
    if state is None:
        return last_progress[0]

    status = state.get("status", "")
    progress = state.get("progress", 0)
    status_text = state.get("status_text", "")

    # 有新的文字状态时推送给前端
    if status_text:
        task_update(eid, status="transcribing", progress=progress, status_text=status_text)

    # progress 变化时写 DB（避免频繁写入）
    if progress != last_progress[0]:
        db.update_task_progress(episode_id, progress)
        last_progress[0] = progress

    return progress


def _run_transcriber_subprocess(audio_file: str, output_dir: Path, episode_name: str,
                                 episode_url: str, eid: str, episode_id: int,
                                 timeout: int = 7200) -> dict:
    """
    启动子进程运行转写，带超时保护。
    进度来源：状态文件（_transcribe_state_{episode_id}.json，权威）
    RESULT 兜底：进程退出时从状态文件读（万一状态文件未写入则从 stdout 读）
    """
    global _transcribe_proc
    transcriber_py = config.PROJECT_ROOT / "transcriber.py"

    cmd = [
        sys.executable,
        str(transcriber_py),
        audio_file,
        str(output_dir),
        episode_name,
        episode_url,
        str(episode_id),
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
    _transcribe_proc = proc

    out_queue: queue.Queue = queue.Queue(maxsize=0)  # 无界队列，避免有界队列填满死锁

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
    last_progress = [0]  # 用于 _poll_transcribe_state 追踪 progress 变化

    try:
        while True:
            if _task_terminated:
                proc.terminate()
                proc.wait()
                return {"ok": False, "error": "已终止"}

            if proc.poll() is not None:
                # 进程已退出：先从状态文件读 result（权威来源）
                # break 前最后一次轮询，确保 100% 进度被推送到前端
                _poll_transcribe_state(output_dir, episode_id, eid, last_progress)
                state = _read_transcribe_state(output_dir, episode_id)
                if state and state.get("result"):
                    result = state["result"]
                else:
                    # 兜底：从 stdout queue 读 RESULT:
                    while True:
                        try:
                            line = out_queue.get(block=False)
                            if line.startswith("RESULT:"):
                                try:
                                    result = json.loads(line[7:])
                                except Exception:
                                    pass
                        except queue.Empty:
                            break
                break

            # 状态文件轮询（每轮询一次，约 1 秒）
            _poll_transcribe_state(output_dir, episode_id, eid, last_progress)
            time.sleep(1)

        elapsed = int(time.time() - start_ts)
        remaining = max(1, timeout - elapsed)

        # 关键修复：先 poll() 检查进程是否已退出（不阻塞）
        # 避免僵尸进程导致 proc.wait() 无限阻塞
        poll_result = proc.poll()
        if poll_result is None:
            # 进程尚未退出，才等待
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return {"ok": False, "error": f"转写超时（{timeout}秒）"}

        # 双重保护：确保 100% 完成事件被推送
        if result and result.get("ok"):
            task_update(eid, status="done_deleted", progress=100)

        if result:
            return result
        if _task_terminated:
            return {"ok": False, "error": "已终止"}
        return {"ok": False, "error": f"转写进程异常退出: {proc.returncode}"}

    finally:
        _transcribe_proc = None


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


def kill_active_subprocess():
    """杀掉当前活跃的子进程（download 或 transcribe）- poll() 非阻塞避免僵尸进程挂起"""
    global _download_proc, _transcribe_proc
    if _download_proc is not None:
        try:
            if _download_proc.poll() is None:
                _download_proc.kill()
                _download_proc.wait()
        except Exception:
            pass
        _download_proc = None
    if _transcribe_proc is not None:
        try:
            if _transcribe_proc.poll() is None:
                _transcribe_proc.kill()
                _transcribe_proc.wait()
        except Exception:
            pass
        _transcribe_proc = None


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
    global _task_terminated, _current_task_info, _current_audio_file, _current_output_dir

    _task_terminated = True

    episode_id = _current_task_info.get("id") if _current_task_info else None

    # 杀子进程（download 和 transcribe 各自杀自己的）
    kill_active_subprocess()

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
