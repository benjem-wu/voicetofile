# 数据库（SQLite）

路径：`f:/voicetofile/voicetofile.db`（不提交 Git）

---

## podcasts 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| pid | TEXT UNIQUE | 播客 ID（小宇宙）；`__manual__` 为虚拟播客"精选播客" |
| name | TEXT | 播客名称（抓取后缓存 DB） |
| added_at | TIMESTAMP | 订阅时间 |

---

## episodes 表

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

---

## podcast_details 表

| 字段 | 类型 | 说明 |
|------|------|------|
| podcast_id | INTEGER PK, FK | 关联 podcasts.id，一对一 |
| author | TEXT | 作者/主播名 |
| description | TEXT | 节目简介 |
| cover_url | TEXT | 封面图 URL |
| subscriber_count | INTEGER | 订阅数 |
| episode_count | INTEGER | 总集数（来自列表页） |
| updated_at | TIMESTAMP | 最后同步时间 |

---

## 入库过滤规则

episode 满足以下条件之一方可入库：
- **条件A**：有 `audio_url`（非空字符串）
- **条件B**：`_is_placeholder()` 返回 False（name >= 7 字且非占位标题）
- **入库前去重**：同名 episode 保留时长最长者，短版标记 `discarded=1` 不展示

---

## db.py 关键函数

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
