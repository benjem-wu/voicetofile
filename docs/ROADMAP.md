# 待实现功能

## 20.1 播客详情表（podcast_details）

> **方案**：新建 `podcast_details` 表，podcasts 表保持不变。

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

---

## 20.2 刷新时 audio_url 过滤

✅ 已实现（见入库过滤规则）

---

## 20.3 同名 episode 去重（保留最长版）

> **问题**：小宇宙同一播客下多个录音共用同一标题（如"Vol.261"出现多次，时长 28min vs 87min），导致列表页显示重复。

**去重规则**：
- 按 `podcast_id + name` 分组，保留 `duration` 最长的那条
- 短版标记 `discarded=1`，不展示在列表中
- 后续刷新时，新 episode 若比已有同名 episode 时长短，则跳过入库

**实现位置**：`api_refresh_episodes()` 中的入库前去重逻辑 + `mark_episode_discarded()`

**DB 字段**：`episodes.discarded`（0=活跃，1=废弃）
