"""
配置常量
VoiceToFile — 小宇宙播客转文字
所有硬编码常量集中在此，方便修改。
"""
from pathlib import Path

# --------------- 路径 ---------------

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# ffmpeg 路径（与 b-site 共享）
FFMPEG_DIR = PROJECT_ROOT / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin"
FFMPEG_PATH = FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_PATH = FFMPEG_DIR / "ffprobe.exe"

# CUDA
CUDA_BIN = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"

# 缓存目录
HF_HOME = r"C:\Users\wule_\.cache\hf_test"
HF_ENDPOINT = "https://hf-mirror.com"

# --------------- 目录 ---------------

# 默认输出根目录
DEFAULT_OUTPUT_ROOT = Path("F:/outfile")

# 输出根目录（运行时可修改）
OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT

# --------------- 网络 ---------------

# 抓取请求间隔（秒）
COOKIE_INTERVAL = 5

# 每次最多入队集数
MAX_ENQUEUE = 10

# --------------- Flask ---------------

PORT = 18990
SECRET_KEY = None  # 运行时动态生成

# --------------- Worker ---------------

# 转写超时（秒）
TRANSCRIBE_TIMEOUT = 7200  # 2小时

# 下载超时（秒）
DOWNLOAD_TIMEOUT = 1800  # 30分钟

# Worker 轮询间隔（秒）
WORKER_POLL_INTERVAL = 2

# --------------- 单实例保护 ---------------

PID_FILE = PROJECT_ROOT / ".voicetofile.pid"
LOCK_FILE = PROJECT_ROOT / ".voicetofile.lock"

# --------------- 初始化 ---------------

def init_config():
    """运行时初始化（生成随机 secret_key 等）"""
    global SECRET_KEY
    if SECRET_KEY is None:
        import os
        import logging
        SECRET_KEY = os.urandom(24)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(message)s",
            datefmt="%H:%M:%S"
        )
