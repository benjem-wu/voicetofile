"""
转写模块
VoiceToFile — 小宇宙播客转文字
使用 Faster-Whisper（large-v3，CUDA）进行语音转文字
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime

# 强制 UTF-8 输出
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# --------------- 配置（可被外部覆盖）---------------

FFMPEG_PATH = Path(__file__).parent / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe"
WHISPER_MODEL = "large-v3"
CUDA_BIN = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"
HF_ENDPOINT = "https://hf-mirror.com"

# Whisper 模型单例缓存（避免重复加载占用 GPU 显存）
_whisper_model_cache = None


# --------------- 标点与分段 ---------------

def add_punctuation(text: str) -> str:
    """为连续文字添加中文标点"""
    # 清理真实换行和字面 \\n
    text = text.replace('\\n', '\n').replace('\n', '').replace('\\n', '')
    if not text or not text.strip():
        return text
    words = text.split()
    result, current, char_count = [], [], 0
    for word in words:
        current.append(word)
        char_count += len(word)
        if char_count >= 25:
            result.append(''.join(current))
            current = []
            char_count = 0
    if current:
        result.append(''.join(current))
    final = '，'.join(result)
    if final and final[-1] not in '。！？':
        final += '。'
    return final


def format_as_article(segments: list, max_gap: float = 3.0, min_para_len: int = 2) -> list:
    """按语义聚合成文章分段"""
    if not segments:
        return []
    paragraphs, current_para, last_end = [], [], 0.0
    for seg in segments:
        text = seg['text'].replace('\n', '').strip()
        if not text:
            continue
        gap = seg['start'] - last_end
        if gap > max_gap and len(current_para) >= min_para_len:
            paragraphs.append(''.join(current_para))
            current_para = [text]
        else:
            current_para.append(text)
        last_end = seg['end']
    if current_para:
        paragraphs.append(''.join(current_para))
    return paragraphs


# --------------- GPU 显存监控（nvidia-smi 优先，回退 torch）---------------

def _get_gpu_memory_nvidia_smi() -> tuple:
    """
    通过 nvidia-smi 获取 GPU 显存使用量（MB）和总量（MB）。
    返回 (used_mb, total_mb)，失败返回 (0, 0)。
    """
    import subprocess
    try:
        # 尝试多个可能的 nvidia-smi 路径
        candidates = [
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            "nvidia-smi",  # PATH 中
        ]
        for exe in candidates:
            try:
                result = subprocess.run(
                    [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=5
                )
                if result.returncode == 0:
                    line = result.stdout.strip().split('\n')[0]
                    parts = line.split(',')
                    if len(parts) == 2:
                        used_mb = float(parts[0].strip())
                        total_mb = float(parts[1].strip())
                        return used_mb, total_mb
            except Exception:
                continue
    except Exception:
        pass
    return 0, 0


def _get_gpu_memory_torch() -> float:
    """通过 torch.cuda 获取显存使用量（GB），失败返回 0。"""
    try:
        import torch
        torch.cuda.init()
        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / 1024**3, 2)
    except Exception:
        pass
    return 0.0


def _monitor_gpu(episode_id: int, status: str, progress: int) -> float:
    """
    监控 GPU 显存，返回 used_gb。
    优先用 nvidia-smi，失败则用 torch.cuda。
    """
    used_mb, total_mb = _get_gpu_memory_nvidia_smi()
    if used_mb > 0 and total_mb > 0:
        used_gb = round(used_mb / 1024, 2)
        total_gb = round(total_mb / 1024, 2)
        print(f"[GPU] episode_id={episode_id} status={status} progress={progress} gpu_mem={used_gb}GB/{total_gb}GB (nvidia-smi)", flush=True)
        return used_gb

    # 回退到 torch
    used_gb = _get_gpu_memory_torch()
    if used_gb > 0:
        print(f"[GPU] episode_id={episode_id} status={status} progress={progress} gpu_mem={used_gb}GB (torch)", flush=True)
    else:
        print(f"[GPU] episode_id={episode_id} status={status} progress={progress} gpu_mem=0.0 (both nvidia-smi and torch failed)", flush=True)
    return used_gb


# --------------- 进度推送 ---------------

def push(event: str, data: str = ""):
    msg = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    print(f"STATUS:{msg}", flush=True)


def write_progress(pid: int, output_dir: Path, pct: int):
    try:
        with open(str(output_dir / f"_transcribe_progress_{pid}.txt"), 'w', encoding='utf-8') as f:
            f.write(str(int(pct)))
    except Exception:
        pass


# --------------- 转写状态文件（权威状态来源）---------------

def _write_state_file(state_file: Path, data: dict):
    """
    原子写入状态文件：先写临时文件再 rename，防止写坏。
    """
    try:
        tmp = state_file.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        tmp.replace(state_file)
    except Exception:
        pass


def write_transcribe_state(output_dir: Path, episode_id: int,
                            status: str, progress: int = 0,
                            result: dict = None, error: str = "",
                            status_text: str = ""):
    """
    写入转写状态文件 _transcribe_state_{episode_id}.json
    """
    data = {
        "status": status,
        "progress": progress,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if result is not None:
        data["result"] = result
    if error:
        data["error"] = error
    if status_text:
        data["status_text"] = status_text

    # GPU 显存监控（nvidia-smi 优先，torch 回退）
    mem_gb = _monitor_gpu(episode_id, status, progress)
    if mem_gb > 0:
        data["gpu_memory_gb"] = mem_gb

    state_file = output_dir / f"_transcribe_state_{episode_id}.json"
    _write_state_file(state_file, data)


# --------------- 转写核心 ---------------

def transcribe(
    audio_path: str | Path,
    output_dir: str | Path,
    episode_name: str,
    episode_url: str = "",
    episode_id: int = 0,
) -> dict:
    """
    主转写函数：音频重采样 → Whisper 转写 → 保存 TXT

    Args:
        audio_path: 音频文件路径（m4a 或 wav）
        output_dir: 输出目录
        episode_name: 集名（用于文件命名）
        episode_url: 集原始链接
        episode_id: 集 ID（用于状态文件命名）

    Returns:
        {"ok": True, "file": "path/to/txt", "content": "全文"}
        或 {"ok": False, "error": "错误信息"}
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    pid = os.getpid()
    _total_start = time.time()
    _episode_id = episode_id

    def _push(event, data=""):
        msg = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        print(f"STATUS:{msg}", flush=True)

    def _write_progress(pct):
        write_progress(pid, output_dir, pct)

    def _write_state(status: str, progress: int = 0, result: dict = None, error: str = "", status_text: str = ""):
        if _episode_id:
            write_transcribe_state(output_dir, _episode_id, status, progress, result, error, status_text)

    try:
        _write_state("starting", 0)
        # ---- 设置环境变量 ----
        ffmpeg_dir = str(FFMPEG_PATH.parent)
        env_path = ffmpeg_dir + os.pathsep + CUDA_BIN + os.pathsep + os.environ.get("PATH", "")
        os.environ["PATH"] = env_path
        os.environ["HF_ENDPOINT"] = HF_ENDPOINT

        # ---- 判断是否需要音频重采样 ----
        audio_exts = {'.m4a', '.mp3', '.wav', '.aac', '.ogg', '.flac', '.opus', '.wma', '.aiff', '.m4b'}
        is_audio_file = audio_path.suffix.lower() in audio_exts
        wav_path = output_dir / "audio.wav"

        # ---- 获取音频总时长 ----
        probe_cmd = [
            str(FFMPEG_PATH.parent / "ffprobe.exe"),
            "-v", "quiet", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(audio_path)
        ]
        try:
            probe_result = subprocess.run(
                probe_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace'
            )
            total_dur = float(probe_result.stdout.strip() or 0)
        except Exception:
            total_dur = 0

        _write_state("resampling", 1, status_text="[1%] 正在重采样为 16kHz WAV...")

        # ---- 音频重采样为 16kHz WAV ----
        if is_audio_file:
            _push("status", "[1%] 检测为音频文件，跳过重采样")
            _write_progress(5)
            wav_path = audio_path  # 直接用原文件
        else:
            _push("status", "[1%] 正在重采样为 16kHz WAV...")
            _write_progress(1)
            _extract_start = time.time()

            cmd = [
                str(FFMPEG_PATH),
                "-i", str(audio_path),
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1", "-y",
                "-progress", "pipe:1",
                str(wav_path)
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding='utf-8', errors='replace'
            )
            last_pct = 1
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        ms = int(line.split("=", 1)[1])
                        cur_sec = ms / 1_000_000
                        elapsed = time.time() - _extract_start
                        if total_dur > 0:
                            pct = min(99, int(cur_sec / total_dur * 100))
                            if pct != last_pct:
                                _push("status", f"[{pct}%] 重采样中... ({int(elapsed)}秒)")
                                _write_progress(pct)
                                last_pct = pct
                    except Exception:
                        pass
                elif line == "progress=end":
                    break
            proc.wait()
            if proc.returncode != 0:
                _write_progress(0)
                return {"ok": False, "error": "音频重采样失败"}
            _write_progress(100)
            _extract_elapsed = time.time() - _extract_start
            _push("status", f"[100%] 重采样完成 ({_extract_elapsed:.1f}秒)")
            _push("status", "─── 重采样完成 ✓ ───")

        _write_state("model_loading", 15, status_text="[2%] 正在加载 Whisper large-v3 模型...")

        # ---- 加载 Whisper 模型 ----
        global _whisper_model_cache
        _push("status", f"[2%] 正在加载 Whisper {WHISPER_MODEL} 模型...")

        import faster_whisper
        if _whisper_model_cache is None:
            for compute_type in ("float16", "int8"):
                try:
                    _whisper_model_cache = faster_whisper.WhisperModel(
                        WHISPER_MODEL, device="cuda", compute_type=compute_type
                    )
                    _push("status", f"[2%] 模型加载成功（compute_type={compute_type}）")
                    _write_state("transcribing", 5, status_text=f"[5%] 模型加载完成，开始识别...")
                    break
                except Exception as e:
                    if compute_type == "int8":
                        raise
                    _push("status", f"float16 加载失败，尝试 int8: {e}")
        model = _whisper_model_cache

        # 确认 GPU 是否真的可用（nvidia-smi + torch双重验证）
        import torch
        _cuda_ok = torch.cuda.is_available()
        used_mb, total_mb = _get_gpu_memory_nvidia_smi()
        if used_mb > 0 and total_mb > 0:
            _gpu_mem = round(used_mb / 1024, 2)
            _gpu_total = round(total_mb / 1024, 2)
            print(f"[GPU_CHECK] nvidia_smi: {used_mb}MB/{total_mb}MB ({_gpu_mem}GB/{_gpu_total}GB), torch.cuda_is_available={_cuda_ok}, compute_type={compute_type}", flush=True)
        else:
            _gpu_mem = torch.cuda.memory_allocated() / 1024**3 if _cuda_ok else 0
            print(f"[GPU_CHECK] cuda_available={_cuda_ok}, memory_allocated={_gpu_mem:.2f}GB (torch only), compute_type={compute_type}", flush=True)

        _push("status", "[5%] 模型加载完成，开始识别...")

        # ---- 转写 ----
        segments, info = model.transcribe(
            str(wav_path),
            language='zh',
            task='transcribe',
            vad_filter=False,
        )

        total_duration = info.duration
        total_minutes = total_duration / 60
        _transcribe_start = time.time()
        _push("status", f"[5%] 音频总时长 {total_minutes:.1f} 分钟，开始转写...")

        whisper_segments = []
        last_push_time = time.time()
        last_end_time = 0.0
        recent_ends = []

        for s in segments:
            whisper_segments.append({
                'start': s.start,
                'end': s.end,
                'text': s.text.replace('\n', '')
            })
            last_end_time = s.end
            seg_count = len(whisper_segments)
            now = time.time()

            if seg_count % 20 == 0 or (now - last_push_time) > 5:
                pct = min(100, int(last_end_time / total_duration * 100)) if total_duration else 0
                recent_ends.append(last_end_time)
                if len(recent_ends) > 10:
                    recent_ends.pop(0)
                if len(recent_ends) >= 3:
                    span = recent_ends[-1] - recent_ends[0]
                    span_t = now - last_push_time
                    if span > 0 and span_t > 0:
                        audio_per_sec = span / span_t
                        remaining_audio = total_duration - last_end_time
                        eta_sec = remaining_audio / audio_per_sec if audio_per_sec > 0 else 0
                        eta_str = f"约剩{int(eta_sec)}秒"
                    else:
                        eta_str = ""
                else:
                    eta_str = ""

                processed_min = last_end_time / 60
                eta_min = int(eta_sec / 60) if 'eta_sec' in dir() and eta_str else 0
                status_text = f"[{pct}%] 转写中 {processed_min:.1f}/{total_minutes:.1f}分钟"
                if eta_str:
                    status_text += f" ({eta_str})"
                _push("status", status_text)
                _write_progress(5 + pct * 0.9)
                _write_state("transcribing", int(5 + pct * 0.9), status_text=status_text)
                last_push_time = now

        _write_progress(100)
        _total_elapsed = time.time() - _total_start
        _transcribe_elapsed = time.time() - _transcribe_start
        _push("status", (
            f"[100%] 转写完成，{len(whisper_segments)}段，"
            f"总耗时 {_total_elapsed:.1f}秒（转写 {_transcribe_elapsed:.1f}秒，"
            f"语言:{info.language}，置信度:{info.language_probability:.2f}）"
        ))

        # ---- 整理输出 ----
        _push("status", "[100%] 正在保存文字稿...")
        paragraphs = format_as_article(whisper_segments)
        punctuated = [add_punctuation(p) for p in paragraphs]

        clean_title = episode_name.replace('\n', ' ').strip()
        txt_file = output_dir / f"{_sanitize_filename(clean_title)}_文字稿.txt"

        header = (
            f"# {clean_title}\n"
            f"来源: {episode_url}\n"
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"原始段落数: {len(whisper_segments)}，聚合后段落数: {len(paragraphs)}\n"
            + "=" * 60 + "\n\n"
        )
        body = "\n\n".join(punctuated)
        full_content = header + body

        # 路径长度检查（Windows MAX_PATH）
        ok, err = _check_path_length(txt_file)
        if not ok:
            _push("status", "[100%] 路径过长，自动缩短标题重试...")
            short_title = episode_name[:80]
            txt_file = output_dir / f"{_sanitize_filename(short_title)}_文字稿.txt"
            ok2, err2 = _check_path_length(txt_file)
            if not ok2:
                return {"ok": False, "error": f"路径长度问题: {err2}"}

        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(full_content)
        _push("status", f"TXT已保存: {txt_file.name}")

        # ---- 删除临时音频 ----
        if wav_path != audio_path and wav_path.exists():
            try:
                wav_path.unlink()
            except Exception:
                pass

        # ---- 清理进度文件 ----
        try:
            (output_dir / f"_transcribe_progress_{pid}.txt").unlink(missing_ok=True)
        except Exception:
            pass

        _write_state("done", 100, result={"ok": True, "file": str(txt_file), "content": full_content},
                     status_text="[100%] 转写完成，文字稿已保存")
        return {"ok": True, "file": str(txt_file), "content": full_content}

    except Exception as e:
        import traceback
        traceback.print_exc()
        _write_state("failed", error=str(e), status_text=f"[失败] 转写异常: {str(e)[:50]}")
        return {"ok": False, "error": str(e)}


# --------------- 工具函数（复制自 _utils 避免循环导入）---------------

def _sanitize_filename(name: str) -> str:
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, '_')
    name = name.strip(' .')
    if len(name) > 200:
        name = name[:200]
    return name or "untitled"


def _check_path_length(path: Path) -> tuple:
    """检查路径长度，返回 (ok, error_msg)"""
    path_str = str(path)
    if len(path_str) > 220:
        return False, f"路径长度 {len(path_str)} 超过限制"
    return True, ""


# --------------- 独立进程入口 ---------------

if __name__ == "__main__":
    print(f"TRANSCRIBER_BOOT pid={os.getpid()}", flush=True)
    try:
        audio_file = sys.argv[1]
        output_dir = Path(sys.argv[2])
        episode_name = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        episode_url = sys.argv[4] if len(sys.argv) > 4 else ""
        episode_id = int(sys.argv[5]) if len(sys.argv) > 5 else 0

        result = transcribe(audio_file, output_dir, episode_name, episode_url, episode_id)

        result_file = output_dir / f"_transcribe_result_{os.getpid()}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"RESULT:{json.dumps(result, ensure_ascii=False)}", flush=True)

        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR: {e}", flush=True)
        sys.exit(1)
