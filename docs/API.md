# API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页，Jinja2 渲染 |
| `/queue` | GET | 独立队列页面（新窗口） |
| `/podcast/<id>` | GET | 独立播客详情页（新窗口） |
| `/api/podcast/fetch` | POST | 订阅播客（模式A） |
| `/api/episode/add` | POST | 手动添加单集（模式B） |
| `/api/podcast/<id>/episodes` | GET | 获取播客全部剧集，**每次自动同步文件状态** |
| `/api/episodes/enqueue` | POST | 将选中剧集加入队列（body: `{episode_ids: [id]}`） |
| `/api/episodes/refresh` | POST | 刷新播客（同步文件状态 + 抓新集数） |
| `/api/podcast/delete` | POST | 删除播客订阅 |
| `/api/episode/retry/<id>` | POST | 重试失败任务 |
| `/api/episode/open/<id>` | GET | 用系统默认程序打开 TXT 文件（`os.startfile`） |
| `/api/podcast/open/<id>` | GET | 用文件资源管理器打开播客输出文件夹 |
| `/api/podcast/viewed/<id>` | POST | 用户展开播客后清除该播客所有集的 is_new 标记 |
| `/api/podcasts/new-ids` | GET | 返回当前所有有 is_new 标记的 podcast_id 列表 |
| `/api/queue` | GET | 获取当前队列状态 |
| `/api/episode/dequeue` | POST | 将 episode 从队列移除，恢复为 `pending` |
| `/api/queue/stop` | GET/POST | 终止当前任务：杀进程 + 删音频文件 + DB 改 pending |
| `/api/episode/pause/<id>` | POST | 暂停任务，保留音频 |
| `/api/episode/resume/<id>` | POST | 继续暂停的任务 |
| `/api/episode/reset/<id>` | POST | 重置任务，删除音频重新入队 |
| `/api/refresh` | POST | 刷新页面（重启 Flask） |
| `/sse/stream` | GET | SSE 实时推送 |

---

## 首页子表/详情页交互

- 点击"未转化" → 调用 `enqueueEpisode(id)` → POST `/api/episodes/enqueue`
- 点击"失败" → 弹错误详情框 + [重新开始] [移除队列]
- 点击"待续转" → 调用 `resumeEpisode(id)` → POST `/api/episode/resume/<id>`
- 点击"已转化" → GET `/api/episode/open/<id>` 用系统程序打开 TXT
