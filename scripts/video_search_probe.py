#!/usr/bin/env python3
"""Probe keyword-related news videos for a possible keyword-tab side panel.

This is a local experiment only. It does not modify or feed the production
news map. The first probe target is YouTube search, constrained by broadcaster
names such as KBS, MBC, and JTBC.
"""

import argparse
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests


KST = timezone(timedelta(hours=9))
TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

SOURCES = [
    {"id": "kbs", "label": "KBS", "query_suffix": "KBS 뉴스"},
    {"id": "mbc", "label": "MBC", "query_suffix": "MBC뉴스"},
    {"id": "jtbc", "label": "JTBC", "query_suffix": "JTBC News"},
    {"id": "sbs", "label": "SBS", "query_suffix": "SBS 뉴스"},
    {"id": "ytn", "label": "YTN", "query_suffix": "YTN"},
]


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def console_text(value):
    return (value or "").encode("cp949", errors="replace").decode("cp949")


def find_balanced_json(text, marker="var ytInitialData = "):
    start = text.find(marker)
    if start < 0:
        marker = "ytInitialData = "
        start = text.find(marker)
    if start < 0:
        return None
    i = text.find("{", start + len(marker))
    if i < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for pos in range(i, len(text)):
        ch = text[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[i:pos + 1]
    return None


def text_runs(obj):
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if "simpleText" in obj:
        return obj.get("simpleText") or ""
    if "runs" in obj:
        return "".join(run.get("text", "") for run in obj.get("runs", []))
    return ""


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def parse_video_renderers(initial_data, source_label, max_results):
    videos = []
    seen = set()
    for node in walk(initial_data):
        renderer = node.get("videoRenderer") if isinstance(node, dict) else None
        if not renderer:
            continue
        video_id = renderer.get("videoId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = clean_text(text_runs(renderer.get("title")))
        owner = clean_text(text_runs(renderer.get("ownerText")) or text_runs(renderer.get("longBylineText")))
        thumbs = renderer.get("thumbnail", {}).get("thumbnails", [])
        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        videos.append({
            "source": source_label,
            "title": title,
            "channel": owner,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": thumb,
            "published": clean_text(text_runs(renderer.get("publishedTimeText"))),
            "views": clean_text(text_runs(renderer.get("viewCountText"))),
            "length": clean_text(text_runs(renderer.get("lengthText"))),
        })
        if len(videos) >= max_results:
            break
    return videos


def search_youtube(keyword, source, max_results):
    query = f'{keyword} {source["query_suffix"]}'
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    body = response.text
    raw = find_balanced_json(body)
    if not raw:
        return {
            "query": query,
            "error": "ytInitialData not found",
            "result_count": 0,
            "results": [],
        }
    data = json.loads(raw)
    videos = parse_video_renderers(data, source["label"], max_results=max_results)
    return {
        "query": query,
        "result_count": len(videos),
        "results": videos,
    }


def run_probe(keywords, max_results):
    output = {
        "generated_at": datetime.now(KST).isoformat(),
        "method": "YouTube HTML search parse; no official API key",
        "limitations": [
            "YouTube HTML structure can change without notice.",
            "Results are YouTube search ranking, not broadcaster editorial ranking.",
            "View counts are text snippets and may be missing or localized.",
        ],
        "keywords": [],
    }
    for keyword in keywords:
        entry = {"keyword": keyword, "sources": {}}
        for source in SOURCES:
            try:
                entry["sources"][source["id"]] = search_youtube(keyword, source, max_results=max_results)
            except Exception as exc:
                entry["sources"][source["id"]] = {
                    "query": f'{keyword} {source["query_suffix"]}',
                    "error": f"{type(exc).__name__}: {exc}",
                    "result_count": 0,
                    "results": [],
                }
            time.sleep(0.7)
        output["keywords"].append(entry)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keywords", nargs="*", default=["김민재", "행정안전부"])
    parser.add_argument("--max-results", type=int, default=3)
    parser.add_argument("--out", default="video_search_probe_results.json")
    args = parser.parse_args()

    result = run_probe(args.keywords, args.max_results)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out_path.resolve()}")
    for keyword in result["keywords"]:
        print(f"\n[{console_text(keyword['keyword'])}]")
        for source_id, data in keyword["sources"].items():
            status = data.get("error") or f"{data['result_count']} results"
            print(f"- {source_id}: {console_text(status)}")
            for row in data.get("results", [])[:2]:
                print(f"  {console_text(row['source'])}: {console_text(row['title'][:80])}")


if __name__ == "__main__":
    main()
