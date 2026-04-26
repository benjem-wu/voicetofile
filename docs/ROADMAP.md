# 待实现功能

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
