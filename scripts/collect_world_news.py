#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_world_news.py — 해외뉴스 지도 수집 파이프라인 (overseas_news_design.txt 기준 1차 구현)

해외 RSS 수집 -> 정규화/중복 제거 -> 영어 단어 단위 TF-IDF 클러스터링
-> 국내뉴스와 같은 raw75 산식으로 점수 계산 -> 중요도 50점 이상만 한국어 번역
-> docs/world_news_map.json 출력

국내뉴스 파이프라인(collect_and_build.py)은 건드리지 않고, 공용 함수만 가져다 쓴다.
"""

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

sys.path.insert(0, str(Path(__file__).parent))
from collect_and_build import (
    HEADERS, TIMEOUT, KST, parse_entry_time, strip_html, dedupe_by_url,
    cluster_greedy, compute_batch_label,
)

CUTOFF_HOURS = 30  # 국내(24h)보다 여유를 둔다(11번: 시차 대응, overseas_news_design.txt 5장)
CLUSTER_THRESHOLD = 0.33
WORLD_SCORE_THRESHOLD = 50  # 이 점수 미만은 화면에 안 보여주고 번역도 하지 않는다

WORLD_SOURCES = [
    {"name": "BBC", "region": "유럽", "source_type": "국제방송",
     "rss": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "CNN", "region": "미국", "source_type": "방송사",
     "rss": "http://rss.cnn.com/rss/edition_world.rss"},
    {"name": "Al Jazeera", "region": "중동", "source_type": "국제방송",
     "rss": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "NPR", "region": "미국", "source_type": "공영라디오",
     "rss": "https://feeds.npr.org/1004/rss.xml"},
    {"name": "DW", "region": "유럽", "source_type": "국제방송",
     "rss": "https://rss.dw.com/xml/rss-en-all"},
    {"name": "France24", "region": "유럽", "source_type": "국제방송",
     "rss": "https://www.france24.com/en/rss"},
    {"name": "The Guardian", "region": "유럽", "source_type": "종합일간지",
     "rss": "https://www.theguardian.com/world/rss"},
    {"name": "New York Times", "region": "미국", "source_type": "종합일간지",
     "rss": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"},
    {"name": "SCMP", "region": "아시아", "source_type": "종합일간지",
     "rss": "https://www.scmp.com/rss/91/feed"},
]
BROADCAST_TYPES = {"국제방송", "방송사", "공영라디오"}
PAPER_TYPES = {"종합일간지"}

BREAKING_TIER3_EN = ["martial law", "declares war", "invasion", "state of emergency", "mass evacuation", "nuclear"]
BREAKING_TIER2_EN = ["breaking", "urgent", "live updates", "strike", "strikes", "attack", "explosion", "ceasefire"]
BREAKING_TIER1_EN = ["confirmed", "killed", "dead", "arrested", "resigns", "wounded", "injured"]

STOPWORDS_EN = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is", "are",
    "was", "were", "by", "with", "as", "its", "it", "that", "this", "after", "before",
    "from", "says", "said", "new", "over", "into", "not", "be", "will", "has", "have",
    "his", "her", "their", "amid", "than", "but", "how", "what", "who", "why",
}


def word_tokens(text):
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", (text or "").lower())
    return [w for w in text.split() if len(w) > 1 and w not in STOPWORDS_EN]


def word_ngrams(text, n_range=(1, 2)):
    tokens = word_tokens(text)
    grams = []
    for n in range(n_range[0], n_range[1] + 1):
        for i in range(len(tokens) - n + 1):
            grams.append(" ".join(tokens[i:i + n]))
    return grams


def build_tfidf_matrix_word(texts, min_df=1, max_vocab=8000):
    """국내뉴스는 문자 3~5-gram(build_tfidf_matrix)을 쓰지만, 영어 기사 제목은
    조사·어미가 없어 단어 1~2-gram이 더 적합하다(overseas_news_design.txt 9장)."""
    doc_grams = [word_ngrams(t) for t in texts]
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
    return X / norms


def fetch_world_source(source, cutoff):
    articles = []
    try:
        resp = requests.get(source["rss"], headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return source["name"], articles, f"HTTP {resp.status_code}"
        parsed = feedparser.parse(resp.content)
    except requests.RequestException as e:
        return source["name"], articles, f"접속실패:{type(e).__name__}"

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
            "title": html.unescape(title).strip(),
            "url": link.strip(),
            "source_name": source["name"],
            "source_type": source["source_type"],
            "region": source["region"],
            "summary": summary,
            "published_at_dt": pub,
        })
    return source["name"], articles, None


def compute_world_score(members, now):
    article_count = len(members)
    sources = {m["source_name"] for m in members}
    source_count = len(sources)

    # 국내뉴스는 통신사 기사가 그대로 복제돼 기사 수·매체 수가 쉽게 커지지만,
    # 해외 매체는 같은 사건도 매체마다 제목을 다르게 써서 3~4개 매체 corroboration도 드물다.
    # 그래서 국내뉴스 구간을 그대로 쓰면 만점 25점 항목조차 나오지 않아 50점 임계값을
    # 구조적으로 못 넘는다. 소스 풀 규모(9곳)에 맞게 구간을 낮춰 잡는다.
    if article_count <= 1:
        article_score = 5
    elif article_count == 2:
        article_score = 12
    elif article_count == 3:
        article_score = 18
    elif article_count <= 5:
        article_score = 22
    else:
        article_score = 25

    if source_count <= 1:
        diversity_score = 4
    elif source_count == 2:
        diversity_score = 10
    elif source_count == 3:
        diversity_score = 15
    else:
        diversity_score = 20

    source_types = {m["source_type"] for m in members}
    regions = {m["region"] for m in members}
    major_score = 0
    if source_types & BROADCAST_TYPES:
        major_score += 5
    if source_types & PAPER_TYPES:
        major_score += 5
    if len(regions) >= 2:  # 서로 다른 권역 매체가 같이 다루면 그만큼 국제적 파급력이 크다고 본다
        major_score += 5
    major_score = min(major_score, 15)

    combined_texts = [f"{m['title']} {m['summary']}".lower() for m in members]

    def hits(words):
        return sum(1 for t in combined_texts if any(w in t for w in words))

    # 표본이 작아(매체당 1~3건) 국내뉴스처럼 "2건 이상" 기준을 쓰면 거의 안 걸리므로 1건으로 완화
    if hits(BREAKING_TIER3_EN) >= 1:
        breaking_score = 9
    elif hits(BREAKING_TIER2_EN) >= 1:
        breaking_score = 7
    elif hits(BREAKING_TIER1_EN) >= 1:
        breaking_score = 4
    else:
        breaking_score = 1

    latest_dt = max(m["published_at_dt"] for m in members)
    age_h = (now - latest_dt).total_seconds() / 3600
    if age_h <= 3:
        time_score = 5
    elif age_h <= 6:
        time_score = 4
    elif age_h <= 12:
        time_score = 3
    elif age_h <= 24:
        time_score = 2
    else:
        time_score = 1

    raw75 = article_score + diversity_score + major_score + breaking_score + time_score
    return {
        "article_score": article_score, "diversity_score": diversity_score,
        "major_score": major_score, "breaking_score": breaking_score,
        "time_score": time_score, "raw75": raw75,
        "score100": round(raw75 * 4 / 3),
    }


def majority_region(members, rep):
    counts = Counter(m["region"] for m in members)
    top = counts.most_common(1)
    return top[0][0] if top else rep["region"]


_translator = None


def translate_to_ko(text, retries=1):
    """제목/인용 요약만 번역한다(대표 기사 단위, overseas_news_design.txt 6장 절약 원칙).
    무료 번역이라 실패할 수 있으므로 실패 시 원문을 그대로 쓰고 상태만 남긴다."""
    global _translator
    if not text:
        return text, "skipped"
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        return text, "skipped"
    if _translator is None:
        _translator = GoogleTranslator(source="en", target="ko")
    last_err = None
    for _ in range(retries + 1):
        try:
            return _translator.translate(text[:1000]), "ok"
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    print(f"번역 실패: {str(last_err)[:100]}")
    return text, "failed"


def main():
    scripts_dir = Path(__file__).parent
    project_root = scripts_dir.parent
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=CUTOFF_HOURS)

    all_articles = []
    print(f"해외 RSS {len(WORLD_SOURCES)}곳에서 수집 시작...")
    for i, source in enumerate(WORLD_SOURCES, 1):
        name, articles, err = fetch_world_source(source, cutoff)
        print(f"[{i:>2}/{len(WORLD_SOURCES)}] {name:<16} {'수집 ' + str(len(articles)) + '건' if not err else err}")
        all_articles.extend(articles)
        time.sleep(0.3)

    all_articles = dedupe_by_url(all_articles)
    print(f"\n중복 제거 후 총 기사 수: {len(all_articles)}건")

    if len(all_articles) < 5:
        sys.exit("해외 기사 수가 너무 적습니다. 수집 실패 가능성이 높습니다.")

    texts = [f"{a['title']} {a['summary']}" for a in all_articles]
    print("영어 단어 TF-IDF 벡터화 중...")
    X = build_tfidf_matrix_word(texts)
    print("클러스터링 중...")
    labels = cluster_greedy(X, threshold=CLUSTER_THRESHOLD)

    by_cluster = {}
    for idx, lbl in enumerate(labels):
        by_cluster.setdefault(int(lbl), []).append(idx)

    clusters_out = []
    for lbl, idxs in by_cluster.items():
        members = [all_articles[i] for i in idxs]
        rep = min(members, key=lambda m: m["published_at_dt"])
        latest_dt = max(m["published_at_dt"] for m in members)
        first_dt = min(m["published_at_dt"] for m in members)
        score = compute_world_score(members, now)
        excerpt_src = max(members, key=lambda m: len(m["summary"]))

        clusters_out.append({
            "id": lbl,
            "rep": rep,
            "excerpt_src": excerpt_src,
            "region": majority_region(members, rep),
            "score": score,
            "members": members,
            "first_dt": first_dt,
            "latest_dt": latest_dt,
        })

    clusters_out.sort(key=lambda c: c["score"]["score100"], reverse=True)
    shown = [c for c in clusters_out if c["score"]["score100"] >= WORLD_SCORE_THRESHOLD]
    print(f"이슈 {len(clusters_out)}개 중 {WORLD_SCORE_THRESHOLD}점 이상 {len(shown)}개 -> 번역 진행")

    clusters_json = []
    for c in shown:
        rep = c["rep"]
        excerpt_src = c["excerpt_src"]
        title_ko, title_status = translate_to_ko(rep["title"])
        excerpt_en = excerpt_src["summary"][:200]
        excerpt_ko, excerpt_status = translate_to_ko(excerpt_en) if excerpt_en else ("", "skipped")
        translation_status = "ok" if title_status == "ok" else title_status

        clusters_json.append({
            "id": f"world-{c['id']}",
            "title": title_ko,
            "title_en": rep["title"],
            "title_source": "earliest_pick",
            "title_url": rep["url"],
            "category": c["region"],
            "excerpt": excerpt_ko,
            "excerpt_en": excerpt_en,
            "excerpt_source": excerpt_src["source_name"] if excerpt_en else None,
            "excerpt_url": excerpt_src["url"] if excerpt_en else None,
            "translation_status": translation_status,
            "score": c["score"]["score100"],
            "score_breakdown": {
                "article_score": c["score"]["article_score"],
                "diversity_score": c["score"]["diversity_score"],
                "major_score": c["score"]["major_score"],
                "breaking_score": c["score"]["breaking_score"],
                "time_score": c["score"]["time_score"],
                "raw75": c["score"]["raw75"],
            },
            "area_value": len(c["members"]),
            "article_count": len(c["members"]),
            "syndicated_count": 0,  # 해외뉴스는 전재(통신사 복제) 판정을 하지 않는다
            "source_count": len({m["source_name"] for m in c["members"]}),
            "sources": sorted({m["source_name"] for m in c["members"]}),
            "first_published_at": c["first_dt"].astimezone(KST).isoformat(),
            "latest_published_at": c["latest_dt"].astimezone(KST).isoformat(),
            "articles": [
                {
                    "title": html.unescape(m["title"]),
                    "source_name": m["source_name"],
                    "url": m["url"],
                    "published_at": m["published_at_dt"].astimezone(KST).strftime("%H:%M"),
                }
                for m in sorted(c["members"], key=lambda m: m["published_at_dt"], reverse=True)
            ][:15],
        })

    now_kst = datetime.now(KST)
    batch_date, batch_time = compute_batch_label(now_kst)
    out = {
        "date": batch_date,
        "batch_time": batch_time,
        "updated_at": now_kst.isoformat(),
        "score_version": "world-0.1",
        "clustering_method": "tfidf_word1-2gram_greedy_centroid(en)",
        "translation_engine": "deep-translator(google)",
        "view_mode": "world",
        "source_count_total": len(WORLD_SOURCES),
        "article_count_total": len(all_articles),
        "cluster_count_total": len(clusters_out),
        "clusters": clusters_json,
    }

    out_path = project_root / "docs" / "world_news_map.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: 기사 {len(all_articles)}건 -> 이슈 {len(clusters_out)}개, {WORLD_SCORE_THRESHOLD}점 이상 {len(clusters_json)}개 번역/출력")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
