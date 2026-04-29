"""
VoiceToFile — Repository 层

封装所有数据库操作，按实体拆分：
  - connection:  连接管理、表初始化
  - podcast_repo: 播客 CRUD
  - episode_repo: 单集 CRUD + 队列操作
"""
from .connection import DB_PATH, get_conn, init_db

from .podcast_repo import (
    MANUAL_PID,
    MANUAL_NAME,
    get_or_create_manual_podcast,
    add_podcast,
    upsert_podcast_details,
    get_podcast_details,
    get_podcast_by_pid,
    list_podcasts,
    delete_podcast,
)

from .episode_repo import (
    add_episodes,
    update_episode_status,
    get_episode_by_eid,
    get_episode_by_name,
    mark_episode_discarded,
    list_episodes_by_podcast,
    get_episode_by_id,
    reset_episode_for_retry,
    pause_episode,
    update_episode_duration,
    get_episodes_missing_duration,
    sync_episode_txt_status,
    sync_podcast_episodes_status,
    cleanup_all_zombie_episodes,
    get_active_episodes,
    cleanup_placeholder_episodes,
    mark_podcast_viewed,
    mark_episodes_new,
    get_podcasts_with_new,
    get_pending_episodes,
    list_manual_episodes,
    get_recently_completed_episodes,
    get_next_queued_task,
    enqueue_task,
    increment_retry_count,
    update_task_progress,
    cleanup_stale_tasks,
    mark_task_done,
    mark_task_failed,
    get_queue_status,
)

__all__ = [
    "DB_PATH", "get_conn", "init_db",
    "MANUAL_PID", "MANUAL_NAME",
    "get_or_create_manual_podcast", "add_podcast", "upsert_podcast_details",
    "get_podcast_details", "get_podcast_by_pid", "list_podcasts", "delete_podcast",
    "add_episodes", "update_episode_status", "get_episode_by_eid",
    "get_episode_by_name", "mark_episode_discarded", "list_episodes_by_podcast",
    "get_episode_by_id", "reset_episode_for_retry", "pause_episode",
    "update_episode_duration", "get_episodes_missing_duration",
    "sync_episode_txt_status", "sync_podcast_episodes_status",
    "cleanup_all_zombie_episodes", "get_active_episodes",
    "cleanup_placeholder_episodes", "mark_podcast_viewed", "mark_episodes_new",
    "get_podcasts_with_new", "get_pending_episodes", "list_manual_episodes",
    "get_recently_completed_episodes", "get_next_queued_task", "enqueue_task",
    "increment_retry_count", "update_task_progress", "cleanup_stale_tasks",
    "mark_task_done", "mark_task_failed", "get_queue_status",
]
