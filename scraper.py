"""
小宇宙播客抓取模块
VoiceToFile — 小宇宙播客转文字
支持：播客订阅页批量获取 episode + 单集详情页解析
内置：付费检测 + 反爬策略（Cookie / 间隔 / 降级 Playwright）
"""
import re
import json
import time
import random
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("scraper")

# --------------- 常量 ---------------

PODCAST_PAGE_URL = "https://www.xiaoyuzhoufm.com/podcast/{pid}"
EPISODE_PAGE_URL = "https://www.xiaoyuzhoufm.com/episode/{eid}"
EPISODE_DATA_URL = "https://www.xiaoyuzhoufm.com/_next/data/-GOav0dS9wDlfSnB05lx2/episode/{eid}.json"

COOKIE_FILE = Path(__file__).parent / ".cookie"
DEFAULT_INTERVAL = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


# --------------- 付费检测 ---------------

PAID_PRICE_RE = re.compile(
    r"(?:单集)?售价?(\d+\.?\d*)\s*元|优惠价(\d+\.?\d*)\s*元",
    re.UNICODE,
)
# 这些词出现在 description 中时通常意味着付费（但需配合价格或单独出现）
PAID_EXPLICIT_RE = re.compile(
    r"购买|小鹅通|已付费|已购买",
    re.UNICODE,
)


def is_paid_episode(description: str) -> bool:
    if not description:
        return False
    # 有具体价格 → 付费
    if PAID_PRICE_RE.search(description):
        return True
    # 有明确购买词且 description 较短（不太像节目介绍）→ 付费
    if PAID_EXPLICIT_RE.search(description) and len(description) < 200:
        return True
    return False


def extract_paid_price(description: str) -> Optional[str]:
    if not description:
        return None
    m = re.search(r"(?:单集)?售价?(\d+\.?\d*)\s*元", description)
    if m:
        return f"¥{m.group(1)}"
    m = re.search(r"优惠价(\d+\.?\d*)\s*元", description)
    if m:
        return f"¥{m.group(1)} (优惠)"
    return "¥?"


# --------------- 数据结构 ---------------

@dataclass
class EpisodeInfo:
    eid: str
    name: str
    pub_date: str = ""
    duration: str = ""
    description: str = ""
    is_paid: bool = False
    paid_price: Optional[str] = None
    audio_url: str = ""
    pid: str = ""


@dataclass
class PodcastInfo:
    pid: str
    name: str
    author: str = ""
    description: str = ""
    cover_url: str = ""
    subscriber_count: int = 0
    episode_count: int = 0
    episodes: list = field(default_factory=list)


# --------------- Cookie 管理 ---------------

def load_cookie() -> str:
    return COOKIE_FILE.read_text(encoding="utf-8").strip() if COOKIE_FILE.exists() else ""


def save_cookie(cookie: str):
    COOKIE_FILE.write_text(cookie.strip(), encoding="utf-8")


# --------------- HTML 解析 ---------------

def _unescape_js(s: str) -> str:
    """反转义 JavaScript 字符串（处理 \\n \\\" 等）"""
    if not s:
        return s
    result = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == 'n':
                result.append('\n')
            elif nxt == 'r':
                result.append('\r')
            elif nxt == '"':
                result.append('"')
            elif nxt == '\\':
                result.append('\\')
            elif nxt == '/':
                result.append('/')
            elif nxt == 't':
                result.append('\t')
            else:
                result.append(ch)
            i += 2
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


def _extract_episodes_from_html(html: str) -> list[EpisodeInfo]:
    """
    从播客页面 HTML 提取 episode 列表。

    JavaScript 数据含 eid（JSON-LD 的 workExample 不含），靠 name 字段关联两者。
    """
    # 阶段1：从 JavaScript 提取 eid + name + pubDate（逐字段安全提取）
    m = re.search(r'"episodes"\s*:\s*\[', html)
    if not m:
        return []
    chunk = html[m.end():m.end() + 500000]

    # 提取所有 eid
    eid_map = {}  # eid -> episode index
    eid_list = []
    for m2 in re.finditer(r'"eid"\s*:\s*"([a-f0-9]{20,})"', chunk):
        idx = len(eid_list)
        eid_list.append(m2.group(1))
        eid_map[m2.group(1)] = idx

    # 提取所有 title（按出现顺序对应 eid 列表）
    title_list = []
    for m3 in re.finditer(r'"title"\s*:\s*"([^"]*)"', chunk):
        title_list.append(_unescape_js(m3.group(1)))

    # 提取所有 pubDate
    pubdate_list = []
    for m4 in re.finditer(r'"pubDate"\s*:\s*"([^"]*)"', chunk):
        pubdate_list.append(m4.group(1))

    # JSON-LD 补充 description / duration / paid
    ld_eps = {}  # name -> {description, duration}
    for m5 in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        raw = m5.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if item.get("@type") != "PodcastEpisode":
                continue
            desc = item.get("description") or ""
            we_name = item.get("name") or ""
            ld_eps[we_name] = {
                "description": desc,
                "duration": item.get("timeRequired") or "",
            }

    # 按 JavaScript 顺序构建结果（eid/name/pubDate 来自 JS，description 等来自 JSON-LD）
    result = []
    n = min(len(eid_list), len(title_list), len(pubdate_list))
    for i in range(n):
        name = title_list[i]
        eid = eid_list[i]
        pub_date = pubdate_list[i]
        ld = ld_eps.get(name, {})
        desc = ld.get("description", "")
        is_paid = is_paid_episode(desc)
        paid_price = extract_paid_price(desc) if is_paid else None
        result.append(EpisodeInfo(
            eid=eid,
            name=name,
            pub_date=pub_date,
            duration=ld.get("duration", ""),
            description=desc,
            is_paid=is_paid,
            paid_price=paid_price,
        ))

    return result


def _extract_podcast_name(html: str) -> str:
    """从 __NEXT_DATA__ 或 <title> 提取播客名称"""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1).strip())
            title = d.get("props", {}).get("pageProps", {}).get("podcast", {}).get("title", "")
            if title:
                return title
        except Exception:
            pass
    m = re.search(r'<title>([^<]+)</title>', html)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[-–|]\s*(小宇宙|xiaoyuzhoufm).*$", "", title)
        if title:
            return title.strip()
    return ""


def _extract_podcast_metadata(html: str) -> dict:
    """
    从 __NEXT_DATA__ 提取播客元数据。
    返回 dict：author, description, cover_url, subscriber_count, episode_count
    """
    result = {
        "author": "",
        "description": "",
        "cover_url": "",
        "subscriber_count": 0,
        "episode_count": 0,
    }
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return result
    try:
        data = json.loads(m.group(1).strip())
        podcast = data.get("props", {}).get("pageProps", {}).get("podcast", {})
        result["author"] = podcast.get("author", "") or ""
        result["description"] = podcast.get("description", "") or ""
        image = podcast.get("image")
        if isinstance(image, dict):
            result["cover_url"] = image.get("picUrl", "") or ""
        elif isinstance(image, str):
            result["cover_url"] = image
        result["subscriber_count"] = int(podcast.get("subscriptionCount", 0) or 0)
        result["episode_count"] = int(podcast.get("episodeCount", 0) or 0)
    except Exception:
        pass
    return result


# --------------- Scraper ---------------

class Scraper:
    def __init__(self, interval: int = DEFAULT_INTERVAL):
        self.interval = interval
        self.cookie = load_cookie()
        self._last_request_time = 0.0
        self._playwright_available = False
        self._request_count = 0

    def _ua(self) -> str:
        return random.choice(USER_AGENTS)

    def _hdrs(self, referer: str = "https://www.xiaoyuzhoufm.com/") -> dict:
        h = {
            "User-Agent": self._ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
        }
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def _wait_interval(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request_time = time.time()

    def _do_request(self, method: str, url: str, **kw) -> requests.Response:
        self._wait_interval()
        self._request_count += 1
        for attempt in range(3):
            try:
                resp = requests.request(method, url, headers=self._hdrs(), timeout=20, **kw)
                resp.raise_for_status()
                # 小宇宙返回 UTF-8 内容，显式指定避免 requests 误判编码
                resp.encoding = "utf-8"
                return resp
            except Exception as e:
                logger.warning(f"请求失败（第{attempt+1}/3）: {url} — {e}")
                if attempt < 2:
                    wait = 10 * (attempt + 1)
                    logger.info(f"等待 {wait}s 后重试...")
                    time.sleep(wait)
                else:
                    raise

    def get(self, url: str, **kw) -> requests.Response:
        return self._do_request("GET", url, **kw)

    def _playwright_get(self, url: str) -> str:
        if not self._playwright_available:
            try:
                from playwright.sync_api import sync_playwright
                self._playwright_available = True
            except ImportError:
                raise RuntimeError("Playwright 未安装: pip install playwright && playwright install chromium")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=random.choice(USER_AGENTS))
            if self.cookie:
                for c in self.cookie.split(";"):
                    if "=" in c:
                        parts = c.split("=", 1)
                        ctx.add_cookies([{
                            "name": parts[0].strip(), "value": parts[1].strip(),
                            "domain": ".xiaoyuzhoufm.com", "path": "/"
                        }])
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            content = page.content()
            browser.close()
            return content

    def get_with_fallback(self, url: str) -> str:
        try:
            return self.get(url).text
        except Exception as e:
            logger.warning(f"切换 Playwright: {e}")
            return self._playwright_get(url)

    def fetch_podcast_info(self, pid: str) -> PodcastInfo:
        """获取播客名称 + 15 集 episode 列表 + 元数据（作者/订阅数/封面等）"""
        url = PODCAST_PAGE_URL.format(pid=pid)
        logger.info(f"获取播客页面: {url}")
        html = self.get_with_fallback(url)

        name = _extract_podcast_name(html) or f"播客_{pid}"
        meta = _extract_podcast_metadata(html)
        episodes = _extract_episodes_from_html(html)
        logger.info(f"获取到 {len(episodes)} 集")

        return PodcastInfo(
            pid=pid,
            name=name,
            author=meta["author"],
            description=meta["description"],
            cover_url=meta["cover_url"],
            subscriber_count=meta["subscriber_count"],
            episode_count=meta["episode_count"],
            episodes=episodes,
        )

    def fetch_episode_detail(self, eid: str, share_token: str = "") -> EpisodeInfo:
        """获取单集音频 URL"""
        data_url = EPISODE_DATA_URL.format(eid=eid)
        try:
            resp = self.get(data_url)
            if resp.status_code == 200:
                j = resp.json()
                ep_data = j.get("pageProps", {}).get("episodeDetail", {})
                if ep_data:
                    return self._parse_episode(ep_data)
        except Exception as e:
            logger.debug(f"JSON 接口失败: {e}")

        page_url = EPISODE_PAGE_URL.format(eid=eid)
        if share_token:
            page_url += f"?s={share_token}"
        html = self.get_with_fallback(page_url)

        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            try:
                data = json.loads(m.group(1).strip())
            except Exception:
                continue
            if data.get("@type") == "PodcastEpisode":
                return self._parse_episode(data)

        raise ValueError(f"无法解析 episode {eid}，页面结构可能已变更")

    def _parse_episode(self, data: dict) -> EpisodeInfo:
        desc = data.get("description") or ""
        is_paid = is_paid_episode(desc)
        paid_price = extract_paid_price(desc) if is_paid else None

        audio_url = ""
        assoc = data.get("associatedMedia")
        if isinstance(assoc, dict):
            audio_url = assoc.get("contentUrl") or ""
        if not audio_url:
            audio = data.get("audio", {})
            if isinstance(audio, dict):
                audio_url = audio.get("contentUrl") or ""

        pid = ""
        if audio_url:
            m = re.search(r"media\.xyzcdn\.net/([a-f0-9]+)/", audio_url)
            if m:
                pid = m.group(1)

        return EpisodeInfo(
            eid=self._eid(data.get("url") or ""),
            name=data.get("name") or data.get("title") or "",
            pub_date=data.get("datePublished") or data.get("pubDate") or "",
            duration=data.get("timeRequired") or data.get("duration") or "",
            description=desc,
            is_paid=is_paid,
            paid_price=paid_price,
            audio_url=audio_url,
            pid=pid,
        )

    @staticmethod
    def _eid(url: str) -> str:
        m = re.search(r"/episode/([a-f0-9]+)", url)
        return m.group(1) if m else ""


# --------------- 全局函数 ---------------

_scraper: Optional[Scraper] = None


def get_scraper(interval: int = DEFAULT_INTERVAL) -> Scraper:
    global _scraper
    if _scraper is None:
        _scraper = Scraper(interval=interval)
    else:
        _scraper.interval = interval
    return _scraper


def fetch_podcast_info(pid: str, interval: int = DEFAULT_INTERVAL) -> PodcastInfo:
    return get_scraper(interval=interval).fetch_podcast_info(pid)


def fetch_episode_info(eid: str, share_token: str = "", interval: int = DEFAULT_INTERVAL) -> EpisodeInfo:
    return get_scraper(interval=interval).fetch_episode_detail(eid, share_token=share_token)


def fetch_episodes_audio_info(episodes: list, max_workers: int = 3) -> list[dict]:
    """
    并行验证一批 episode 的音频 URL 并获取真实时长。
    输入：PodcastInfo.episodes 列表（每个元素有 .eid .name .pub_date .is_paid 等属性）
    返回：[{
        "eid", "name", "pub_date", "duration", "is_paid",
        "paid_price", "description", "has_audio"
    }]
    所有调用方共用此函数，保证刷新/订阅逻辑完全一致。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(ep):
        detail = fetch_episode_info(ep.eid, interval=1)
        return {
            "eid": ep.eid,
            "name": ep.name,
            "pub_date": ep.pub_date,
            "duration": detail.duration,
            "is_paid": ep.is_paid,
            "paid_price": getattr(ep, "paid_price", None),
            "description": getattr(ep, "description", ""),
            "has_audio": bool(detail.audio_url),
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, ep): ep for ep in episodes}
        results = []
        for future in as_completed(futures):
            results.append(future.result())
    return results


def set_cookie(cookie: str):
    save_cookie(cookie)
    global _scraper
    _scraper = None


def extract_pid(url: str) -> Optional[str]:
    m = re.search(r"(?:podcast|播客)[\/=]([a-f0-9]+)", url, re.IGNORECASE)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-f0-9]{16,24}", url):
        return url
    return None


# --------------- 工具函数 ---------------

def parse_duration_minutes(iso: str) -> int:
    if not iso:
        return 0
    m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso, re.IGNORECASE)
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) + (1 if int(m.group(3) or 0) > 0 else 0)


def format_duration(iso: str) -> str:
    mins = parse_duration_minutes(iso)
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    return f"{mins}min"


# --------------- 测试 ---------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    print("=== 测试1：忽左忽右（资讯早7点）===")
    pid = "67891a429407dcd1bfbfcfcb"
    info = fetch_podcast_info(pid, interval=3)
    print(f"播客名: {info.name}")
    print(f"总集数: {len(info.episodes)}")
    paid_count = sum(1 for e in info.episodes if e.is_paid)
    print(f"付费集: {paid_count}")
    for ep in info.episodes[:5]:
        tag = f" [需购买 {ep.paid_price}]" if ep.is_paid else " [免费]"
        dur = format_duration(ep.duration)
        print(f"  {ep.name[:40]:<40} | {ep.pub_date[:10]} | {dur:>6} |{tag}")

    print(f"\n请求次数: {get_scraper()._request_count}")

    print("\n=== 测试2：URL 提取 PID ===")
    for u in [
        "https://www.xiaoyuzhoufm.com/podcast/67891a429407dcd1bfbfcfcb",
        "https://www.xyzcdn.net/podcast/67891a429407dcd1bfbfcfcb",
        "67891a429407dcd1bfbfcfcb",
    ]:
        print(f"  {u} -> {extract_pid(u)}")

    print("\n=== 测试3：单集详情（含音频URL）===")
    ep = fetch_episode_info("69de4c4ab977fb2c47ef785e", interval=3)
    print(f"  名称: {ep.name}")
    print(f"  音频: {ep.audio_url[:70]}...")
    print(f"  付费: {ep.is_paid} {ep.paid_price or ''}")
