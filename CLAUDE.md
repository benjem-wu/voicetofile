# VoiceToFile 播客转文字工具 — 项目知识快照

> 本文件是 Claude Code 的项目上下文文件，每次对话开始时自动加载。

---

## 1. 项目是什么

小宇宙播客订阅转文字本地工具：
- 输入播客链接 → 下载音频 → Whisper转写 → 保存TXT → **立即删除音频**
- 纯人工触发，不做定时任务；支持手动添加单集
- 仓库：`https://github.com/benjem-wu/voicetofile`
- **与 b-site 共用 ffmpeg 捆绑包和 Faster-Whisper 模型缓存**

---

## 2. 系统架构

```
用户浏览器 (http://127.0.0.1:18990)
    │
    ▼
Flask Web Server
├── SSE广播 (sse.py) · API路由 (routes/) · 页面路由 (app.py)
└── Worker (worker.py) ─ 抢任务→下载→转写
    ├── scraper.py ─ 抓取+付费检测+反爬
    ├── downloader.py ─ yt-dlp下载
    └── transcriber.py ─ Faster-Whisper转写(子进程)
            │
            ▼
    小宇宙 API (xiaoyuzhoufm.com)

SQLite DB ─ podcasts表─1:N─ episodes表
文件系统 ─ F:/outfile/{podcast}/{episode}_文字稿.txt
外部依赖 ─ ffmpeg/ (与b-site共享) · HF模型缓存 (CUDA 12.1)
```

**数据流向**：用户URL → scraper抓取 → 入DB队列(pending) → worker抢任务(downloading→transcribing) → done_deleted → 删音频，只留TXT

**关键文件**：app.py · config.py · sse.py · worker.py · db.py · scraper.py · downloader.py · transcriber.py · routes/ · templates/

**修改归属**：转写/队列→worker.py · SSE广播→sse.py · 常量→config.py · 页面路由→app.py · API路由→routes/

---

## 3. 数据获取

@art:docs/DATA_FETCH.md

---

## 4. 付费检测

关键词 → 标记付费集 → **不下载、不进队列**

```
售价、购买、付费、单集.*元、优惠价、小鹅通、已付费、已购买
```

---

## 5. 反爬策略

| 策略 | 实现 |
|------|------|
| 请求间隔 | ≥5 秒（`COOKIE_INTERVAL` 可配置） |
| Cookie | UI 输入，保存到 `.cookie` 文件 |
| 重试退让 | 失败等 10 秒，最多 3 次 |
| 降级方案 | 3 次失败后切换 Playwright |

---

## 6. 状态流转

| 状态 | 含义 |
|------|------|
| `pending` | 未入队 |
| `queued` | 已入队等待处理 |
| `downloading` | 下载进行中 |
| `transcribing` | 转写进行中（子进程） |
| `paused` | 待续转（暂停，音频已保留） |
| `failed` | 失败（自动重试2次后标记） |
| `done_deleted` | 转写完成，音频已删除 |

@art:docs/QUEUE_ARCH.md

---

## 7. 数据库

路径：`f:/voicetofile/voicetofile.db`（不提交 Git）

@art:docs/DATABASE.md

---

## 8. 文件系统

**输出路径**：`{根目录}/{podcast_name}/{episode_name}_文字稿.txt`（默认 `F:\outfile`）

**临时文件（处理后清理）**：`_transcribe_progress_{PID}.txt` · `_download_progress_{eid}.txt` · `{episode_name}.m4a`

**.gitignore**：.cookie · voicetofile.db · ffmpeg/ · F:/outfile/

---

## 9. Web UI

- **模板引擎**：Jinja2 + 原生 JavaScript
- **实时更新**：轮询 `/api/queue`（每 3 秒）
- **红点持久化**：`episodes.is_new` 字段
- **单页保护**：`localStorage` key `vtf_page_active`

**状态显示**：pending→未转化(可入队) · queued→排队中 · downloading→音频下载中 · transcribing→◌音频转文字中 · paused→待续转(可继续) · failed→失败(可重试) · done_deleted→已转化(可打开)

@art:docs/WEB_UI.md

---

## 10. API 端点

@art:docs/API.md

---

## 11. 技术选型

| 组件 | 选型 |
|------|------|
| Web 框架 | Flask + 原生 JS |
| 数据库 | SQLite |
| ASR | Faster-Whisper large-v3（CUDA 12.1） |
| 下载器 | yt-dlp |
| 浏览器降级 | Playwright |
| ffmpeg | 捆绑版（与 b-site 共享） |

---

## 12. 边界情况

| 情况 | 处理 |
|------|------|
| 付费集 | 列表展示，不可操作 |
| 下载/转写失败 | 自动重试 2 次，仍失败则 failed |
| GPU OOM | 降级到 int8，仍失败则 failed |
| 标题过长 | 自动截断（80字）重试 |
| 音频已是 m4a | 跳过 ffmpeg 重采样 |
| 重复添加 | 提示已在队列/已完成 |

---

## 13. 启动与部署

```bash
# 双击启动
启动.bat

# 手动启动
python app.py
# 访问 http://127.0.0.1:18990

# 依赖
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 14. 与 b-site 的关系

- **共享 ffmpeg**：`f:/voicetofile/ffmpeg/`（不提交 Git）
- **共享 Faster-Whisper**：large-v3，CUDA 12.1
- **独立数据库**：`voicetofile.db`
- **独立端口**：18990（b-site 用 18989）

---

## 15. 队列架构 v2

@art:docs/QUEUE_ARCH.md

---

## 16. 模块化重构

@art:docs/REFACTOR.md

---

## 17. 待实现功能

@art:docs/ROADMAP.md

---

*最后更新：2026-04-21*
