#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_feeds.py — 오늘의 뉴스지도 0단계(0-A): 수집원 생존 검증

설계 문서 v3 5장·13장 기준.
feeds_candidates.csv의 RSS 후보를 전수 확인하고
docs/live_feeds.md 리포트와 feeds_result.csv를 생성한다.

사용법:
    pip install feedparser requests
    python check_feeds.py                      # 같은 폴더의 feeds_candidates.csv 사용
    python check_feeds.py --csv my_feeds.csv   # 다른 후보 파일 지정
    python check_feeds.py --fresh-hours 48     # 신선도 기준 변경 (기본 24시간)

판정 기준 (v3 설계 문서):
    LIVE  : HTTP 200 + 파싱 성공 + 기사 1건 이상 + 최근 N시간 내 신규 기사
    STALE : 응답·파싱은 되지만 최근 N시간 내 신규 기사 없음 (방치된 피드)
    DEAD  : 접속 실패, 4xx/5xx, 또는 파싱 불가

통과 기준 (0-A 게이트):
    통신사(is_wire_service=true) LIVE 1곳 이상
    + 자체보도 매체(통신사 제외) LIVE 10곳 이상
"""

import argparse
import csv
import email.utils
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import feedparser
    import requests
except ImportError:
    sys.exit("필요 라이브러리가 없습니다. 먼저 실행하세요:  pip install feedparser requests")

KST = timezone(timedelta(hours=9))
HEADERS = {
    # 일부 언론사는 기본 UA를 차단하므로 브라우저형 UA 사용
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36 NewsMapFeedChecker/0.1"),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
TIMEOUT = 12


def parse_entry_time(entry):
    """entry에서 발행시각을 timezone-aware datetime으로 추출. 없으면 None."""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
            except (OverflowError, ValueError):
                continue
    # feedparser가 못 읽는 비표준 RFC822 날짜(쉼표 뒤 공백 누락 등) 보정 재시도
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                parsed = email.utils.parsedate_tz(raw.replace(",", ", ", 1))
                if parsed:
                    return datetime.fromtimestamp(email.utils.mktime_tz(parsed), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def check_one(url, fresh_hours):
    """단일 피드 검사. 결과 dict 반환."""
    result = {
        "http_status": None, "status": "DEAD", "entry_count": 0,
        "latest_kst": "", "fresh": False, "has_summary": False,
        "missing_fields": "", "error": "",
    }
    # 1) HTTP 응답 확인
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        result["http_status"] = resp.status_code
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result
        content = resp.content
    except requests.RequestException as e:
        result["error"] = f"접속실패: {type(e).__name__}"
        return result

    # 2) 파싱
    parsed = feedparser.parse(content)
    entries = parsed.entries or []
    result["entry_count"] = len(entries)
    if not entries:
        result["error"] = "파싱됨-기사 0건 (RSS 아님/빈 피드)"
        return result

    # 3) 필수 필드 확인 (제목/링크/발행시각) — 설계문서 0-A 기준
    sample = entries[0]
    missing = [f for f, k in (("제목", "title"), ("링크", "link")) if not sample.get(k)]
    times = [parse_entry_time(e) for e in entries]
    times = [t for t in times if t]
    if not times:
        missing.append("발행시각")
    result["missing_fields"] = ",".join(missing)

    # 4) 요약문(description/summary) 존재 여부 — 클러스터링 입력에 영향
    result["has_summary"] = bool(sample.get("summary") or sample.get("description"))

    # 5) 신선도: 최근 fresh_hours 이내 기사 존재 여부
    if times:
        latest = max(times)
        result["latest_kst"] = latest.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        result["fresh"] = (datetime.now(timezone.utc) - latest) <= timedelta(hours=fresh_hours)

    result["status"] = "LIVE" if (result["fresh"] and not missing) else "STALE"
    if missing:
        result["error"] = "필수필드 누락"
    return result


def main():
    ap = argparse.ArgumentParser(description="뉴스지도 수집원 생존 검증 (0단계 0-A)")
    ap.add_argument("--csv", default="feeds_candidates.csv", help="후보 목록 CSV 경로")
    ap.add_argument("--fresh-hours", type=int, default=24, help="신선도 기준 시간 (기본 24)")
    ap.add_argument("--out-dir", default="docs", help="리포트 출력 폴더 (기본 docs/)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"후보 파일이 없습니다: {csv_path}")

    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("후보 목록이 비어 있습니다.")

    print(f"수집원 {len(rows)}곳 검증 시작 (신선도 기준: 최근 {args.fresh_hours}시간)\n")
    results = []
    for i, row in enumerate(rows, 1):
        name, url = row["source_name"].strip(), row["rss_url"].strip()
        print(f"[{i:>2}/{len(rows)}] {name:<12} ... ", end="", flush=True)
        r = check_one(url, args.fresh_hours)
        mark = {"LIVE": "O LIVE", "STALE": "~ STALE", "DEAD": "X DEAD"}[r["status"]]
        extra = f" ({r['error']})" if r["error"] else f" (기사 {r['entry_count']}건, 최신 {r['latest_kst']})"
        print(mark + extra)
        results.append({**row, **r})
        time.sleep(0.5)  # 예의상 요청 간격

    # ---- 게이트 판정 (v3 13장 0-A 통과 기준) ----
    def is_true(v):
        return str(v).strip().lower() == "true"

    live = [r for r in results if r["status"] == "LIVE"]
    wire_live = [r for r in live if is_true(r["is_wire_service"])]
    own_live = [r for r in live if not is_true(r["is_wire_service"])]
    gate_pass = len(wire_live) >= 1 and len(own_live) >= 10
    no_summary = [r for r in live if not r["has_summary"]]

    # ---- 출력 파일 ----
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    # 1) feeds_result.csv
    result_csv = Path("feeds_result.csv")
    fieldnames = list(rows[0].keys()) + ["status", "http_status", "entry_count",
                                         "latest_kst", "fresh", "has_summary",
                                         "missing_fields", "error"]
    with open(result_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)

    # 2) docs/live_feeds.md
    md = [f"# 생존 피드 목록 (live_feeds.md)\n",
          f"검증 일시: {now_str} / 신선도 기준: 최근 {args.fresh_hours}시간\n",
          f"\n## 게이트 판정 (설계문서 v3, 13장 0-A)\n",
          f"- 통신사 LIVE: {len(wire_live)}곳 (기준 1곳 이상)",
          f"- 자체보도 매체 LIVE: {len(own_live)}곳 (기준 10곳 이상)",
          f"- **판정: {'통과 — 2단계(수집 구축) 진행 가능' if gate_pass else '미달 — 네이버 API/구글뉴스 RSS를 0.1로 앞당기는 설계 조정 필요 (v3 5장)'}**\n",
          "\n## LIVE 피드\n",
          "| 언론사 | 유형 | 통신사 | 요약문 | 최신 기사 | URL |",
          "|---|---|---|---|---|---|"]
    for r in sorted(live, key=lambda x: (not is_true(x["is_wire_service"]), x["source_type"])):
        md.append(f"| {r['source_name']} | {r['source_type']} | "
                  f"{'O' if is_true(r['is_wire_service']) else ''} | "
                  f"{'O' if r['has_summary'] else '없음'} | {r['latest_kst']} | {r['rss_url']} |")
    md += ["\n## STALE / DEAD 피드 (제외 대상)\n",
           "| 언론사 | 상태 | 사유 | URL |", "|---|---|---|---|"]
    for r in results:
        if r["status"] != "LIVE":
            md.append(f"| {r['source_name']} | {r['status']} | {r['error'] or '갱신 없음'} | {r['rss_url']} |")
    if no_summary:
        md += ["\n## 주의: 요약문 없는 LIVE 피드\n",
               "아래 피드는 description이 없어 클러스터링 임베딩 입력이 '제목만'이 된다.",
               "(v3 6-1장: 제목+요약 결합 임베딩 기준에 예외 처리 필요)\n"]
        md += [f"- {r['source_name']}" for r in no_summary]

    (out_dir / "live_feeds.md").write_text("\n".join(md), encoding="utf-8")

    # ---- 콘솔 요약 ----
    print("\n" + "=" * 60)
    print(f"결과: LIVE {len(live)} / STALE {sum(r['status']=='STALE' for r in results)}"
          f" / DEAD {sum(r['status']=='DEAD' for r in results)}")
    print(f"통신사 LIVE {len(wire_live)}곳, 자체보도 LIVE {len(own_live)}곳")
    print(f"게이트 판정: {'통과' if gate_pass else '미달 — 설계 조정 필요'}")
    print(f"리포트: {out_dir/'live_feeds.md'}, {result_csv}")


if __name__ == "__main__":
    main()
