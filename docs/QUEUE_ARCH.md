# 队列架构 v2

> **原则**：取消内存队列，所有任务状态统一存在 `episodes` 表。Flask 重启不丢状态。

---

## 状态流转

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

---

## 终止流程（downloading/transcribing 时点击"终止任务"）

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
- **时序关键**：`db.update_episode_status` 必须在 `broadcast_sse` 之前

---

## 暂停后继续（paused 时点击"待续转"）

```
├─ audio_path 存在且完整 → 状态改 transcribing，跳过下载，直接转写
└─ audio_path 不存在或不完整 → 删路径，状态改 downloading，重新下载
```

---

## worker.py 全局状态（进程级别）

```python
_download_proc      # 当前 download subprocess（用于 kill）
_transcribe_proc    # 当前 transcribe subprocess（用于 kill）
_task_terminated    # 标记任务被提前终止
_current_task_info  # 当前任务信息（供 api_queue_stop 获取 episode_id）
_current_audio_file # 当前音频文件路径（供终止时验证）
_current_output_dir # 当前输出目录（供终止时清理 progress 文件）
```

对外：`get_current_task_info()` · `get_current_audio_file()` · `set_task_terminated()` · `is_task_terminated()` · `terminate_current_task()`

---

## 转写状态文件架构

transcriber.py 子进程写入 `_transcribe_state_{episode_id}.json`（权威来源），worker.py 每秒轮询更新 DB + SSE。

```
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

---

## 与旧架构对比

| 对比项 | 旧架构（v1） | 新架构（v2） |
|--------|------------|------------|
| 任务队列 | `task_queue` 内存 + DB 双份 | DB 唯一 |
| Flask 重启 | `task_queue` 丢失 | 状态保留在 DB |
| 残留任务 | 需手动清理 | `cleanup_stale_tasks()` 启动时自动重置为 pending |
| 子进程卡死 | 永久阻塞 | timeout 后 kill |
| 停止任务 | 只能请求，无法强制 | `proc.kill()` 强制 |
