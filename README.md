# VoiceToFile

小宇宙播客订阅转文字工具。本地 Flask Web UI，输入播客链接，自动下载音频 → Whisper 转写 → 保存 TXT → 删除音频。

## 功能

- **模式 A**：输入播客链接，一键获取最近 15 集，勾选下载
- **模式 B**：手动输入单集链接，直接下载转写
- 付费集自动识别并跳过（仅展示，不下载）
- 每集转写完成后自动删除音频，只保留文字稿
- 实时进度显示（下载 % + 转写 % + 已用时间）
- 支持重新处理已完成的集

## 环境要求

- Windows（与 b-site 共享 ffmpeg，无需单独安装）
- Python 3.10+
- NVIDIA GPU + CUDA 12.1
- 已安装 [FFmpeg](https://ffmpeg.org/)（项目已捆绑）

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/benjem-wu/voicetofile.git
cd voicetofile

# 安装依赖
pip install -r requirements.txt

# 启动（双击 启动.bat 或手动运行）
python app.py
```

然后打开浏览器访问 http://127.0.0.1:18990

## 项目结构

```
voicetofile/
├── app.py              # Flask 主程序
├── db.py               # SQLite 数据库
├── scraper.py          # 小宇宙抓取 + 付费检测 + 反爬
├── downloader.py       # yt-dlp 音频下载
├── transcriber.py      # Faster-Whisper 转写
├── _utils.py           # 共享工具
├── templates/
│   └── index.html      # 前端页面
└── 启动.bat             # 双击启动
```

## 技术栈

Flask · SQLite · Faster-Whisper (large-v3, CUDA) · yt-dlp · Playwright

## LICENSE

MIT
