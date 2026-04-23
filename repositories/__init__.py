"""
Repository 层
VoiceToFile — 小宇宙播客转文字

按实体拆分的数据库访问层：
- podcast_repo.py: 播客相关函数
- episode_repo.py: 单集相关函数
- QueueRepo: 队列相关操作（保留在 db.py）
"""
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
    mark_podcast_viewed,
    get_podcasts_with_new,
)
from .episode_repo import (
    _is_placeholder,
    add_episodes,
    get_episode_by_eid,
    get_episode_by_name,
    mark_episode_discarded,
    list_episodes_by_podcast,
    get_episode_by_id,
    update_episode_status,
    reset_episode_for_retry,
    pause_episode,
    update_episode_duration,
    get_episodes_missing_duration,
    sync_episode_txt_status,
    sync_podcast_episodes_status,
    cleanup_all_zombie_episodes,
    get_active_episodes,
    cleanup_placeholder_episodes,
    mark_episodes_new,
    get_pending_episodes,
    list_manual_episodes,
    get_recently_completed_episodes,
)

__all__ = [
    "MANUAL_PID",
    "MANUAL_NAME",
    "get_or_create_manual_podcast",
    "add_podcast",
    "upsert_podcast_details",
    "get_podcast_details",
    "get_podcast_by_pid",
    "list_podcasts",
    "delete_podcast",
    "mark_podcast_viewed",
    "get_podcasts_with_new",
    "_is_placeholder",
    "add_episodes",
    "get_episode_by_eid",
    "get_episode_by_name",
    "mark_episode_discarded",
    "list_episodes_by_podcast",
    "get_episode_by_id",
    "update_episode_status",
    "reset_episode_for_retry",
    "pause_episode",
    "update_episode_duration",
    "get_episodes_missing_duration",
    "sync_episode_txt_status",
    "sync_podcast_episodes_status",
    "cleanup_all_zombie_episodes",
    "get_active_episodes",
    "cleanup_placeholder_episodes",
    "mark_episodes_new",
    "get_pending_episodes",
    "list_manual_episodes",
    "get_recently_completed_episodes",
]
