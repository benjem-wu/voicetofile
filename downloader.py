"""
音频下载模块
VoiceToFile — 小宇宙播客转文字
使用 yt-dlp 下载 m4a 音频
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Optional

# --------------- 配置 ---------------

FFMPEG_PATH = Path(__file__).parent / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin" / "ffmpeg.exe"
CUDA_BIN = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"

# yt-dlp 路径（使用捆绑版本或系统版本）
YT_DLP = "yt-dlp"  # 已在 PATH 或 pip 安装

# --------------- 工具函数 ---------------

def sanitize_filename(name: str) -> str:
    """清理文件名，移除 Windows 非法字符"""
    if not name:
        return "untitled"
    # 移除 Windows 文件名非法字符
    illegal_chars = '<>:"/\\|?*'
    for ch in illegal_chars:
        name = name.replace(ch, '_')
    # 移除前后空格和点
    name = name.strip(' .')
    # 限制长度
    if len(name) > 200:
        name = name[:200]
    return name or "untitled"


# --------------- 下载器类 ---------------

class Downloader:
    """
    使用 yt-dlp 下载单集音频。

    使用方式（独立进程模式）：
        python downloader.py <audio_url> <output_dir> <episode_name> <eid>
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._setup_env()

    def _setup_env(self):
        """设置 PATH 环境变量"""
        ffmpeg_dir = str(FFMPEG_PATH.parent)
        existing_path = os.environ.get("PATH", "")
        env_parts = [ffmpeg_dir, CUDA_BIN] + existing_path.split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(env_parts)

    def download(
        self,
        audio_url: str,
        episode_name: str,
        eid: str,
        progress_callback=None,
    ) -> dict:
        """
        下载音频文件。

        Args:
            audio_url: 音频直链（m4a）
            episode_name: 用于文件名的集名
            eid: 用于进度文件命名
            progress_callback: 回调函数，接收 (bytes_downloaded, total_bytes)

        Returns:
            {"ok": True, "file": "path/to/audio.m4a"}
            或 {"ok": False, "error": "错误信息"}
        """
        pid = os.getpid()
        clean_name = sanitize_filename(episode_name)
        audio_path = self.output_dir / f"{clean_name}.m4a"

        def push_status(text: str):
            print(f"STATUS:{json.dumps({'event': 'download', 'data': text}, ensure_ascii=False)}", flush=True)

        try:
            push_status(f"[1%] 正在连接音频源...")

            # yt-dlp 命令
            cmd = [
                YT_DLP,
                "-f", "bestaudio/best",
                "--audio-format", "m4a",
                "--audio-quality", "0",       # 最高质量
                "-o", str(audio_path),
                "--no-playlist",
                "--no-check-certificate",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "--add-header", "Referer:https://www.xiaoyuzhoufm.com/",
                audio_url,
            ]

            push_status(f"[2%] 开始下载...")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                errors='replace',
            )

            last_pct = 2
            _start = time.time()

            # 读取 yt-dlp 输出（-v info 模式）
            # yt-dlp 在下载中会输出类似：[download]  10.5% of   45.32MiB at   1.23MiB/s ETA 00:35
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                line = line.strip()

                # 解析下载进度
                if line.startswith("[download]"):
                    # 格式: "  10.5% of   45.32MiB at   1.23MiB/s ETA 00:35"
                    parts = line.split()
                    if len(parts) >= 1 and "%" in parts[0]:
                        try:
                            pct_str = parts[0].replace("%", "")
                            pct = float(pct_str)
                            final_pct = min(99, int(pct))
                            elapsed = int(time.time() - _start)
                            if final_pct != last_pct:
                                push_status(f"[{final_pct}%] 下载中... ({elapsed}秒)")
                                # 写进度文件
                                self._write_progress(eid, final_pct)
                                last_pct = final_pct
                        except (ValueError, IndexError):
                            pass

            proc.wait()

            if proc.returncode != 0:
                stderr_text = proc.stderr.read() if proc.stderr else ""
                push_status(f"[错误] 下载失败: {stderr_text[:200]}")
                return {"ok": False, "error": f"yt-dlp 返回码 {proc.returncode}"}

            if not audio_path.exists() or audio_path.stat().st_size < 1024:
                return {"ok": False, "error": "文件未正确下载（文件不存在或太小）"}

            push_status(f"[100%] 下载完成 ({int(time.time()-_start)}秒)")
            self._write_progress(eid, 100)

            return {"ok": True, "file": str(audio_path)}

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def _write_progress(self, eid: str, pct: int):
        """写下载进度文件"""
        try:
            progress_file = self.output_dir / f"_download_progress_{eid}.txt"
            with open(progress_file, 'w', encoding='utf-8') as f:
                f.write(str(int(pct)))
        except Exception:
            pass

    def get_progress(self, eid: str) -> int:
        """读取下载进度（0-100）"""
        try:
            progress_file = self.output_dir / f"_download_progress_{eid}.txt"
            if progress_file.exists():
                return int(progress_file.read_text(encoding='utf-8').strip())
        except Exception:
            pass
        return 0

    def cleanup_progress(self, eid: str):
        """清理进度文件"""
        try:
            (self.output_dir / f"_download_progress_{eid}.txt").unlink(missing_ok=True)
        except Exception:
            pass


# --------------- 独立进程入口 ---------------

if __name__ == "__main__":
    """
    用法: python downloader.py <audio_url> <output_dir> <episode_name> <eid>
    """
    print(f"DOWNLOADER_BOOT pid={os.getpid()}", flush=True)

    if len(sys.argv) < 5:
        print("用法: python downloader.py <audio_url> <output_dir> <episode_name> <eid>")
        sys.exit(1)

    audio_url = sys.argv[1]
    output_dir = Path(sys.argv[2])
    episode_name = sys.argv[3]
    eid = sys.argv[4]

    dl = Downloader(output_dir)
    result = dl.download(audio_url, episode_name, eid)

    # 写结果文件
    result_file = output_dir / f"_download_result_{os.getpid()}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"RESULT:{json.dumps(result, ensure_ascii=False)}", flush=True)
