"""
SQLite 连接管理
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "voicetofile.db"


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（每次新建，自动设置 row_factory）"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库，创建表（如不存在）"""
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pid TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                podcast_id INTEGER NOT NULL,
                eid TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                pub_date TEXT DEFAULT '',
                duration TEXT DEFAULT '',
                is_paid INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                txt_path TEXT DEFAULT '',
                error_msg TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'subscribe',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                UNIQUE(podcast_id, eid)
            )
        """)
        # 兼容已有数据库
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN source TEXT NOT NULL DEFAULT 'subscribe'")
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0")
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN audio_path TEXT DEFAULT ''")
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN discarded INTEGER NOT NULL DEFAULT 0")
        _add_column_if_not_exists(cur, "ALTER TABLE episodes ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")

        # 索引加速查询
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_podcast
            ON episodes(podcast_id, status)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_status
            ON episodes(status)
        """)

        # podcast_details 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS podcast_details (
                podcast_id INTEGER PRIMARY KEY,
                author TEXT DEFAULT '',
                description TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                subscriber_count INTEGER DEFAULT 0,
                episode_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
    finally:
        conn.close()


def _add_column_if_not_exists(cur, alter_sql: str):
    """安全添加列（表已存在该列则跳过）"""
    try:
        cur.execute(alter_sql)
    except sqlite3.OperationalError:
        pass
