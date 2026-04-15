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
| `templates/index.html` | 前端页面（原生 JS，Jinja2 模板） |
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
| `pending` | 用户勾选了，等待下载 |
| `downloading` | 下载进行中 |
| `transcribing` | 转写进行中（子进程） |
| `done_deleted` | 转写完成，音频已删除 |
| `failed` | 失败，自动重试 2 次后仍失败则标记，等用户手动重试 |

---

## 7. 数据库（SQLite）

路径：`f:/voicetofile/voicetofile.db`（不提交 Git）

### podcasts 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| pid | TEXT UNIQUE | 播客 ID（小宇宙） |
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
- **无 Vue/React**：纯原生 JS实现

### 主页结构

```
┌─ 顶栏 ─────────────────────────────────────────────────┐
│ VoiceToFile              [📋 队列]  [⚙ 设置]            │
└─────────────────────────────────────────────────────────┘
┌─ 走马灯（点击进入队列视图）───────────────────────────────┐
│ ● 正在处理  随机波动—第3集[45%]  ▮ 半拿铁—第1集[转写中]  │
└─────────────────────────────────────────────────────────┘
┌─ 主表格 ────────────────────────────────────────────────┐
│ 📻 已订阅播客                    [🗑 删除]  [📡 订阅] [➕ 手动添加] [🔄 全部刷新] │
│ ┌──┬────────┬────┬─────┬──────────┬──────┐              │
│ │☑ │播客名称│集数│已转文字│最新发布时间│操作│              │
│ ├──┼────────┼────┼─────┼──────────┼──────┤              │
│ │☑ │随机波动│ 50 │ 35  │ 04-15    │[查看]│              │
│ │☑ │半拿铁  │ 30 │ 20  │ 04-12    │[查看]│              │
│ └──┴────────┴────┴─────┴──────────┴──────┘              │
└─────────────────────────────────────────────────────────┘
```

### 展开子表（点击▶展开）

显示该播客最新 5 集，不含复选框。

列：标题 / 播客发布时间(170px) / 音频长度 / 是否已转文字 / 是否免费

### 详情页（点击"查看"进入）

- 顶部统计：已转文字数 / 未转文字数 / 付费数
- 表格列：标题 / 发布时间(160px) / 音频长度 / 是否已转文字 / 是否免费
- **无复选框**
- 分页：每页 20 条

### 队列视图

卡片式布局，显示：播客名 — 集名 / 状态标签 / 进度 / 耗时

### 对话框

- **订阅播客**：输入播客链接或 PID
- **手动添加**：输入单集链接

---

## 10. API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页，Jinja2 渲染 |
| `/api/podcast/fetch` | POST | 订阅播客（模式A） |
| `/api/episode/add` | POST | 手动添加单集（模式B） |
| `/api/podcast/<id>/episodes` | GET | 获取播客全部剧集（前端展开/详情页用） |
| `/api/episodes/enqueue` | POST | 将选中剧集加入队列 |
| `/api/episodes/refresh` | POST | 重新获取播客列表 |
| `/api/podcast/delete` | POST | 删除播客订阅 |
| `/api/episode/retry/<id>` | POST | 重试失败任务 |
| `/api/queue` | GET | 获取当前队列状态 |
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

## 16. 已知问题与修复记录

| 日期 | 问题 | 修复 |
|------|------|------|
| 2026-04-15 | 模板 `{{ now() }}` 报错：`UndefinedError: 'now' is undefined` | `render_template` 加 `now=datetime.now` |
| 2026-04-15 | 启动.bat 双击闪退：Python 不在系统 PATH | 改用硬编码完整路径 `C:\Users\wule_\AppData\Local\Programs\Python\Python312\python.exe` |
| 2026-04-15 | 启动.bat 中文乱码导致命令解析失败 | 批处理文件编码从 UTF-8 改为 GBK |
| 2026-04-15 | 刷新按钮点击后旧窗口关闭但浏览器未刷新 | 改为轮询 + `location.reload(true)` 强制刷新 |
| 2026-04-15 | 前端静态演示数据无法连接后端 | 重构 index.html：Jinja2 渲染主表格 + JavaScript API 动态加载 |

---

*最后更新：2026-04-15*
