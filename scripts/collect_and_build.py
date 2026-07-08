#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_and_build.py — 오늘의 뉴스지도 0.1 파이프라인 (실데이터 1회 실행판)

RSS 수집 -> 정규화/중복 제거 -> 전재(통신사 복제) 판정 -> TF-IDF 기반 이슈 클러스터링
-> 0.1 산식 규칙 기반 점수 계산 -> news_map.json 출력

주의(정직한 스코프 고지):
- 설계문서 v3 6-1장은 "임베딩 기반 클러스터링"을 0.1에서도 유지하라고 명시하지만,
  이 스크립트는 즉시 실행 가능한 데모를 위해 문자 n-gram TF-IDF + 코사인 유사도
  기반 단일연결(single-linkage) 클러스터링으로 대체했다.
  (환경이 32bit Python이라 scikit-learn 사전빌드 wheel이 없어 numpy로 직접 구현)
  한국어 문장 임베딩 모델(sentence-transformers 등)로 교체하면
  클러스터링 품질(과분할/과병합)이 개선될 여지가 크다.
"""

import csv
import email.utils
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import numpy as np
import requests

KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36 NewsMapCollector/0.1"),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
TIMEOUT = 12
WIRE_NAMES = {"연합뉴스", "뉴시스", "뉴스1"}
WIRE_CREDIT_PATTERNS = [
    (re.compile(r"[=(]\s*연합뉴스"), "연합뉴스"),
    (re.compile(r"[=(]\s*뉴시스"), "뉴시스"),
    (re.compile(r"[=(]\s*뉴스1"), "뉴스1"),
]
BREAKING_TIER3 = ["계엄", "탄핵", "전쟁", "미사일", "대피", "경보"]
BREAKING_TIER2 = ["속보", "긴급", "특보"]
BREAKING_TIER1 = ["단독", "최초", "발표", "확정", "체포", "압수수색", "사망", "사고", "화재", "폭발", "금리"]

# 참고 화면의 카테고리 배지 표시용 키워드 추정 분류.
# 설계문서 v3 2-3장은 0.1 버전에서 카테고리 분류를 하지 않는다고 명시하므로,
# 이 분류는 점수/클러스터링에는 전혀 관여하지 않는 화면 표시 전용 태그다.
CATEGORY_KEYWORDS = [
    ("정치", ["대통령", "국회", "여야", "정당", "국무회의", "장관", "의원", "탄핵", "청와대", "총리", "당정", "여당", "야당"]),
    ("국제", ["미국", "중국", "일본", "러시아", "우크라이나", "나토", "정상회의", "트럼프", "외교", "튀르키예", "북한", "eu ", "유엔"]),
    ("경제", ["코스피", "금리", "환율", "증시", "수출", "물가", "기준금리", "투자", "채권", "코스닥", "무역"]),
    ("산업", ["삼성", "반도체", "완성차", "자동차", "배터리", "조선", "제조", "실적", "영업이익", "매출", "공장"]),
    ("사회", ["경찰", "검찰", "법원", "화재", "사고", "교육", "의료", "날씨", "장마", "태풍", "재판", "구속", "혐의"]),
    ("IT", ["ai", "인공지능", "플랫폼", "스타트업", "앱", "빅테크", "네이버", "카카오", "챗봇"]),
]


def guess_category(text):
    low = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS:
        if any(kw in low for kw in keywords):
            return cat
    return "기타"

DECOR_PATTERNS = [
    re.compile(r"^\s*\[[^\]]{1,10}\]\s*"),      # [속보] [단독] 등
    re.compile(r"\s*-\s*[가-힣A-Za-z0-9]{2,10}\s*$"),  # 끝의 " - 매체명"
    re.compile(r"[\"“”'‘’]"),
]


def normalize_title(title):
    t = html.unescape(title or "")
    for pat in DECOR_PATTERNS:
        t = pat.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
            except (OverflowError, ValueError):
                continue
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


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def load_sources(csv_path):
    with open(csv_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_true(v):
    return str(v).strip().lower() == "true"


def fetch_source_articles(row, cutoff):
    name = row["source_name"].strip()
    url = row["rss_url"].strip()
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return name, articles, f"HTTP {resp.status_code}"
        parsed = feedparser.parse(resp.content)
    except requests.RequestException as e:
        return name, articles, f"접속실패:{type(e).__name__}"

    for e in parsed.entries:
        title = e.get("title")
        link = e.get("link")
        if not title or not link:
            continue
        pub = parse_entry_time(e)
        if pub is None or pub < cutoff:
            continue
        summary = strip_html(e.get("summary") or e.get("description") or "")
        articles.append({
            "title": title.strip(),
            "norm_title": normalize_title(title),
            "url": link.strip(),
            "source_name": name,
            "source_type": row["source_type"],
            "is_wire_service": is_true(row["is_wire_service"]),
            "is_major_source": is_true(row["is_major_source"]),
            "published_at": pub.isoformat(),
            "published_at_dt": pub,
            "summary": summary,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        })
    return name, articles, None


def char_ngrams(text, n_range=(3, 5)):
    """짧은 2-gram은 한국어 조사·어미가 겹쳐 무관한 기사끼리 체인처럼 묶이는
    과병합을 유발하므로 제외하고 3~5-gram만 사용한다."""
    text = re.sub(r"\s+", " ", text).strip()
    grams = []
    for n in range(n_range[0], n_range[1] + 1):
        for i in range(len(text) - n + 1):
            grams.append(text[i:i + n])
    return grams


def build_tfidf_matrix(texts, min_df=2, max_vocab=8000):
    """sklearn 없이 문자 n-gram TF-IDF 벡터를 직접 계산 (L2 정규화된 dense numpy 행렬 반환)."""
    doc_grams = [char_ngrams(t) for t in texts]
    df = Counter()
    for grams in doc_grams:
        df.update(set(grams))
    vocab_items = [(g, c) for g, c in df.items() if c >= min_df]
    vocab_items.sort(key=lambda x: -x[1])
    vocab_items = vocab_items[:max_vocab]
    vocab = {g: i for i, (g, _) in enumerate(vocab_items)}

    n_docs = len(texts)
    n_vocab = len(vocab)
    X = np.zeros((n_docs, n_vocab), dtype=np.float32)
    idf = np.zeros(n_vocab, dtype=np.float32)
    for g, idx in vocab.items():
        idf[idx] = np.log((1 + n_docs) / (1 + df[g])) + 1.0

    for doc_idx, grams in enumerate(doc_grams):
        counts = Counter(grams)
        total = sum(counts.values()) or 1
        for g, c in counts.items():
            j = vocab.get(g)
            if j is not None:
                X[doc_idx, j] = (c / total) * idf[j]

    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X = X / norms
    return X


def cosine_sim_matrix(X):
    return np.clip(X @ X.T, -1.0, 1.0)


def cluster_greedy(X, threshold):
    """단일연결(single-linkage) 대신 클러스터 중심(centroid)과의 유사도로
    묶는 그리디 방식. 약한 연결로 무관한 기사가 체인처럼 딸려오는
    과병합(chaining)을 방지한다.
    """
    n = X.shape[0]
    labels = -np.ones(n, dtype=int)
    centroids = []  # 각 클러스터의 (합벡터, 문서수)
    for i in range(n):
        vec = X[i]
        best_lbl, best_sim = -1, -1.0
        for lbl, (csum, ccount) in enumerate(centroids):
            centroid = csum / ccount
            cnorm = np.linalg.norm(centroid)
            if cnorm == 0:
                continue
            sim = float(np.dot(vec, centroid) / cnorm)
            if sim > best_sim:
                best_sim, best_lbl = sim, lbl
        if best_sim >= threshold:
            labels[i] = best_lbl
            csum, ccount = centroids[best_lbl]
            centroids[best_lbl] = (csum + vec, ccount + 1)
        else:
            labels[i] = len(centroids)
            centroids.append((vec.copy(), 1))
    return labels


def dedupe_by_url(articles):
    seen = set()
    out = []
    for a in articles:
        h = hashlib.sha1(a["url"].encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append(a)
    return out


def compute_batch_label(now_kst):
    """GitHub Actions cron은 실행이 몇 분~몇십 분 늦어질 수 있으므로,
    화면에는 실제 실행 시각이 아니라 가장 가까운 목표 회차(06/12/18/00시)를
    고정 표시한다. 설계문서 2장은 00시 회차를 '전날 24:00'(방송 관례 표기)으로
    취급하라고 하지만, 사용자 피드백에 따라 "오늘 날짜 00:00"이라는 직관적인
    표기를 대신 사용한다(가리키는 실제 시점은 동일).
    """
    hour = now_kst.hour
    if 6 <= hour < 12:
        return now_kst.strftime("%Y-%m-%d"), "06:00"
    if 12 <= hour < 18:
        return now_kst.strftime("%Y-%m-%d"), "12:00"
    if 18 <= hour < 24:
        return now_kst.strftime("%Y-%m-%d"), "18:00"
    return now_kst.strftime("%Y-%m-%d"), "00:00"


def detect_wire_credit(article):
    text = f"{article['title']} {article['summary']}"
    for pat, wire in WIRE_CREDIT_PATTERNS:
        if pat.search(text) and article["source_name"] != wire:
            return wire
    return None


def load_keyword_config(path):
    if not path.exists():
        return {"window_hours": 48, "cluster_threshold": 0.36, "max_clusters_per_keyword": 20, "keyword_groups": []}
    return json.loads(path.read_text(encoding="utf-8"))


def keyword_text(article):
    return re.sub(
        r"\s+",
        " ",
        f"{article.get('title', '')} {article.get('norm_title', '')} {article.get('summary', '')}".lower(),
    )


def article_matches_keyword(article, group):
    text = keyword_text(article)
    aliases = [a.lower() for a in group.get("aliases", []) if a.strip()]
    if not aliases or not any(alias in text for alias in aliases):
        return False

    include_any = [v.lower() for v in group.get("include_any", []) if v.strip()]
    if include_any and not any(v in text for v in include_any):
        return False

    exclude_any = [v.lower() for v in group.get("exclude_any", []) if v.strip()]
    if exclude_any and any(v in text for v in exclude_any):
        return False

    return True


def keyword_focus_score(members, aliases, rep):
    aliases = [a.lower() for a in aliases if a.strip()]
    if not aliases:
        return 0
    title_hits = 0
    for m in members:
        title = f"{m.get('title', '')} {m.get('norm_title', '')}".lower()
        if any(alias in title for alias in aliases):
            title_hits += 1
    rep_title = f"{rep.get('title', '')} {rep.get('norm_title', '')}".lower()
    score = 0
    if any(alias in rep_title for alias in aliases):
        score += 2
    if title_hits >= max(1, len(members) / 2):
        score += 2
    if title_hits == len(members):
        score += 1
    return min(score, 5)


def compute_keyword_score(members, non_syn, now, aliases, rep):
    article_count = len(non_syn)
    source_count = len({m["source_name"] for m in non_syn})

    if article_count <= 1:
        article_score = 3
    elif article_count <= 3:
        article_score = 7
    elif article_count <= 6:
        article_score = 11
    elif article_count <= 10:
        article_score = 15
    else:
        article_score = 20

    if source_count <= 1:
        diversity_score = 3
    elif source_count == 2:
        diversity_score = 6
    elif source_count <= 4:
        diversity_score = 10
    elif source_count <= 7:
        diversity_score = 15
    else:
        diversity_score = 20

    major_score = 0
    source_types = {m["source_type"] for m in non_syn}
    if any(m["is_wire_service"] for m in non_syn):
        major_score += 3
    if "방송사" in source_types:
        major_score += 4
    if "종합일간지" in source_types:
        major_score += 4
    if "경제지" in source_types or "IT매체" in source_types:
        major_score += 2
    if len(source_types) >= 3:
        major_score += 2
    major_score = min(major_score, 15)

    def hits(words):
        return sum(1 for m in members if any(w in f"{m['title']} {m['summary']}" for w in words))
    if hits(BREAKING_TIER3) >= 2:
        breaking_score = 10
    elif hits(BREAKING_TIER2) >= 2:
        breaking_score = 7
    elif hits(BREAKING_TIER1) >= 1:
        breaking_score = 4
    else:
        breaking_score = 1

    latest_dt = max(m["published_at_dt"] for m in members)
    age_h = (now - latest_dt).total_seconds() / 3600
    if age_h <= 1:
        time_score = 10
    elif age_h <= 3:
        time_score = 8
    elif age_h <= 6:
        time_score = 6
    elif age_h <= 12:
        time_score = 4
    elif age_h <= 24:
        time_score = 2
    else:
        time_score = 1

    focus_score = keyword_focus_score(members, aliases, rep)
    raw80 = article_score + diversity_score + major_score + breaking_score + time_score + focus_score
    return {
        "article_score": article_score,
        "diversity_score": diversity_score,
        "major_score": major_score,
        "breaking_score": breaking_score,
        "time_score": time_score,
        "keyword_focus_score": focus_score,
        "raw80": raw80,
        "score100": round(raw80 * 100 / 80),
    }


def build_keyword_news_map(all_articles, live_source_count, batch_date, batch_time, now_kst, config):
    now = datetime.now(timezone.utc)
    window_hours = int(config.get("window_hours", 48))
    cutoff = now - timedelta(hours=window_hours)
    threshold = float(config.get("cluster_threshold", 0.36))
    max_clusters = int(config.get("max_clusters_per_keyword", 20))
    keyword_groups_out = []

    for group in config.get("keyword_groups", []):
        matched = [
            a for a in all_articles
            if a["published_at_dt"] >= cutoff and article_matches_keyword(a, group)
        ]
        matched = dedupe_by_url(matched)

        if len(matched) >= 2:
            texts = [f"{a['norm_title']} {a['summary']}" for a in matched]
            X = build_tfidf_matrix(texts, min_df=1)
            labels = cluster_greedy(X, threshold=threshold)
        else:
            labels = np.arange(len(matched), dtype=int)

        by_cluster = {}
        for idx, lbl in enumerate(labels):
            by_cluster.setdefault(int(lbl), []).append(idx)

        clusters_out = []
        for lbl, idxs in by_cluster.items():
            members = [matched[i] for i in idxs]
            non_syn = [m for m in members if not m["is_syndicated"]]
            if not non_syn:
                non_syn = members

            article_count = len(non_syn)
            sources = {m["source_name"] for m in non_syn}
            source_count = len(sources)
            syndicated_count = len(members) - len(non_syn)
            wire_members = [m for m in members if m["is_wire_service"]]
            pool = wire_members if wire_members else members
            rep = min(pool, key=lambda m: m["published_at_dt"])
            title_source = "wire_pick" if wire_members else "earliest_pick"
            latest_dt = max(m["published_at_dt"] for m in members)
            first_dt = min(m["published_at_dt"] for m in members)
            score = compute_keyword_score(members, non_syn, now, group.get("aliases", []), rep)
            category = guess_category(f"{rep['title']} {rep['summary']}")
            excerpt_src = max(members, key=lambda m: len(m["summary"]))
            excerpt = excerpt_src["summary"][:160] if excerpt_src["summary"] else ""

            clusters_out.append({
                "id": f"{group['id']}-{lbl}",
                "keyword_id": group["id"],
                "title": normalize_title(rep["title"]),
                "title_source": title_source,
                "title_url": rep["url"],
                "category": category,
                "excerpt": excerpt,
                "excerpt_source": excerpt_src["source_name"] if excerpt else None,
                "excerpt_url": excerpt_src["url"] if excerpt else None,
                "score": score["score100"],
                "score_breakdown": {
                    "article_score": score["article_score"],
                    "diversity_score": score["diversity_score"],
                    "major_score": score["major_score"],
                    "breaking_score": score["breaking_score"],
                    "time_score": score["time_score"],
                    "keyword_focus_score": score["keyword_focus_score"],
                    "raw80": score["raw80"],
                },
                "area_value": article_count,
                "article_count": article_count,
                "syndicated_count": syndicated_count,
                "source_count": source_count,
                "sources": sorted(sources),
                "first_published_at": first_dt.astimezone(KST).isoformat(),
                "latest_published_at": latest_dt.astimezone(KST).isoformat(),
                "articles": [
                    {
                        "title": html.unescape(m["title"]),
                        "source_name": m["source_name"],
                        "url": m["url"],
                        "published_at": m["published_at_dt"].astimezone(KST).strftime("%H:%M"),
                        "is_syndicated": m["is_syndicated"],
                    }
                    for m in sorted(members, key=lambda m: m["published_at_dt"])
                ][:15],
            })

        clusters_out.sort(key=lambda c: c["score"], reverse=True)
        clusters_out = clusters_out[:max_clusters]
        keyword_groups_out.append({
            "id": group["id"],
            "label": group["label"],
            "aliases": group.get("aliases", []),
            "matched_article_count": len(matched),
            "cluster_count_total": len(by_cluster),
            "clusters": clusters_out,
        })

    return {
        "date": batch_date,
        "batch_time": batch_time,
        "updated_at": now_kst.isoformat(),
        "score_version": "keyword-0.1",
        "clustering_method": "tfidf_char3-5gram_greedy_centroid(keyword)",
        "window_hours": window_hours,
        "source_count_total": live_source_count,
        "article_count_total": len(all_articles),
        "keyword_groups": keyword_groups_out,
    }


def main():
    scripts_dir = Path(__file__).parent
    project_root = scripts_dir.parent
    cache_path = scripts_dir / "articles_cache.json"
    from_cache = "--from-cache" in sys.argv

    if from_cache and cache_path.exists():
        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        for a in cache_data:
            a["published_at_dt"] = datetime.fromisoformat(a["published_at"])
        all_articles = cache_data
        live_source_count = len({a["source_name"] for a in all_articles})
        print(f"캐시에서 기사 {len(all_articles)}건 로드 (재수집 생략)")
    else:
        rows = load_sources(scripts_dir / "feeds_result.csv")
        live_rows = [r for r in rows if r.get("status") == "LIVE"]
        live_source_count = len(live_rows)
        print(f"LIVE 피드 {len(live_rows)}곳에서 수집 시작...")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        all_articles = []
        for i, row in enumerate(live_rows, 1):
            name, articles, err = fetch_source_articles(row, cutoff)
            print(f"[{i:>2}/{len(live_rows)}] {name:<10} {'수집 '+str(len(articles))+'건' if not err else err}")
            all_articles.extend(articles)
            time.sleep(0.3)

        all_articles = dedupe_by_url(all_articles)
        print(f"\n중복 제거 후 총 기사 수: {len(all_articles)}건")

        if len(all_articles) < 5:
            sys.exit("기사 수가 너무 적습니다. 수집 실패 가능성이 높습니다.")

        cache_data = [{k: v for k, v in a.items() if k != "published_at_dt"} for a in all_articles]
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
        print(f"원본 기사 캐시 저장: {cache_path} (--from-cache 옵션으로 재수집 없이 재실행 가능)")

    # ---- 전재(통신사 복제) 1차 판정: 크레딧 패턴 ----
    for a in all_articles:
        credit_wire = detect_wire_credit(a)
        a["is_syndicated"] = bool(credit_wire)
        a["credit_wire"] = credit_wire

    # ---- TF-IDF 임베딩(대용) 및 클러스터링 (sklearn 미가용 -> numpy로 직접 구현) ----
    texts = [f"{a['norm_title']} {a['summary']}" for a in all_articles]
    print("TF-IDF 벡터화 중...")
    X = build_tfidf_matrix(texts)
    print("클러스터링 중...")
    labels = cluster_greedy(X, threshold=0.30)
    sim = cosine_sim_matrix(X)

    for a, lbl in zip(all_articles, labels):
        a["cluster_label"] = int(lbl)

    # ---- 2차 전재 판정: 클러스터 내부 고유사도 + 통신사 원기사 존재 시 ----
    by_cluster = {}
    for idx, a in enumerate(all_articles):
        by_cluster.setdefault(a["cluster_label"], []).append(idx)

    for lbl, idxs in by_cluster.items():
        wire_idxs = [i for i in idxs if all_articles[i]["is_wire_service"]]
        if not wire_idxs:
            continue
        for i in idxs:
            if all_articles[i]["is_wire_service"] or all_articles[i]["is_syndicated"]:
                continue
            best = max(sim[i][w] for w in wire_idxs)
            if best >= 0.92:
                all_articles[i]["is_syndicated"] = True

    # ---- 클러스터별 집계 및 0.1 산식 점수 계산 ----
    now = datetime.now(timezone.utc)
    clusters_out = []
    for lbl, idxs in by_cluster.items():
        members = [all_articles[i] for i in idxs]
        non_syn = [m for m in members if not m["is_syndicated"]]
        if not non_syn:
            non_syn = members  # 전부 전재 판정이면 원본 취급으로 강등 방지
        article_count = len(non_syn)
        sources = {m["source_name"] for m in non_syn}
        source_count = len(sources)
        syndicated_count = len(members) - len(non_syn)

        # 대표 제목: 통신사 제목(1순위) 중 가장 이른 것, 없으면 전체 중 가장 이른 것
        wire_members = [m for m in members if m["is_wire_service"]]
        pool = wire_members if wire_members else members
        rep = min(pool, key=lambda m: m["published_at_dt"])
        title_source = "wire_pick" if wire_members else "earliest_pick"

        latest_dt = max(m["published_at_dt"] for m in members)
        first_dt = min(m["published_at_dt"] for m in members)

        # 기사량 점수 (최대 25)
        if article_count <= 2:
            article_score = 3
        elif article_count <= 5:
            article_score = 7
        elif article_count <= 10:
            article_score = 12
        elif article_count <= 20:
            article_score = 18
        else:
            article_score = 25

        # 언론사 다양성 점수 (최대 20)
        if source_count <= 1:
            diversity_score = 2
        elif source_count <= 3:
            diversity_score = 5
        elif source_count <= 7:
            diversity_score = 10
        elif source_count <= 14:
            diversity_score = 15
        else:
            diversity_score = 20

        # 주요 언론 보도 점수 (최대 15, 포털노출 항목은 데이터 없어 미부여)
        major_score = 0
        if any(m["is_wire_service"] for m in non_syn):
            major_score += 3
        if any(m["source_type"] == "방송사" for m in non_syn):
            major_score += 3
        if any(m["source_type"] == "종합일간지" for m in non_syn):
            major_score += 3
        if any(m["source_type"] == "경제지" for m in non_syn):
            major_score += 3

        # 속보성 점수 (최대 10) - 동일 이슈 내 2건 이상 반복 확인시만 상위 구간 인정
        combined = " ".join(f"{m['title']} {m['summary']}" for m in members)
        def hits(words):
            return sum(1 for m in members if any(w in f"{m['title']} {m['summary']}" for w in words))
        if hits(BREAKING_TIER3) >= 2:
            breaking_score = 9
        elif hits(BREAKING_TIER2) >= 2:
            breaking_score = 7
        elif hits(BREAKING_TIER1) >= 2:
            breaking_score = 4
        else:
            breaking_score = 1

        # 시간 가중치 점수 (최대 5) - latest_published_at 기준
        age_h = (now - latest_dt).total_seconds() / 3600
        if age_h <= 1:
            time_score = 5
        elif age_h <= 3:
            time_score = 4
        elif age_h <= 6:
            time_score = 3
        elif age_h <= 12:
            time_score = 2
        else:
            time_score = 1

        raw75 = article_score + diversity_score + major_score + breaking_score + time_score
        score100 = round(raw75 * 4 / 3)
        category = guess_category(f"{rep['title']} {rep['summary']}")
        excerpt_src = max(members, key=lambda m: len(m["summary"]))
        excerpt = excerpt_src["summary"][:160] if excerpt_src["summary"] else ""

        clusters_out.append({
            "id": lbl,
            "title": normalize_title(rep["title"]),
            "title_source": title_source,
            "title_url": rep["url"],
            "category": category,
            "excerpt": excerpt,
            "excerpt_source": excerpt_src["source_name"] if excerpt else None,
            "excerpt_url": excerpt_src["url"] if excerpt else None,
            "score": score100,
            "score_breakdown": {
                "article_score": article_score, "diversity_score": diversity_score,
                "major_score": major_score, "breaking_score": breaking_score,
                "time_score": time_score, "raw75": raw75,
            },
            "area_value": article_count,
            "article_count": article_count,
            "syndicated_count": syndicated_count,
            "source_count": source_count,
            "sources": sorted(sources),
            "first_published_at": first_dt.astimezone(KST).isoformat(),
            "latest_published_at": latest_dt.astimezone(KST).isoformat(),
            "articles": [
                {
                    "title": html.unescape(m["title"]), "source_name": m["source_name"],
                    "url": m["url"],
                    "published_at": m["published_at_dt"].astimezone(KST).strftime("%H:%M"),
                    "is_syndicated": m["is_syndicated"],
                }
                for m in sorted(members, key=lambda m: m["published_at_dt"])
            ][:15],
        })

    clusters_out.sort(key=lambda c: c["score"], reverse=True)
    top = clusters_out[:40]

    now_kst = datetime.now(KST)
    batch_date, batch_time = compute_batch_label(now_kst)
    out = {
        "date": batch_date,
        "batch_time": batch_time,
        "updated_at": now_kst.isoformat(),  # 실제 실행 시각(로그용). 화면 표시는 date+batch_time을 사용.
        "score_version": "0.1",
        "clustering_method": "tfidf_char3-5gram_greedy_centroid(demo)",
        "source_count_total": live_source_count,
        "article_count_total": len(all_articles),
        "cluster_count_total": len(clusters_out),
        "clusters": top,
    }

    out_path = project_root / "docs" / "news_map.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    keyword_config = load_keyword_config(scripts_dir / "keyword_config.json")
    keyword_out = build_keyword_news_map(all_articles, live_source_count, batch_date, batch_time, now_kst, keyword_config)
    keyword_out_path = project_root / "docs" / "keyword_news_map.json"
    keyword_out_path.write_text(json.dumps(keyword_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: 기사 {len(all_articles)}건 -> 이슈 {len(clusters_out)}개 (상위 {len(top)}개 출력)")
    print(f"저장: {out_path}")
    print(f"저장: {keyword_out_path}")


if __name__ == "__main__":
    main()
