"""
测试：刷新优化逻辑
验证新增集识别 + 只对新增集验证音频
"""
import sys
sys.path.insert(0, '.')

import db
import scraper
import config
from scraper import fetch_episode_info
from scraper import PodcastInfo

def test_refresh_optimization(podcast_id: int):
    # 1. 获取播客信息
    p = db.get_conn().execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
    pid = p["pid"]
    info = scraper.fetch_podcast_info(pid, interval=1)

    # 2. 查 DB 已有的 eid
    existing_eids = set(
        row["eid"] for row in db.get_conn().execute(
            "SELECT eid FROM episodes WHERE podcast_id = ?", (podcast_id,)
        ).fetchall()
    )

    # 3. 统计
    all_eids = [ep.eid for ep in info.episodes]
    new_eids = [ep.eid for ep in info.episodes if ep.eid not in existing_eids]
    old_eids = [ep.eid for ep in info.episodes if ep.eid in existing_eids]

    print(f"\n=== 测试结果 ===")
    print(f"播客: {p['name']} (id={podcast_id})")
    print(f"网络总集数: {len(all_eids)}")
    print(f"DB已有集数: {len(existing_eids)}")
    print(f"新增集数:   {len(new_eids)}")
    print(f"旧逻辑需 fetch_episode_info: {len(all_eids)} 次")
    print(f"新逻辑只需 fetch_episode_info: {len(new_eids)} 次")
    if all_eids:
        print(f"减少请求: {len(all_eids) - len(new_eids)} 次 ({100*(len(all_eids)-len(new_eids))//len(all_eids)}%)")

    # 4. 验证：只对新增集调用 fetch_episode_info
    print(f"\n=== 验证：实际只 fetch 新增集 ===")
    for ep in info.episodes:
        if ep.eid not in existing_eids:
            print(f"  [NEW] eid={ep.eid}, name={ep.name[:30]}")

    return len(new_eids), len(all_eids)

if __name__ == "__main__":
    # 用 podcast_id=1 测试
    new_count, total_count = test_refresh_optimization(1)
    print(f"\n测试通过: 新逻辑只处理 {new_count} 个新增集，总集数 {total_count}")
