#!/usr/bin/env python3
"""
Build data/posts.json from posts/*.md.

Each Markdown file may start with a front matter block:

    ---
    title: 記事のタイトル
    date: 2026-10-19 14:30  # the time is optional ("2026-10-19" works too); always Japan time, never converted
    tags: [AI, 日記]        # or "AI, 日記"
    summary: 一覧に出す要約
    slug: my-post           # optional, defaults to the file name
    draft: true             # optional, skips the post
    ---

Without front matter the title is taken from the first "# heading" (or the file
name), the date/time from the file's last commit (or modification time, in Japan
time) and the summary from the first paragraph.

posts.json keeps "date" as YYYY-MM-DD (older pages rely on it) and adds "time" as HH:MM
("" when no time was given). Posts are sorted newest first by date, then time.

Standard library only, so the workflow does not need to install anything.
"""

import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "posts"
OUT = ROOT / "data" / "posts.json"
SUMMARY_LEN = 120
JST = timezone(timedelta(hours=9))

FRONT = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def parse_front(text: str):
    m = FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():]


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_tags(v) -> list:
    if not v:
        return []
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    tags = [unquote(t) for t in v.split(",")]
    return [t for t in (t.strip() for t in tags) if t]


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^\w\-]", "", name, flags=re.U)
    return name.strip("-") or "post"


def file_date(path: Path) -> tuple[str, str]:
    """(date, time) of the last commit that touched the file, in Japan time; falls back to the mtime."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(path)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if out:
            dt = datetime.fromisoformat(out).astimezone(JST)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        pass
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone(JST)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def parse_datetime(v: str) -> tuple[str, str] | None:
    """Accepts 2026-10-19 / 2026/10/19 / 2026.10.19, optionally followed by " 14:30" or "T14:30[:00]".
    Returns (YYYY-MM-DD, "HH:MM" or ""). An invalid time is ignored; an invalid date gives None."""
    m = re.match(r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]+(\d{1,2}):(\d{2}))?", unquote(v))
    if not m:
        return None
    try:
        date = datetime(int(m[1]), int(m[2]), int(m[3])).strftime("%Y-%m-%d")
    except ValueError:
        return None
    time = ""
    if m[4] is not None and int(m[4]) < 24 and int(m[5]) < 60:
        time = "%02d:%s" % (int(m[4]), m[5])
    return date, time


def make_summary(body: str) -> str:
    text = re.sub(r"```.*?```", "", body, flags=re.S)
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith(("#", ">", "|", "![", "---", "- ", "* ", "1.")):
            continue
        block = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", block)
        block = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", block)
        block = re.sub(r"[`*_~]", "", block)
        block = re.sub(r"\s+", " ", block).strip()
        if block:
            return block[:SUMMARY_LEN] + ("…" if len(block) > SUMMARY_LEN else "")
    return ""


def build_post(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = parse_front(text)
    if unquote(meta.get("draft", "")).lower() in ("true", "yes", "1"):
        return None

    body = body.strip("\n")
    title = unquote(meta.get("title", ""))
    if not title:
        m = re.match(r"\s*#\s+(.+)", body)
        if m:
            title = m.group(1).strip()
            body = body[m.end():].lstrip("\n")
    if not title:
        title = path.stem

    parsed = parse_datetime(meta["date"]) if meta.get("date") else None
    date, time = parsed if parsed else file_date(path)
    slug = slugify(unquote(meta.get("slug", "")) or path.stem)

    return {
        "id": slug,
        "slug": slug,
        "title": title,
        "date": date,
        "time": time,
        "tags": parse_tags(meta.get("tags")),
        # "summary:" written but left empty means "no summary"; leaving the key out auto-generates one.
        "summary": unquote(meta["summary"]) if "summary" in meta else make_summary(body),
        "content": body,
    }


def main() -> int:
    if not POSTS_DIR.is_dir():
        print(f"error: {POSTS_DIR} not found", file=sys.stderr)
        return 1

    posts, seen = [], {}
    for path in sorted(POSTS_DIR.rglob("*.md")):
        if path.name.startswith((".", "_")) or path.name.lower() == "readme.md":
            continue
        post = build_post(path)
        if post is None:
            print(f"skip (draft): {path.relative_to(ROOT)}")
            continue
        if post["slug"] in seen:
            print(f"error: duplicate slug '{post['slug']}': {path.relative_to(ROOT)} and {seen[post['slug']]}",
                  file=sys.stderr)
            return 1
        seen[post["slug"]] = path.relative_to(ROOT)
        posts.append(post)

    if not posts:
        # Never publish an empty blog because posts/ was emptied by mistake.
        print("error: no posts found in posts/ (refusing to overwrite data/posts.json)", file=sys.stderr)
        return 1

    posts.sort(key=lambda p: (p["date"], p["time"] or "00:00", p["slug"]), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"posts": posts}, ensure_ascii=False, indent=2) + "\n"
    OUT.write_text(data, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(posts)} posts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
