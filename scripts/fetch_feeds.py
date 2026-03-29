#!/usr/bin/env python3
"""
Fetch RSS feeds from Filmarks and 読書メーター, save as JSON.
Runs via GitHub Actions daily.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import feedparser
    import requests
except ImportError:
    print("Required packages not installed. Run: pip install feedparser requests")
    sys.exit(1)

# ===== Config =====
FILMARKS_USER = "VernrvSaki"
BOOKMETER_USER_ID = "1421871"

DATA_DIR = Path(__file__).parent.parent / "data"

# RSS feed URL candidates (try in order)
FILMARKS_FEED_URLS = [
    f"https://filmarks.com/users/{FILMARKS_USER}/feed",
    f"https://filmarks.com/users/{FILMARKS_USER}.rss",
    f"https://filmarks.com/users/{FILMARKS_USER}/feed.atom",
]

BOOKMETER_FEED_URLS = [
    f"https://bookmeter.com/users/{BOOKMETER_USER_ID}/feed",
    f"https://bookmeter.com/users/{BOOKMETER_USER_ID}.rss",
    f"https://api.bookmeter.com/v2/reports/read?user_id={BOOKMETER_USER_ID}&format=json",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; rnvneko-feed-bot/1.0; "
        "+https://github.com/rnvneko/rnvneko-info)"
    )
}

MAX_ITEMS = 20


def try_fetch_feed(urls: list[str]) -> feedparser.FeedParserDict | None:
    """Try each URL until one returns a valid feed."""
    for url in urls:
        print(f"  Trying: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                if feed.entries:
                    print(f"  OK: {len(feed.entries)} entries found")
                    return feed
                else:
                    print(f"  No entries found")
            else:
                print(f"  HTTP {resp.status_code}")
        except Exception as e:
            print(f"  Error: {e}")
    return None


def parse_date(entry) -> str:
    """Extract date string from a feed entry."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
    return ""


def extract_image(entry) -> str:
    """Try to extract a thumbnail/image URL from a feed entry."""
    # media:thumbnail
    for m in getattr(entry, "media_thumbnail", []):
        if m.get("url"):
            return m["url"]
    # enclosures
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href", "")
    # Look in summary/content HTML
    import re
    html = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    return ""


def extract_score(entry) -> str:
    """Try to extract a score/rating from an entry."""
    import re
    summary = entry.get("summary", "") or ""
    # Look for patterns like ★3.5 or 評価: 4.0
    m = re.search(r'[★☆]\s*([\d.]+)', summary)
    if m:
        return m.group(1)
    m = re.search(r'評価[：:\s]*([\d.]+)', summary)
    if m:
        return m.group(1)
    # star count (e.g. ★★★☆☆)
    filled = summary.count('★')
    if filled:
        return str(filled)
    return "0"


# ===== Filmarks =====
def fetch_filmarks() -> dict:
    print("\n[Filmarks]")
    feed = try_fetch_feed(FILMARKS_FEED_URLS)

    if not feed:
        print("  Could not fetch Filmarks feed. Keeping existing data.")
        existing = DATA_DIR / "filmarks.json"
        if existing.exists():
            return json.loads(existing.read_text("utf-8"))
        return {"updated": None, "reviews": []}

    reviews = []
    for entry in feed.entries[:MAX_ITEMS]:
        reviews.append({
            "title": entry.get("title", "").strip(),
            "score": extract_score(entry),
            "date": parse_date(entry),
            "comment": _strip_html(entry.get("summary", "")),
            "image": extract_image(entry),
            "url": entry.get("link", ""),
        })

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "reviews": reviews,
    }


# ===== 読書メーター =====
def fetch_bookmeter() -> dict:
    print("\n[読書メーター]")
    feed = try_fetch_feed(BOOKMETER_FEED_URLS)

    if not feed:
        print("  Could not fetch 読書メーター feed. Keeping existing data.")
        existing = DATA_DIR / "bookmeter.json"
        if existing.exists():
            return json.loads(existing.read_text("utf-8"))
        return {"updated": None, "reviews": []}

    reviews = []
    for entry in feed.entries[:MAX_ITEMS]:
        title = entry.get("title", "").strip()
        author = ""

        # 読書メーター feed titles are often "書名 / 著者名"
        if " / " in title:
            parts = title.split(" / ", 1)
            title = parts[0].strip()
            author = parts[1].strip()

        reviews.append({
            "title": title,
            "author": author,
            "score": extract_score(entry),
            "date": parse_date(entry),
            "comment": _strip_html(entry.get("summary", "")),
            "image": extract_image(entry),
            "url": entry.get("link", ""),
        })

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "reviews": reviews,
    }


def _strip_html(html: str) -> str:
    """Very basic HTML tag stripper."""
    import re
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return text.strip()


# ===== Main =====
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    filmarks_data = fetch_filmarks()
    bookmeter_data = fetch_bookmeter()

    filmarks_path = DATA_DIR / "filmarks.json"
    bookmeter_path = DATA_DIR / "bookmeter.json"

    filmarks_path.write_text(json.dumps(filmarks_data, ensure_ascii=False, indent=2), "utf-8")
    bookmeter_path.write_text(json.dumps(bookmeter_data, ensure_ascii=False, indent=2), "utf-8")

    print(f"\nSaved:")
    print(f"  filmarks.json  : {len(filmarks_data.get('reviews', []))} reviews")
    print(f"  bookmeter.json : {len(bookmeter_data.get('reviews', []))} reviews")


if __name__ == "__main__":
    main()
