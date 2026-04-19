"""
SSE 广播系统
VoiceToFile — 小宇宙播客转文字

所有 SSE 状态和广播函数集中在本模块，供 worker.py 和 routes/*.py 使用。
"""
import json
import queue
import threading

# --------------- SSE 全局状态（供 routes.system 使用）---------------

sse_subscribers: list = []
sse_lock = threading.Lock()


# --------------- 广播函数 ---------------

def broadcast_sse(event: str, data: dict):
    """向所有 SSE 订阅者广播消息，永不阻塞（慢消费者会被移除）"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for sub in sse_subscribers:
            try:
                sub.put_nowait(msg)
            except queue.Full:
                dead.append(sub)
            except Exception:
                dead.append(sub)
        for d in dead:
            try:
                sse_subscribers.remove(d)
            except Exception:
                pass


def addLog(text: str, log_type: str = "tag"):
    """前端日志推送"""
    broadcast_sse("log", {"text": text, "type": log_type})


def task_update(eid: str, **kwargs):
    """更新任务状态并推送 SSE"""
    broadcast_sse("task_update", {"eid": eid, **kwargs})
