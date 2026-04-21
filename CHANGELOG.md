# Changelog

> 本文件记录 VoiceToFile 的开发历史、问题修复和架构演进。

---

## 已知问题与修复记录

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
| 2026-04-19 | Flask 重启后转写子进程继续独立运行，DB 被 `cleanup_stale_tasks` 重置为 `pending`，TXT 已生成但队列页不显示 | `cleanup_stale_tasks()` 启动时检查 TXT 文件是否已存在：存在则标记 `done_deleted`，不存在才恢复 `pending`；同时在输出目录按命名规则查找 `*_文字稿.txt` |
| 2026-04-19 | 队列页 F5 刷新后显示"暂无正在处理的任务"（第一次刷新正常，第二次异常） | 三重兜底：① `cleanup_stale_tasks()` 正确识别已完成的 TXT；② `/api/queue` 扫描 `_transcribe_state_*.json` 状态文件补充孤儿转写进程；③ `queue.html` 用 `sessionStorage` 缓存活跃任务，API 返回0个活跃任务时自动从缓存恢复（30分钟有效） |
| 2026-04-19 | 首页 ticker 显示任务 ID 而非名称（`6989cc6266e2c30377a5b227`），刷新后丢失 | 首页 ticker 缓存 `_tickerTasks` 改用 Map 合并策略（API 权威数据优先 + 缓存补全 LIMIT 50 截断的完成/失败任务）；`sessionStorage` 缓存最近完成任务（30分钟），缺失 `name/podcast_name` 时从中兜底查找 |
| 2026-04-19 | 刷新播客时 TXT 文件已存在但 status 非 `done_deleted` 的 episode 未被修正 | `api_refresh_episodes()` 新增同步逻辑：遍历已有 episode，按命名规则查找 `*_文字稿.txt`，若文件存在则修正 status 为 `done_deleted` |
| 2026-04-19 | `routes/queue.py` 缺少 `config` 模块引用导致 500 错误 | 添加 `import config`；修复 `conn` 在 `finally: conn.close()` 后被继续使用（改用独立 `conn2`） |

---

## 架构演进

### 队列架构 v1 → v2

| 对比项 | 旧架构（v1） | 新架构（v2） |
|--------|------------|------------|
| 任务队列 | `task_queue` 内存 + DB 双份 | DB 唯一 |
| Flask 重启 | `task_queue` 丢失 | 状态保留在 DB |
| 残留任务 | 需手动清理 | `cleanup_stale_tasks()` 启动时自动重置为 pending |
| 子进程卡死 | 永久阻塞 | timeout 后 kill |
| 停止任务 | 只能请求，无法强制 | `proc.kill()` 强制 |

### 模块化重构（2026-04-17）

app.py 从 1289 行拆分为多个独立模块。改动不再互相干扰。

**拆分后的文件**：

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

**routes 模块调用关系**：

```
routes/podcasts.py  → db.*, scraper.*, worker.get_output_dir()
routes/episodes.py  → db.*, scraper.*, worker.*, sse.*
routes/queue.py     → worker.*, sse.broadcast_sse
routes/system.py    → db.*, config.*, sse.sse_subscribers/sse_lock
```

无循环依赖。

---

## 开发经验总结

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
