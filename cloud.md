# VoiceToFile 云端同步文档

本文档记录 VoiceToFile 项目在本地开发环境的变更，供云端同步参考。

---

## 最近提交（2026-04-16）

### 修复：队列页面重复显示相同任务

**问题**：同一个 episode 在"排队中"列表里出现两次，原因是：
- `api_queue` 接口从两个数据源查询排队任务：`db.get_pending_episodes()` 和直接查询 `WHERE status='queued'`
- 没有对两个数据源的结果做去重
- 同一个 episode 可能同时存在于内存的 `task_queue` 和 DB 的 `queued` 状态

**修复**（app.py 第 701-716 行）：
- 新增 `pending_eids` Set 追踪已加入列表的 episode
- 所有来源的查询结果统一去重后返回

```python
# 排队中：task_queue 里的 pending/queued + DB 里的 queued
with queue_lock:
    in_queue = [t for t in task_queue if t.get("status") in ("pending", "queued")]
in_queue_eids = {t["eid"] for t in in_queue}
all_pending = db.get_pending_episodes()
pending_eids = set()
pending = []
for p in all_pending:
    if p["eid"] not in active_eids and p["eid"] not in in_queue_eids and p["eid"] not in pending_eids:
        pending_eids.add(p["eid"])
        pending.append(p)
# 从 DB 补充 queued 状态（也要去重）
conn = db.get_conn()
try:
    cur = conn.cursor()
    cur.execute("SELECT e.*, p.name as podcast_name FROM episodes e JOIN podcasts p ON e.podcast_id = p.id WHERE e.status = 'queued'")
    for row in cur.fetchall():
        rd = dict(row)
        if rd["eid"] not in active_eids and rd["eid"] not in in_queue_eids and rd["eid"] not in pending_eids:
            pending.append(rd)
finally:
    conn.close()
```

---

### 新增：首页 5 秒状态自动同步

**问题**：点击 episode 后状态停留在"排队中"，不会更新为"处理中"

**修复**（templates/new_index.html）：
- 新增 `/api/homepage/status` 接口，返回 `{episodeId: status}` 格式
- 新增 `syncHomepageStatus()` 函数，每 5 秒对已展开的播客 sub-table 进行状态刷新
- 改动 `refreshSubTable()` 使其支持更新已存在的 episode 行

```javascript
// 5秒状态同步
async function syncHomepageStatus() {
  for (const podcastId of Object.keys(episodeCache)) {
    const expandedRow = document.getElementById(`expand-${podcastId}`);
    if (expandedRow && expandedRow.style.display !== 'none') {
      await refreshSubTable(parseInt(podcastId));
    }
  }
}
setInterval(syncHomepageStatus, 5000);
```

后端接口（app.py）：
```python
@app.route("/api/homepage/status")
def api_homepage_status():
    statuses = {}
    with queue_lock:
        for t in task_queue:
            if t.get("status") not in ("done", "failed"):
                statuses[str(t["episode_id"])] = t["status"]
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, status FROM episodes WHERE status IN ('downloading', 'transcribing', 'queued')")
        for row in cur.fetchall():
            statuses[str(row["id"])] = row["status"]
    finally:
        conn.close()
    return jsonify({"statuses": statuses})
```

---

### 新增：走马灯（Ticker）实时显示

**问题**：首页顶部走马灯始终为空

**修复**：
- `window._tickerTasks` 全局数组存储活跃任务
- SSE `task_update` / `task_new` / `task_done` 事件处理器实时更新走马灯
- `loadQueue()` 从 API 同步走马灯数据作为 fallback
- 支持 `pending`/`queued` 状态显示为"排队中"，`downloading` 显示"音频下载中"，`transcribing` 显示"音频转文字中"

---

### 修复：Flask 重启后多个 worker 同时处理任务

**问题**：Flask 重启时，DB 里可能有多个 episode 处于 `downloading`/`transcribing` 状态，如果 worker 在 cleanup 之前就读取 DB，会导致多个任务同时被拾起

**修复**（app.py 启动顺序）：
- 先执行 `task_queue.clear()` 清空内存队列
- 再执行 `UPDATE episodes SET status='queued' WHERE status IN ('downloading', 'transcribing')` 清理 DB 残留状态
- 最后才启动 worker 线程

---

### 新增：队列页面（/queue）

独立页面显示：
- 正在处理的任务（进度条、已耗时、终止按钮）
- 排队中的任务（可移除）
- 已完成/失败的任务

技术实现：
- SSE 实时推送 `task_update` / `task_new` / `task_done` 事件
- 每秒轮询 `/api/queue` API 补充状态
- `window._cachedTasks` 缓存所有任务，完成的任务永久保留在列表

---

### 新增：终止任务功能

- `/api/queue/stop` 接口：终止当前正在处理的任务
- 会从 `task_queue` 和 DB 中移除任务
- 设置 `_task_terminated` 标志防止 finally 块重复处理

---

## 文件变更汇总

| 文件 | 变更内容 |
|------|----------|
| `app.py` | 新增 `/api/homepage/status` 接口、修复队列去重、修复启动顺序、终止任务功能 |
| `db.py` | 新增 `get_pending_episodes(limit)` 函数、两个 `get_pending_episodes` 重名（第二个生效） |
| `templates/new_index.html` | 走马灯、5秒同步、SSE 事件处理、detail page 行状态同步 |
| `templates/queue.html` | 新增独立队列页面 |
| `templates/podcast.html` | 新增播客详情页面 |
| `CLAUDE.md` | 项目知识快照文档 |

---

## 待注意的潜在问题

1. **db.py 有两个同名函数** `get_pending_episodes()`（line 349 和 line 401），Python 使用最后定义的那个。旧函数未被删除，可能造成混淆。

2. **SSE 重连机制**：`homeEvtSource` 的 SSE 连接在断线后会自动重连，但 `queueEvtSource` 在 queue.html 页面没有实现重连逻辑。

3. **浏览器缓存**：建议使用 `Ctrl+Shift+R` 强制刷新页面，以确保加载最新 JS 代码。
