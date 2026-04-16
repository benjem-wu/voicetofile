# VoiceToFile 播客转文字工具 — 项目知识快照

> 本文件是 Claude Code 的项目上下文文件，每次对话开始时自动加载。

---

## 1. 项目是什么

一个小宇宙播客订阅转文字的本地工具：
- 用户输入播客链接 → 下载音频 → Whisper 转写 → 保存 TXT → **立即删除音频**
- 纯人工触发，不做定时自动任务
- 支持手动添加单集（模式B，不依赖订阅流程）
- 仓库：`https://github.com/benjem-wu/voicetofile`
- **与 b-site 共用 ffmpeg 捆绑包，共用 SenseVoice 模型缓存**

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
| `app.py` | Flask Web 主程序 |
| `db.py` | SQLite 数据库（podcasts + episodes） |
| `scraper.py` | 小宇宙抓取 + 付费检测 + 反爬 |
| `downloader.py` | yt-dlp 音频下载 |
| `transcriber.py` | Faster-Whisper 转写（子进程运行） |
| `_utils.py` | 共享工具（路径校验、文件名清理、ISO 8601 时长解析） |
| `ffmpeg/` | 捆绑 ffmpeg（与 b-site 共享，**不提交到 Git**） |
| `templates/new_index.html` | 前端页面（原生 JS，Jinja2 模板） |
| `启动.bat` | 双击启动脚本 |
| `requirements.txt` | Python 依赖 |

### 分支策略

- `main` — 稳定版

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
| `done_deleted` | 转写完成，音频已删除 |
| `failed` | 失败（自动重试 2 次后仍失败则标记） |

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
| eid | TEXT | Episode ID |
| name | TEXT | 集名 |
| pub_date | TEXT | 发布时间 |
| duration | TEXT | ISO 8601 时长（PT169M） |
| is_paid | INTEGER | 是否付费（0/1） |
| status | TEXT | pending/downloading/transcribing/done_deleted/failed |
| txt_path | TEXT | 转写结果路径 |
| error_msg | TEXT | 失败错误信息 |
| source | TEXT | 'subscribe'（订阅）或 'manual'（手动添加） |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

---

## 8. 文件系统

### 输出路径

用户配置的根目录（UI 可配置，默认 `F:\outfile`）：

```
{根目录}/{podcast_name}/{episode_name}_文字稿.txt
```

### 临时文件（处理完后清理）

- `_transcribe_progress_{pid}.txt` — 转写进度
- `_download_progress_{eid}.txt` — 下载进度
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

## 9. Web UI（已重构）

### 前端架构

- **模板引擎**：Jinja2 + 原生 JavaScript
- **实时更新**：轮询 `/api/queue`（每 3 秒）
- **数据缓存**：客户端 episodeCache 缓存已加载的播客数据
- **无 Vue/React**：纯原生 JS 实现
- **红点持久化**：localStorage key `vtf_viewed_podcasts`，展开/查看播客时标记已读
- **日期显示**：主页表格最新发布时间显示**月日**（JS 格式化）

### 主页结构

```
┌─ 顶栏 ─────────────────────────────────────────────────┐
│ VoiceToFile              [📋 队列]  [⚙ 设置]            │
└─────────────────────────────────────────────────────────┘
┌─ 走马灯（点击新窗口打开 /queue）─────────────────────────┐
│ ● 随机波动—第3集[45%]  ▮ 半拿铁—第1集[处理中]          │
└─────────────────────────────────────────────────────────┘
┌─ 主表格（精选播客置顶，其余按订阅时间倒序）──────────────────────┐
│ 📻 已订阅播客       [🔄 全部刷新] [📡 订阅] [➕ 手动添加]      │
│ ┌──┬────────┬────┬─────┬──────────┬───────────────┐    │
│ │☑ │[名]链接│ 5  │ 2   │04-15     │[查看][刷新][删除]│    │
│ │☑ │随机波动│ 50 │ 35  │04-15     │[查看][刷新][删除]│    │
│ │☑ │半拿铁  │ 30 │ 20  │04-12     │[查看][刷新][删除]│    │
│ └──┴────────┴────┴─────┴──────────┴───────────────┘    │
│  ▼ 查看全部 50 集（已转 35 集）← 点击新窗口打开 /podcast/<id> │
└─────────────────────────────────────────────────────────┘
```

### 点击行为

- **播客名称** → `window.open('https://www.xiaoyuzhoufm.com/podcast/{pid}', '_blank')` 打开小宇宙原页面
- **主表"查看"按钮** → `window.open('/podcast/<id>', '_blank')` 新标签页打开播客详情页
- **主表"刷新"按钮** → 调用 `/api/episodes/refresh` 刷新该播客（含同步文件状态+抓新集数）
- **"查看全部 X 集（已转 Y 集）"** → `window.open('/podcast/<id>', '_blank')` 新标签页打开详情页
- **已转化状态** → 点击调用 `GET /api/episode/open/<id>` 用系统默认程序打开 TXT

### 展开子表（点击▶展开，已废弃导航功能）

展开内容仅显示"查看全部"链接，不含刷新按钮和子表数据。

列：标题 / 播客发布时间(170px) / 音频长度 / 是否已转文字 / 是否免费

状态显示（子表/详情页统一）：
- `pending`（无队列任务）→ **未转化**（点击加入队列）
- `pending`（队列有任务）→ **排队中**
- `downloading` / `transcribing` → **◌ 处理中**（旋转动画）
- `done_deleted`（文件存在）→ **已转化**（点击用系统程序打开 TXT）
- `failed` → **失败**

### 独立详情页 `/podcast/<id>`

新窗口打开，分页列表（每页20条），顶部统计已转化/未转化/付费数量，含"🔄 刷新"和"📥 全部下载"按钮。

### 独立队列页 `/queue`

新窗口打开，3秒轮询刷新，显示当前处理中/排队中/已完成任务。队列历史从DB查询，重启后保留最近20条。

### 对话框

- **订阅播客**：输入播客链接或 PID
- **手动添加**：输入单集链接

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
| `/api/episodes/enqueue` | POST | 将选中剧集加入队列 |
| `/api/episodes/refresh` | POST | 刷新播客（同步文件状态 + 抓新集数） |
| `/api/podcast/delete` | POST | 删除播客订阅 |
| `/api/episode/retry/<id>` | POST | 重试失败任务 |
| `/api/episode/open/<id>` | GET | 用系统默认程序打开 TXT 文件（`os.startfile`） |
| `/api/queue` | GET | 获取当前队列状态 |
| `/api/episode/dequeue` | POST | 将 episode 从队列移除，恢复为 `pending` |
| `/api/queue/stop` | GET/POST | 终止当前处理中的任务 |
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

## 13. 已确认的边界情况

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
- 首次提交：`5f11fe8 feat: 初始化 VoiceToFile 项目`

---

## 17. 已知问题与修复记录

| 日期 | 问题 | 修复 |
|------|------|------|
| 2026-04-15 | 模板 `{{ now() }}` 报错：`UndefinedError: 'now' is undefined` | `render_template` 加 `now=datetime.now` |
| 2026-04-15 | 启动.bat 双击闪退：Python 不在系统 PATH | 改用硬编码完整路径 |
| 2026-04-15 | 启动.bat 中文乱码导致命令解析失败 | 批处理文件编码从 UTF-8 改为 GBK |
| 2026-04-15 | 刷新按钮点击后旧窗口关闭但浏览器未刷新 | 改为轮询 + `location.reload(true)` 强制刷新 |
| 2026-04-15 | 前端静态演示数据无法连接后端 | 重构 index.html：Jinja2 渲染主表格 + JavaScript API 动态加载 |
| 2026-04-15 | 订阅后占位集（声动早咖啡、资讯早7点等短标题）存入 DB | 新增 `_is_placeholder()` 过滤，订阅/手动添加时均过滤 |
| 2026-04-15 | 展开播客时"加载中..."一直显示不消失（API 失败时无错误处理） | 添加 `resp.ok` 检查和 `data.ok` else 分支，catch 块更新 UI 显示红色错误 |
| 2026-04-15 | 订阅/手动添加对话框失败时不关闭 | catch/else 分支均调用 `closeDialogs()` |
| 2026-04-15 | 红点在页面刷新后重新出现 | 使用 `localStorage` key `vtf_viewed_podcasts` 持久化，`markPodcastViewed()` 在展开/查看时写入 |
| 2026-04-15 | 手动添加的单集和订阅播客混在一起 | 改用虚拟播客"精选播客"（pid=`__manual__`）存放手动单集，置顶显示，不再独立区块 |
| 2026-04-15 | 订阅时无法判断音频是否真实存在 | 订阅时并行验证音频 URL（ThreadPoolExecutor 3 线程），只存储有音频的集 |
| 2026-04-15 | 500 Internal Server Error | 误删 `get_active_episodes()` 函数，已恢复 |
| 2026-04-15 | 手动添加后子表未刷新 | 手动添加成功后清空 `episodeCache = {}`，下次展开时重新加载 |
| 2026-04-15 | 手动添加后自动开始下载/转写，用户无控制权 | 手动添加只存入 DB 为 `pending` 状态，需点击"排队中"才会进入队列 |
| 2026-04-15 | 失败任务无法重试 | 支持手动重试（点击"失败"链接），并自动重试 2 次后标记失败 |

---

## 18. 最近添加的功能

### 精选播客（虚拟播客）
手动添加的单集统一归入名为"精选播客"的**虚拟播客**，PID=`__manual__`，显示在已订阅播客列表**置顶**位置，展开可查看所有手动单集。
- `/api/episode/add` 强制使用虚拟播客
- `db.get_or_create_manual_podcast()` 启动时确保虚拟播客存在

### 订阅时并行音频 URL 验证
`app.py` 的 `/api/podcast/fetch` 中，使用 3 线程 `ThreadPoolExecutor` 验证每集的音频 URL，过滤掉无音频的占位集后才存入 DB。

### localStorage 红点持久化
- Key：`vtf_viewed_podcasts`（JSON 数组）
- `markPodcastViewed(podcastId)` — 展开/查看时写入
- 页面加载时 `clearViewedBadge()` 清除已查看播客的红点 DOM

### 队列/UI 全面重构

**队列架构**（重要）：
- **单 worker 线程**：`_queue_worker` 用 `t.join()` 等待每次任务完成才取下一个，**同时只有 1 个任务在处理**
- **内存队列 + DB 状态**：`task_queue` 是内存队列，保存正在处理的任务；DB 的 `queued` 状态表示已入队等待
- **启动时清空**：`if __name__ == "__main__"` 时执行 `task_queue.clear()`，防止重启后残留显示
- **状态分离**：`pending` = 未入队，`queued` = 已入队等待处理，`downloading`/`transcribing` = 正在处理
- **停止机制**：`api_queue_stop` 设置全局 `_task_terminated=True`，处理线程检查此标志跳过重试，`finally` 不覆盖 DB 状态

**手动添加行为变更**：手动添加单集不再自动开始下载/转写，只存入 DB 的 `pending` 状态，用户需主动点击"排队中"才会进入队列处理。

**状态标签（子表/详情页）**：
- `downloading` → `音频下载中`
- `transcribing` → `◌ 音频转文字中`（带旋转动画）
- `failed` → `失败`（可点击查看错误信息）
- `queued` → `排队中`（可点击进入队列）
- `pending` → `未转化`（可点击入队）

**错误信息弹窗**：失败任务的错误信息通过弹窗 overlay 显示，点击"失败"链接弹出 `id="error-dialog-overlay"`。

**查看全部链接**：`▼ 查看全部 X 集（已转 Y 集）`，显示在展开子表底部。

**队列页面**（纯服务端 Jinja2 渲染，无 JS）：
- 顶部卡片只显示 `downloading`/`transcribing` 任务（最多 1 个）
- 下方等待列表：`排队中（共 N 个）`，状态为 `queued`
- 每项显示：播客名 — 集名 / 移除按钮 / 状态标签
- **队列历史从 DB 查询**（`get_recently_completed_episodes`），重启后仍保留最近 20 条已完成记录
- 移除按钮：POST 到 `/queue` 恢复为 `pending`（未转化）
- 终止按钮：GET `/api/queue/stop` 立即停止当前任务

**走马灯**：只显示第一个 `downloading` 或 `transcribing` 任务，不再显示所有进行中任务。

**旋转动画**：`@keyframes spin` + `.spin-icon { animation: spin 1s linear infinite; }`

**自动重试**：失败任务自动重试 2 次，仍失败才标记为 `failed`。

### 子表/详情页对齐统一
标题列左对齐，其余列（发布时间、音频长度、状态）居中对齐。

---

*最后更新：2026-04-16*
