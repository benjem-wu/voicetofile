# 飞书机器人转发播客链接 — 设计文档

## 1. 概述

**目标**：用户通过飞书机器人发送小宇宙播客链接，PC 服务自动接收并加入处理队列，完成后通过飞书通知用户。

**核心价值**：省去"手机复制链接→发到PC→粘贴到浏览器→输入项目"的手动操作，实现手机到PC的自动同步。

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  手机飞书   │ ──── │  飞书服务器  │ ──── │  PC 服务    │
│  (发送链接) │      │  (长连接)    │      │  (接收处理) │
└─────────────┘      └─────────────┘      └─────────────┘
                                                  │
                                                  ▼
                                          ┌─────────────┐
                                          │  现有队列系统 │
                                          │  (scraper)   │
                                          └─────────────┘
```

### 2.2 技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| 飞书事件接收 | 长连接模式 (Persistent Connection) | 不需要公网URL，PC主动连飞书 |
| 消息格式 | 飞书 Card 消息 | 支持富文本回复 |
| PC 内部通信 | HTTP POST (127.0.0.1) | 复用现有 Flask 服务 |

---

## 3. 模块划分

### 3.1 新增模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 飞书事件接收器 | `feishu/receiver.py` | 建立长连接、接收飞书事件、验证签名 |
| 飞书消息解析器 | `feishu/parser.py` | 解析消息文本、提取播客链接 |
| 飞书通知器 | `feishu/notifier.py` | 向用户发送飞书消息（完成通知） |
| 飞书路由 | `feishu/routes.py` | Flask 蓝图，注册路由 |
| 飞书配置 | `feishu/config.py` | 飞书机器人配置（App ID、App Secret） |

### 3.2 复用现有模块

- `scraper.py` — 播客抓取（复用）
- `db.py` — 数据库操作（复用）
- `sse.py` — 实时广播（复用）

---

## 4. 数据流

### 4.1 用户发送链接流程

```
1. 用户在飞书向机器人发送消息: "https://www.xiaoyuzhoufm.com/episode/xxx"
2. 飞书服务器通过长连接推送事件到 PC 服务
3. feishu/receiver.py 接收事件，验证签名
4. feishu/parser.py 提取链接
5. 飞书回复用户: "收到，正在处理..."
6. PC 服务内部调用 /api/add（127.0.0.1:18990）
7. scraper 抓取播客信息，入队列
8. 后续流程复用现有 worker 处理
```

### 4.2 处理完成通知流程

```
1. Worker 完成转写，状态变为 done_deleted
2. SSE 广播状态更新（现有逻辑）
3. feishu/notifier.py 调用飞书 API
4. 飞书通知用户: "播客 {name} 已转化完成，文字稿已保存"
```

---

## 5. 接口设计

### 5.1 飞书事件接收端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/feishu/events` | POST | 飞书长连接事件接收 |
| `/feishu/health` | GET | 健康检查 |

### 5.2 飞书消息回复

使用飞书 Card 消息格式，示例：

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": { "tag": "plain_text", "content": "📥 收到链接" },
      "template": "blue"
    },
    "elements": [
      { "tag": "markdown", "content": "**播客名称**\n正在处理中..." }
    ]
  }
}
```

### 5.3 PC 内部接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/add` | POST | 添加播客到队列（现有接口） |

---

## 6. 配置项

在 `config.py` 中新增：

```python
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_BOT_NAME = os.getenv("FEISHU_BOT_NAME", "VoiceToFile")
```

环境变量存储在 `.env` 或 `.feishu_config` 文件中（不提交 Git）。

---

## 7. Phase 1 最小范围

**Phase 1 只做一件事：链路打通**

| 功能 | 状态 | 说明 |
|------|------|------|
| 飞书机器人接收链接 | ✅ | 提取链接并回复"收到" |
| PC 内部转发 | ❌ | Phase 2 再做 |
| 飞书通知 | ❌ | Phase 2 再做 |
| 错误处理 | ❌ | Phase 2 再做 |

**Phase 1 验收标准：**
- 手机飞书发送链接 → 飞书回复"收到" ✅

**Phase 2：**
- PC 内部调用 /api/add 入队列
- scraper 抓取播客信息

**Phase 3：**
- 处理完成后飞书通知用户

---

## 8. 文件变更清单

### Phase 1 新增文件

```
feishu/
├── __init__.py
├── config.py      # 飞书配置
├── receiver.py    # 长连接事件接收
├── parser.py      # 消息解析、链接提取
└── routes.py      # Flask 蓝图
```

### Phase 1 修改文件

```
config.py          # 新增 FEISHU_* 配置项
app.py             # 注册飞书蓝图
```

（notifier.py Phase 2/3 再做）

---

## 9. 依赖

```
feishu-skill-sdk  # 飞书官方 Python SDK（lark-oapi）
```

---

## 10. 待确认事项

- [ ] 飞书应用创建（用户操作）
- [ ] App ID 和 App Secret 获取
- [ ] 飞书机器人的长连接权限配置
