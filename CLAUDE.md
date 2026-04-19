# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# VoiceToFile 播客转文字工具 — 项目知识快照

> 本文件是 Claude Code 的项目上下文文件，每次对话开始时自动加载。

---

## 1. 项目是什么

一个小宇宙播客订阅转文字的本地工具：
- 用户输入播客链接 → 下载音频 → Whisper 转写 → 保存 TXT → **立即删除音频**
- 纯人工触发，不做定时自动任务
- 支持手动添加单集（模式B，不依赖订阅流程）
- 仓库：`https://github.com/benjem-wu/voicetofile`
- **与 b-site 共用 ffmpeg 捆绑包，共用 Faster-Whisper 模型缓存**

---

## 2. 技术架构

```
用户输入（播客 URL 或 Episode URL）
    │
    ▼
scraper.py（抓取 + 付费检测 + 反爬）
    │
    ▼
downloader.py（yt-dlp 音频下载）
    │
    ▼
transcriber.py（Faster-Whisper large-v3 转写）
    │
    ▼
保存 TXT → 删除音频文件
```

### 关键文件

| 文件 | 作用 |
|------|------|
| `app.py` | Flask 入口：路由注册 + 单实例保护 + 启动 worker |
| `config.py` | 所有硬编码常量（路径/超时/端口/目录） |
| `sse.py` | SSE 广播系统（订阅者管理 + broadcast_sse） |
| `worker.py` | 队列 worker：任务处理 + 转写子进程 + 全局任务状态 |
| `db.py` | SQLite 数据库（podcasts + episodes） |
| `scraper.py` | 小宇宙抓取 + 付费检测 + 反爬 |
| `downloader.py` | yt-dlp 音频下载 |
| `transcriber.py` | Faster-Whisper 转写（子进程运行） |
| `_utils.py` | 共享工具（路径校验、文件名清理、ISO 8601 时长解析） |
| `routes/__init__.py` | 路由注册（Blueprints） |
| `routes/podcasts.py` | 播客相关 API 路由 |
| `routes/episodes.py` | 单集相关 API 路由 |
| `routes/queue.py` | 队列/终止相关 API 路由 |
| `routes/system.py` | SSE + 系统路由 |
| `ffmpeg/` | 捆绑 ffmpeg（与 b-site 共享，**不提交到 Git**） |
| `templates/new_index.html` | 前端页面（原生 JS，Jinja2 模板），含单页保护 `vtf_page_active` |
| `templates/queue.html` | 独立队列页面 |
| `templates/podcast_detail.html` | 独立播客详情页（无单页保护，用于"查看"按钮新窗口打开） |
| `启动.bat` | 双击启动脚本 |
| `requirements.txt` | Python 依赖 |

### 模块架构

```
app.py（~230行）
    │
    ├── config.py          # OUTPUT_ROOT, PORT, COOKIE_INTERVAL, 超时等
    ├── sse.py             # broadcast_sse, addLog, task_update
    ├── worker.py          # _queue_worker, _process_task, _run_transcriber_subprocess
    │                       # 全局状态: _proc_to_kill, _task_terminated, _current_task_info
    └── routes/
        ├── __init__.py    # register_routes(app)
        ├── podcasts.py     # /api/podcast/*
        ├── episodes.py     # /api/episode/*, /api/episodes/*
        ├── queue.py       # /api/queue/*
        └── system.py      # /sse/stream, /api/homepage/status, /api/refresh
```

**修改归属**：
- 转写/队列逻辑 → `worker.py`
- SSE 广播 → `sse.py`
- 常量配置 → `config.py`
- 页面路由（/、/queue、/podcast/<id>） → `app.py`
- API 路由 → `routes/` 下对应文件

### 分支策略

- `master` — 稳定版

---

## 3. 数据获取（重要：已验证）

### URL 清单

| 用途 | URL |
|------|-----|
| 播客主页（获取名称 + 15 集列表） | `https://www.xiaoyuzhoufm.com/podcast/{pid}` |
| 单集 JSON 数据（优先） | `https://www.xiaoyuzhoufm.com/_next/data/-GOav0dS9wDlfSnB05lx2/episode/{eid}.json` |
| 单集 HTML 详情（备选） | `https://www.xiaoyuzhoufm.com/episode/{eid}` |
| 音频文件 | `https://media.xyzcdn.net/{pid}/{hash}.m4a` |

### 列表页解析方式（关键经验）

**`xyzcdn.net` 不能用**——它返回的 JSON-LD 里 `workExample` 有 15 集数据，但**不含 eid**。

正确方式：从 `xiaoyuzhoufm.com/podcast/{pid}` HTML 中提取 **JavaScript 内嵌数据**：
```
"episodes":[{"type":"EPISODE","eid":"69de4c4ab977fb2c47ef785e",
  "pid":"...","title":"...","description":"...",
  "duration":"PT8M38S","pubDate":"..."}]
```
- **eid、title、pubDate** 从 HTML JavaScript 数据提取（regex 逐字段安全提取）
- **description、duration** 从 JSON-LD 的 `workExample` 补充（通过 name 关联）
- 两者缺一不可，JSON-LD 无 eid，JavaScript 数据无完整 description

### 单集音频 URL

从 JSON-LD `associatedMedia.contentUrl` 或 HTML JSON-LD `enclosure.url` 提取。

### 列表页正则提取策略

```python
# 阶段1：从 JavaScript 提取所有 eid / title / pubDate（逐字段，避免对象解析）
eids = re.findall(r'"eid"\s*:\s*"([a-f0-9]{20,})"', chunk)
titles = re.findall(r'"title"\s*:\s*"([^"]*)"', chunk)
pubdates = re.findall(r'"pubDate"\s*:\s*"([^"]*)"', chunk)

# 阶段2：从 JSON-LD 补充 description / duration（通过 name 关联）
```

---

## 4. 付费检测

description 中含以下关键词之一 → 标记为付费集 → **不下载、不进队列**

```
售价、购买、付费、单集.*元、优惠价、小鹅通、已付费、已购买
```

付费集在列表中显示为**不可操作**，标签：`收费`

---

## 5. 反爬策略

| 策略 | 实现 |
|------|------|
| 请求间隔 | 每请求之间等 **≥5 秒**（`COOKIE_INTERVAL` 可配置） |
| 已登录 Cookie | UI 上文本框让用户粘贴，保存到 `.cookie` 文件（不提交 Git） |
| 重试退让 | 失败后等 **10 秒** 再重试，最多 **3 次** |
| 降级方案 | 3 次失败后切换 **Playwright**（需 `pip install playwright && playwright install chromium`） |
| UA / Referer | 随机从 4 个 UA 中选择 + 带上 Referer |

---

## 6. 状态流转

| 状态 | 含义 |
|------|------|
| `pending` | 未转化（从未入队，或移除后恢复为此状态） |
| `queued` | 已入队等待处理（队列中，未开始） |
| `downloading` | 下载进行中 |
| `transcribing` | 转写进行中（子进程） |
| `paused` | 待续转（暂停，音频已保留） |
| `failed` | 失败（自动重试 2 次后仍失败则标记） |
| `done_deleted` | 转写完成，音频已删除 |

> **重要**：`pending` ≠ 排队中。排队中的状态是 `queued`。移除队列时状态恢复为 `pending`。

---

## 7. 数据库（SQLite）

路径：`f:/voicetofile/voicetofile.db`（不提交 Git）

### podcasts 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| pid | TEXT UNIQUE | 播客 ID（小宇宙）；`__manual__` 为虚拟播客"精选播客" |
| name | TEXT | 播客名称（抓取后缓存 DB） |
| added_at | TIMESTAMP | 订阅时间 |

### episodes 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| podcast_id | INTEGER FK | 关联 podcasts.id |
| eid | TEXT | Episode ID（小宇宙唯一，但同标题不同录音会复用标题） |
| name | TEXT | 集名 |
| pub_date | TEXT | 发布时间 |
| duration | TEXT | ISO 8601 时长（PT169M），刷新时从单集详情页补全 |
| is_paid | INTEGER | 是否付费（0/1） |
| status | TEXT | pending/queued/downloading/transcribing/paused/failed/done_deleted |
| txt_path | TEXT | 转写结果路径 |
| error_msg | TEXT | 失败错误信息 |
| audio_path | TEXT | 暂停时存储音频路径 |
| source | TEXT | 'subscribe'（订阅）或 'manual'（手动添加） |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |
| progress | INTEGER | 转写进度（0-100） |
| is_new | INTEGER | 新集标记（0/1） |
| discarded | INTEGER | 是否废弃（0=活跃，1=废弃）；同标题不同录音时保留最长版，短版标记废弃不展示 |

### podcast_details 表

| 字段 | 类型 | 说明 |
|------|------|------|
| podcast_id | INTEGER PK, FK | 关联 podcasts.id，一对一 |
| author | TEXT | 作者/主播名 |
| description | TEXT | 节目简介 |
| cover_url | TEXT | 封面图 URL |
| subscriber_count | INTEGER | 订阅数 |
| episode_count | INTEGER | 总集数（来自列表页） |
| updated_at | TIMESTAMP | 最后同步时间 |

> **入库过滤规则**：episode 满足以下条件之一方可入库
> - 条件A：有 `audio_url`（非空字符串）
> - 条件B：`_is_placeholder()` 返回 False（name >= 7 字且非占位标题）
> - **入库前去重**：同名 episode 保留时长最长者，短版标记 `discarded=1` 不展示

---

## 8. 文件系统

### 输出路径

用户配置的根目录（UI 可配置，默认 `F:\outfile`）：

```
{根目录}/{podcast_name}/{episode_name}_文字稿.txt
```

### 临时文件（处理完后清理）

- `_transcribe_progress_{子进程PID}.txt` — 转写进度（存于 `output_dir`）
- `_download_progress_{eid}.txt` — 下载进度（存于 `output_dir`）
- `_download_result_{pid}.json` — 下载结果
- `audio.wav` — 临时重采样文件
- `{episode_name}.m4a` — 下载的原始音频（转写完成后删除）

### .gitignore 排除项

```
.cookie          # 用户 Cookie
voicetofile.db   # 数据库
ffmpeg/          # ffmpeg 捆绑（与 b-site 共享）
F:/outfile/      # 输出文件
```

---

## 9. Web UI

### 前端架构

- **模板引擎**：Jinja2 + 原生 JavaScript
- **实时更新**：轮询 `/api/queue`（每 3 秒）
- **数据缓存**：客户端 episodeCache 缓存已加载的播客数据
- **无 Vue/React**：纯原生 JS 实现
- **红点持久化**：`episodes.is_new` 字段，展开/查看播客后清除
- **单页保护**：`localStorage` key `vtf_page_active`，多 tab 时自动关闭

### 状态显示（子表/详情页统一）

| 状态 | 显示文案 | 可点击 |
|------|---------|--------|
| `pending` | 未转化 | 可点击入队 |
| `queued` | 排队中 | 可点击 |
| `downloading` | 音频下载中 | 不可点击 |
| `transcribing` | ◌ 音频转文字中（旋转动画） | 不可点击 |
| `paused` | **待续转** | **可点击继续** |
| `failed` | 失败 | 可点击（弹框重试/移除） |
| `done_deleted` | 已转化 | 可点击（打开 TXT） |

### 首页子表/详情页

- 点击"未转化" → 调用 `enqueueEpisode(id)` → POST `/api/episodes/enqueue`
- 点击"失败" → 弹错误详情框 + [重新开始] [移除队列]
- 点击"待续转" → 调用 `resumeEpisode(id)` → POST `/api/episode/resume/<id>`
- 点击"已转化" → GET `/api/episode/open/<id>` 用系统程序打开 TXT

---

## 10. API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页，Jinja2 渲染 |
| `/queue` | GET | 独立队列页面（新窗口） |
| `/podcast/<id>` | GET | 独立播客详情页（新窗口） |
| `/api/podcast/fetch` | POST | 订阅播客（模式A） |
| `/api/episode/add` | POST | 手动添加单集（模式B） |
| `/api/podcast/<id>/episodes` | GET | 获取播客全部剧集，**每次自动同步文件状态** |
| `/api/episodes/enqueue` | POST | 将选中剧集加入队列（body: `{episode_ids: [id]}`） |
| `/api/episodes/refresh` | POST | 刷新播客（同步文件状态 + 抓新集数） |
| `/api/podcast/delete` | POST | 删除播客订阅 |
| `/api/episode/retry/<id>` | POST | 重试失败任务 |
| `/api/episode/open/<id>` | GET | 用系统默认程序打开 TXT 文件（`os.startfile`） |
| `/api/podcast/open/<id>` | GET | 用文件资源管理器打开播客输出文件夹 |
| `/api/podcast/viewed/<id>` | POST | 用户展开播客后清除该播客所有集的 is_new 标记 |
| `/api/podcasts/new-ids` | GET | 返回当前所有有 is_new 标记的 podcast_id 列表 |
| `/api/queue` | GET | 获取当前队列状态 |
| `/api/episode/dequeue` | POST | 将 episode 从队列移除，恢复为 `pending` |
| `/api/queue/stop` | GET/POST | 终止当前任务：杀进程 + 删音频文件 + DB 改 pending |
| `/api/episode/pause/<id>` | POST | 暂停任务，保留音频 |
| `/api/episode/resume/<id>` | POST | 继续暂停的任务 |
| `/api/episode/reset/<id>` | POST | 重置任务，删除音频重新入队 |
| `/api/refresh` | POST | 刷新页面（重启 Flask） |
| `/sse/stream` | GET | SSE 实时推送 |

---

## 11. 进度消息格式

子进程通过 `print(f"STATUS:{json.dumps({...})}", flush=True)` 发消息，launcher 解析 `STATUS:` 前缀。

```
▶ [1%] 正在提取音频... (0秒)
▶ [45%] 正在提取音频... (12秒)
▶ [100%] 音频提取完成 (3.2秒)
▶ [10%] 模型加载完成，开始识别...
▶ [100%] 转写完成，已耗时 266.7秒，总耗时 270.3秒
```

---

## 12. 技术选型

| 组件 | 选型 |
|------|------|
| Web 框架 | Flask + 原生 JS（无框架） |
| 数据库 | SQLite |
| ASR | Faster-Whisper large-v3（CUDA 12.1） |
| 下载器 | yt-dlp |
| 浏览器降级 | Playwright（需单独安装） |
| ffmpeg | 捆绑版（与 b-site 共享） |
| HF 镜像 | `https://hf-mirror.com` |
| HF 缓存 | `C:\Users\wule_\.cache\hf_test` |

---

## 13. 边界情况

| 情况 | 处理 |
|------|------|
| 付费集 | 列表展示，不可操作，不下载 |
| 下载/转写失败 | transcriber.py 内自动重试 2 次，仍失败则 failed |
| GPU OOM | transcriber 降级到 int8，仍失败则 failed |
| 标题过长（MAX_PATH） | 自动截断标题（80字）重试 |
| 音频已是 m4a（播客原生） | transcriber 检测后跳过 ffmpeg 重采样步骤 |
| 重复添加同一集 | 提示已在队列/已完成，不重复添加 |
| 获取到 0 集 | 通常是网络问题或 Cookie 过期 |

---

## 14. 启动与部署

### 方式一：双击启动
```
启动.bat
```

### 方式二：手动启动
```bash
python app.py
# 访问 http://127.0.0.1:18990
```

### 依赖安装
```bash
pip install -r requirements.txt
# Playwright 需单独安装浏览器
python -m playwright install chromium
```

---

## 15. 与 b-site 的关系

- **共享 ffmpeg**：捆绑在 `f:/voicetofile/ffmpeg/`（从 b-site 复制，不提交 Git）
- **共享 Faster-Whisper**：相同配置，`large-v3`，CUDA 12.1
- **独立数据库**：`voicetofile.db`，与 b-site 隔离
- **独立端口**：18990（与 b-site 的 18989 不同）

---

## 16. Git 仓库

- 本地已初始化：`git init`
- 远程：`https://github.com/benjem-wu/voicetofile`

---

## 17. 已知问题与修复记录（按时间）

| 日期 | 问题 | 修复 |
|------|------|------|
| 2026-04-15 | 模板 `{{ now() }}` 报错 | `render_template` 加 `now=datetime.now` |
| 2026-04-15 | 启动.bat 双击闪退 | 改用硬编码完整路径 |
| 2026-04-15 | 启动.bat 中文乱码 | 批处理文件编码改为 GBK |
| 2026-04-15 | 订阅后占位集存入 DB | 新增 `_is_placeholder()` 过滤 |
| 2026-04-15 | 手动添加单集和订阅播客混在一起 | 改用虚拟播客"精选播客"（pid=`__manual__`） |
| 2026-04-15 | 订阅时无法判断音频是否真实存在 | 并行验证音频 URL，过滤无音频的占位集 |
| 2026-04-15 | 手动添加后自动开始下载/转写 | 手动添加只存 `pending`，需主动点击入队 |
| 2026-04-15 | 失败任务无法重试 | 支持手动重试，自动重试 2 次后标记 failed |
| 2026-04-16 | 多 Flask 实例同时运行，两个 worker 抢同一任务 | `msvcrt.locking` 文件锁 + PID 文件验证 |
| 2026-04-16 | 残留任务（downloading/transcribing）重启后无法恢复 | `cleanup_stale_tasks()` 启动时 `UPDATE status='pending'`（不再 DELETE） |
| 2026-04-17 | SSE 广播在 DB 更新之前，前端收到后 DB 仍是旧状态 | `broadcast_sse` 移到 `db.update_episode_status()` 之后 |
| 2026-04-17 | 终止任务后"正在处理"池不更新 | `task_stopped` 事件立即从 `_cachedTasks` 移除任务；`loadQueue()` 直接用 API 数据替换缓存 |
| 2026-04-17 | 终止任务后 progress 文件未删除 | `terminate_current_task()` 从正确的 `output_dir` 用 glob 删除；之前从 CWD 删且文件名用 `podcast_name`（实际是子进程 PID） |
| 2026-04-17 | `/podcast/<id>` 500 错误 | 缺少 `new_podcast_ids` 模板变量，添加 `new_podcast_ids=[]` |
| 2026-04-17 | 点击"查看"按钮闪一下又关掉 | `new_index.html` 单页保护 `vtf_page_active` 使新 tab 自杀；新建 `podcast_detail.html` 无此保护 |
| 2026-04-16 | 进程被强制 kill 后锁文件残留 | PID 文件里存 PID + `OpenProcess` 验证 |
| 2026-04-16 | `get_next_queued_task()` 未返回 `podcast_name` | RETURNING 后 JOIN podcasts 表补充 |
| 2026-04-16 | 转写完成后 `status=done_deleted` 但 `txt_path` 为空 | `mark_task_done(id, txt_path)` 增加路径参数 |
| 2026-04-17 | 小红点用 localStorage，刷新后丢失 | 改用 DB `is_new` 字段持久化 |
| 2026-04-17 | 点击播客名称经常误跳详情页 | 播客名称去掉链接，右侧已有"查看"按钮 |
| 2026-04-17 | 多 tab 打开导致状态混乱 | 加单页保护 `vtf_page_active`；后用独立 `podcast_detail.html` 解决"查看"按钮新窗口被自杀的问题 |
| 2026-04-17 | 转写子进程在 Windows 上管道读取卡住 | 后台线程 + `queue.Queue` 持续读取 stdout |
| 2026-04-17 | app.py 臃肿（1289 行），改动互相干扰 | 重构为模块化结构：`config.py`/`sse.py`/`worker.py`/`routes/` |
| 2026-04-17 | 点击"未转化"提示"加入队列失败，未选择任何集" | 前端发送字段名从 `{eids}` 改为 `{episode_ids}`，与后端一致 |
| 2026-04-17 | 终止按钮设计过于复杂（ffprobe 验证 + 弹框选择） | 简化为：直接杀进程 + 删音频文件 + 改 pending，worker finally 不写 DB |
| 2026-04-18 | 播客列表页同标题出现多条（Vol.261、Vol.260 等重复） | 小宇宙同一标题发布多个录音（不同 duration，如 28min vs 87min），按 duration 去重：保留最长版，短版标记 `discarded=1` 不展示 |
| 2026-04-18 | 刷新播客时短版重复入库 | 新增入库前去重逻辑：`api_refresh_episodes` 入库前比对同名 episode，时长更短者标记 discarded 后跳过入库 |
| 2026-04-18 | 终止任务无响应（根因：0311d85引入） | `_proc_to_kill` 被 download 覆盖导致杀错进程；`_start_task_thread` 重复定义导致 `_current_worker_thread` 始终为 None；改为 `_download_proc`/`_transcribe_proc` 分开存储 + `kill_active_subprocess()` 统一杀进程 |
| 2026-04-18 | 下载无超时保护 | `DOWNLOAD_TIMEOUT=1800` 在代码中定义但从未接入；接入 downloader 并在无输出时检查超时 |
| 2026-04-18 | 转写进度卡住（0%→10%→0% 循环） | transcriber.py 缺 `import subprocess` 导致所有转写立即失败；`subprocess.run/Popen` 调用处无 import；新增状态文件架构 `_transcribe_state_{episode_id}.json` 作为权威进度来源，worker.py 每秒轮询，替代脆弱的 stdout 解析 |
| 2026-04-18 | 科技早知道 E05 被误判为付费内容 | `is_paid_episode` 正则过于宽泛，"付费内容"等描述性文字触发误判；改为有具体价格或明确购买词才标付费 |
| 2026-04-18 | 转写完成后 `status=downloading` 卡住，音频/音频文件未删除 | `queue.Queue(maxsize=100)` 有界队列，STATUS 消息填满队列导致 drainer 阻塞死锁，RESULT 丢失；改为无界队列 `maxsize=0`；`proc.poll()` 退出后先排空队列再 `proc.wait()` 确保 RESULT 不丢失 |
| 2026-04-18 | 终止任务时 stop API 永久阻塞 | `wait_for_worker_exit()` 无 timeout，网络卡顿时 API 永久阻塞；改为 daemon thread + 5s timeout |
| 2026-04-18 | 付费检测误判导致任务无限重试 | `episodes` 表无 `retry_count` 列导致重试次数无法持久化；增加 `retry_count` 列 + `increment_retry_count()`，max 2 次重试后标记 failed |
| 2026-04-18 | fetch_episode_info 无总超时保护 | 网络卡顿时任务永远卡住；增加 90 秒线程超时包装 |
| 2026-04-18 | TeeWriter 写日志时 UnicodeEncodeError | Windows 终端 GBK 编码无法编码 ¥ 等字符；捕获 `UnicodeEncodeError` 并降级写入 |
| 2026-04-18 | 数据库含 test_ep_X/Y 脏数据导致 worker 崩溃 | eid 无效（`test_` 前缀或长度<10）；增加 eid 格式校验，无效则直接标记 failed |
| 2026-04-19 | 转写完成后 UI 卡在 XX% 但 TXT 已生成 | **三处 `proc.wait()` 阻塞**：① `_run_transcriber_subprocess` while 循环 break 前未最后一次轮询状态文件（导致 100% 进度丢失）；② `_process_task` finally 块直接调用 `proc.wait()` 导致僵尸进程挂起；③ `kill_active_subprocess()` 同理；**SSE 广播阻塞**：`broadcast_sse` 的 `sub.put()` 在队列满时无限阻塞，慢消费者卡死整个 worker 线程 |
| 2026-04-19 | 进度文字显示混乱（音频下载中 / 正在转文字多少 交替出现） | 转写状态文件 `status_text` 值如 `"[45%] 转写中 12.5/30.0分钟"` 前缀 `[N%]` 与进度条百分比重复显示；前端 ticker 过滤掉 `status_text` 中的 `[N%]` 模式，前端显示：百分比（进度条）+ 阶段描述（如"转写中 12.5/30.0分钟"） |

---

## 18. 队列架构 v2

> **原则**：取消内存队列，所有任务状态统一存在 `episodes` 表。Flask 重启不丢状态。

### 状态流转

```
Flask 启动
    │
    ▼
cleanup_stale_tasks()          ← downloading/transcribing → pending（DB 记录保留）
    │
    ▼
Worker 启动（while True）
    │
    ▼
get_next_queued_task()      ← queued → downloading（原子 SQL）
    │
    ▼
_do_download(timeout=30min)
    │
    ▼
_do_transcribe(timeout=2hr)  ← 实时 update_task_progress()
    │
    ▼
mark_task_done()             ← done_deleted, progress=100
    │
    ▼
loop → get_next_queued_task()
```

### 终止流程（downloading/transcribing 时点击"终止任务"）

```
点终止 → POST /api/queue/stop
                │
                ▼
        worker.terminate_current_task()
                │
                ├─ _task_terminated = True
                ├─ proc.kill(); proc.wait()
                ├─ 删音频文件（_current_audio_file 精准值，无竞态）
                ├─ 从 output_dir glob 删除 _download_progress_*.txt / _transcribe_progress_*.txt
                └─ return episode_id
                │
                ▼
        db.update_episode_status(episode_id, 'pending')
                │
                ▼
        broadcast_sse('task_stopped', {episode_id})
                │
                ▼
        前端立即从缓存移除任务 + loadQueue() 同步，状态变"未转化"
```

- 不验证音频完整性，直接删
- 不弹框，直接执行
- DB 记录不删除，只改状态为 pending
- worker finally 块检测到 `_task_terminated=True` 时不写 DB（由 stop API 统一处理）
- **时序关键**：`db.update_episode_status` 必须在 `broadcast_sse` 之前，否则前端 SSE 触发时 DB 还是旧状态

### 暂停后继续（paused 时点击"待续转"）

```
├─ audio_path 存在且完整 → 状态改 transcribing，跳过下载，直接转写
└─ audio_path 不存在或不完整 → 删路径，状态改 downloading，重新下载
```

### db.py 关键函数

| 函数 | 作用 |
|------|------|
| `get_next_queued_task()` | 原子抢任务：`queued → downloading`，RETURNING + JOIN |
| `enqueue_task(id)` | `pending/failed → queued` |
| `update_task_progress(id, progress)` | 更新转写进度（0-100） |
| `cleanup_stale_tasks()` | 启动时删除 downloading/transcribing 残留任务 |
| `mark_task_done(id, txt_path)` | `→ done_deleted`，progress=100 |
| `mark_task_failed(id, error_msg)` | `→ failed`，progress=0 |
| `mark_podcast_viewed(podcast_id)` | 清除该播客所有集的 is_new 标记 |
| `get_podcasts_with_new()` | 返回当前有 is_new 标记的所有 podcast_id |
| `get_episode_by_name(podcast_id, name)` | 按播客 ID + 集名查找活跃 episode（用于去重比对） |
| `mark_episode_discarded(episode_id)` | 将 episode 标记为 `discarded=1`（同名去重时保留最长版） |
| `_parse_duration_to_minutes(duration)` | 从 ISO 8601 时长字符串（PT28M）提取分钟数，用于时长比对 |

### worker.py 全局状态（进程级别）

```python
_download_proc      # 当前 download subprocess（用于 kill）
_transcribe_proc    # 当前 transcribe subprocess（用于 kill）
_task_terminated    # 标记任务被提前终止
_current_task_info  # 当前任务信息（供 api_queue_stop 获取 episode_id）
_current_audio_file # 当前音频文件路径（供终止时验证）
_current_output_dir # 当前输出目录（供终止时清理 progress 文件）
```

对外：`get_current_task_info()`、`get_current_audio_file()`、`set_task_terminated()`、`is_task_terminated()`、`terminate_current_task()`

### 转写状态文件架构

transcriber.py 子进程写入 `_transcribe_state_{episode_id}.json`（权威来源），worker.py 每秒轮询更新 DB + SSE。

```text
transcriber.py（子进程）
    │
    ├── 写 _transcribe_state_{episode_id}.json（原子写入，先写 .tmp 再 rename）
    │       字段：status / progress / status_text / result / error / updated_at
    │
    └── 打印 RESULT:（兜底，进程异常退出时读）

worker.py（主进程）
    │
    ├── _poll_transcribe_state() 每秒轮询状态文件
    │       → db.update_task_progress()
    │       → task_update() → SSE 推前端
    │
    └── 进程退出时：状态文件 result（第一优先）
                  → stdout RESULT:（兜底）
```

状态文件路径：`{output_dir}/_transcribe_state_{episode_id}.json`

### 与旧架构对比

| 对比项 | 旧架构（v1） | 新架构（v2） |
|--------|------------|------------|
| 任务队列 | `task_queue` 内存 + DB 双份 | DB 唯一 |
| Flask 重启 | `task_queue` 丢失 | 状态保留在 DB |
| 残留任务 | 需手动清理 | `cleanup_stale_tasks()` 启动时自动重置为 pending |
| 子进程卡死 | 永久阻塞 | timeout 后 kill |
| 停止任务 | 只能请求，无法强制 | `proc.kill()` 强制 |

---

## 19. 模块化重构（2026-04-17）

> app.py 从 1289 行拆分为多个独立模块。改动不再互相干扰。

### 拆分后的文件

| 文件 | 职责 |
|------|------|
| `app.py` ~230行 | Flask 入口、页面路由、单实例保护、启动 worker |
| `config.py` | 所有硬编码常量（路径/超时/端口/目录） |
| `sse.py` | SSE 订阅者管理、`broadcast_sse`、`addLog`、`task_update` |
| `worker.py` | `_queue_worker`、`_process_task`、全局任务状态 |
| `routes/__init__.py` | `register_routes(app)` |
| `routes/podcasts.py` | `/api/podcast/*` |
| `routes/episodes.py` | `/api/episode/*`、`/api/episodes/*` |
| `routes/queue.py` | `/api/queue`、`/api/queue/stop` |
| `routes/system.py` | `/sse/stream`、`/api/homepage/status`、`/api/refresh` |
| `templates/new_index.html` | 首页（单页保护） |
| `templates/queue.html` | 独立队列页面 |
| `templates/podcast_detail.html` | 独立播客详情页（无单页保护） |

### routes 模块调用关系

```
routes/podcasts.py  → db.*, scraper.*, worker.get_output_dir()
routes/episodes.py  → db.*, scraper.*, worker.*, sse.*
routes/queue.py     → worker.*, sse.broadcast_sse
routes/system.py    → db.*, config.*, sse.sse_subscribers/sse_lock
```

无循环依赖。

### 独立详情页解决方案（"查看"按钮）

`new_index.html` 的单页保护 `vtf_page_active` 导致：点击"查看"→新窗口打开 `podcast_detail` tab→检测到旧 tab 的 localStorage→调用 `window.close()` 自杀。

**解决方案**：新建 `templates/podcast_detail.html`，内容从 `new_index.html` 的详情部分提取，**不包含单页保护 JS**。`app.py` 的 `/podcast/<id>` 路由渲染此独立模板，10 个播客共用 1 个模板文件（通过 URL 参数 `podcast.id` 区分）。

---

## 20. 待实现功能

### 20.1 播客详情表（podcast_details）

> 方案：新建 `podcast_details` 表，podcasts 表保持不变。

**背景**：列表页可抓取更多元数据（作者、订阅数、节目简介等），但当前只存了 name。这些数据适合独立存储，不污染 podcasts 表。

### podcast_details 表（新增）

| 字段 | 类型 | 说明 |
|------|------|------|
| podcast_id | INTEGER PK, FK | 关联 podcasts.id，一对一 |
| author | TEXT | 作者/主播名 |
| description | TEXT | 节目简介 |
| cover_url | TEXT | 封面图 URL |
| subscriber_count | INTEGER | 订阅数 |
| episode_count | INTEGER | 总集数 |
| play_count | INTEGER | 播放量（若有） |
| updated_at | TIMESTAMP | 最后同步时间 |

**关联关系**：`podcasts` : `podcast_details` = 1 : 1，`podcast_id` 作为主键兼外键。

**抓取来源**：从播客列表页 HTML 的 JSON-LD 或 `<script>` 标签提取。

**UI 用途**：
- 播客卡片显示作者名、订阅数
- 详情页显示节目简介、封面图

### 20.2 刷新时 audio_url 过滤

✅ 已实现（见入库过滤规则）

### 20.3 同名 episode 去重（保留最长版）

> **问题**：小宇宙同一播客下多个录音共用同一标题（如"Vol.261"出现多次，时长 28min vs 87min），导致列表页显示重复。

**去重规则**：
- 按 `podcast_id + name` 分组，保留 `duration` 最长的那条
- 短版标记 `discarded=1`，不展示在列表中
- 后续刷新时，新 episode 若比已有同名 episode 时长短，则跳过入库

**实现位置**：`api_refresh_episodes()` 中的入库前去重逻辑 + `mark_episode_discarded()`

**DB 字段**：`episodes.discarded`（0=活跃，1=废弃）

---

## 21. 避免级联改动的经验总结

### 核心原则

| 原则 | 说明 | 例子 |
|------|------|------|
| **单一数据源** | 同一份数据只在一个地方定义/存储 | queue v2: 任务状态只在 DB，不在内存 |
| **明确边界** | 模块之间通过接口交互，不直接读写内部状态 | routes 只调 db.*，不直接操作 SQLite 连接 |
| **先想后改** | 改一个地方时，先列出所有"下游消费者"再动手 | 改 `episodes.status` 枚举值 → 检查 worker.py, routes/, 前端 JS |
| **向后兼容** | DB 字段只增不改类型，只删不用字段 | `episodes.audio_path` 废弃但保留列 |
| **独立部署** | 每个功能可独立测试 | scraper.py 单独跑，不依赖 Flask |

### 级联改动高发场景

| 场景 | 风险 | 防御 |
|------|------|------|
| 新增 DB 字段 | 旧代码不读/报错 | 先加列（DEFAULT NULL），再改读写逻辑 |
| 新增 API 参数 | 旧前端不传 | 设默认值，前端渐进升级 |
| 修改状态流转 | worker/前端/DB 三处不一致 | 先画状态图，再改代码 |
| 重构共享函数 | 多个调用方行为不同 | 先抽象接口，逐一迁移调用方 |
| 前端模板拆出子模板 | 变量传递遗漏 | 新模板独立渲染，先用 iframe 测试 |

### 具体做法

1. **改前先隔离测试**：改 `db.py` 之前，先用 `python -c "from db import *; ..."` 单独验证 SQL
2. **改后立即验证**：每改一个文件，立即跑一遍相关功能（不要攒很多改一起测）
3. **用 git diff --stat 看影响面**：`git diff` 列出改动文件，评估是否有多处改动应该分开提交
4. **注释"合约"而非实现**：函数 docstring 写清楚"输入什么、输出什么、副作用什么"，不写"怎么做的"
5. **CI 思维**：如果条件允许，写最小化的 smoke test（比如访问 `/api/queue` 不报错）

---

*最后更新：2026-04-18*
