# Web UI

## 前端架构

- **模板引擎**：Jinja2 + 原生 JavaScript
- **实时更新**：轮询 `/api/queue`（每 3 秒）
- **数据缓存**：客户端 episodeCache 缓存已加载的播客数据
- **无 Vue/React**：纯原生 JS 实现
- **红点持久化**：`episodes.is_new` 字段，展开/查看播客后清除
- **单页保护**：`localStorage` key `vtf_page_active`，多 tab 时自动关闭

---

## 状态显示（子表/详情页统一）

| 状态 | 显示文案 | 可点击 |
|------|---------|--------|
| `pending` | 未转化 | 可点击入队 |
| `queued` | 排队中 | 可点击 |
| `downloading` | 音频下载中 | 不可点击 |
| `transcribing` | ◌ 音频转文字中（旋转动画） | 不可点击 |
| `paused` | **待续转** | **可点击继续** |
| `failed` | 失败 | 可点击（弹框重试/移除） |
| `done_deleted` | 已转化 | 可点击（打开 TXT） |

---

## 首页子表/详情页交互

- 点击"未转化" → 调用 `enqueueEpisode(id)` → POST `/api/episodes/enqueue`
- 点击"失败" → 弹错误详情框 + [重新开始] [移除队列]
- 点击"待续转" → 调用 `resumeEpisode(id)` → POST `/api/episode/resume/<id>`
- 点击"已转化" → GET `/api/episode/open/<id>` 用系统程序打开 TXT

---

## 模板文件

| 文件 | 说明 |
|------|------|
| `templates/new_index.html` | 首页（单页保护 `vtf_page_active`） |
| `templates/queue.html` | 独立队列页面 |
| `templates/podcast_detail.html` | 独立播客详情页（无单页保护，用于"查看"按钮新窗口打开） |
