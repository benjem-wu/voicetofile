# 模块化重构（2026-04-17）

> app.py 从 1289 行拆分为多个独立模块。改动不再互相干扰。

---

## 拆分后的文件

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

---

## routes 模块调用关系

```
routes/podcasts.py  → db.*, scraper.*, worker.get_output_dir()
routes/episodes.py  → db.*, scraper.*, worker.*, sse.*
routes/queue.py     → worker.*, sse.broadcast_sse
routes/system.py    → db.*, config.*, sse.sse_subscribers/sse_lock
```

无循环依赖。

---

## 独立详情页解决方案（"查看"按钮）

`new_index.html` 的单页保护 `vtf_page_active` 导致：点击"查看"→新窗口打开 `podcast_detail` tab→检测到旧 tab 的 localStorage→调用 `window.close()` 自杀。

**解决方案**：新建 `templates/podcast_detail.html`，内容从 `new_index.html` 的详情部分提取，**不包含单页保护 JS**。`app.py` 的 `/podcast/<id>` 路由渲染此独立模板，10 个播客共用 1 个模板文件（通过 URL 参数 `podcast.id` 区分）。

---

## 模块架构

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
