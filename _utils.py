"""
共享工具模块
VoiceToFile — 小宇宙播客转文字
"""
import re
import os
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """清理文件名，移除 Windows 非法字符"""
    if not name:
        return "untitled"
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, '_')
    name = name.strip(' .')
    if len(name) > 200:
        name = name[:200]
    return name or "untitled"


def check_path_length(path: Path) -> tuple:
    """
    检查路径长度，返回 (ok, error_msg)
    Windows MAX_PATH = 260（部分模式开启后 32767）
    留安全余量，220 以上视为危险
    """
    path_str = str(Path(path).resolve())
    if len(path_str) > 220:
        return False, f"路径过长（{len(path_str)} 字符），请缩短标题或输出路径"
    return True, ""


def parse_duration_minutes(iso_duration: str) -> int:
    """将 ISO 8601 PT169M 转为分钟数"""
    if not iso_duration:
        return 0
    m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration, re.IGNORECASE)
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) + (1 if int(m.group(3) or 0) > 0 else 0)


def format_duration(iso_duration: str) -> str:
    """将 ISO 8601 PT169M 转为可读字符串"""
    mins = parse_duration_minutes(iso_duration)
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    return f"{mins}min"


def clean_temp_files(output_dir: Path, pid: int):
    """清理指定进程的临时文件"""
    patterns = [
        f"_audio_progress_{pid}.txt",
        f"_audio_result_{pid}.json",
        f"_download_progress_*.txt",
        f"_download_result_{pid}.json",
        f"_transcribe_progress_{pid}.txt",
        f"_transcribe_result_{pid}.json",
        "audio.wav",
    ]
    for p in patterns:
        if '*' in p:
            for fp in output_dir.glob(p):
                try:
                    fp.unlink()
                except Exception:
                    pass
        else:
            fp = output_dir / p
            if fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
