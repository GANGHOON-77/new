// 오늘의 뉴스지도 0.1 - 등급별 고정 크기 그리드 렌더러

const SCORE_THRESHOLD = 50; // 이 점수 미만 이슈는 화면에서 제외
const KEYWORD_SCORE_THRESHOLD = 0; // 키워드 뉴스는 보도량이 적어 낮은 점수 이슈도 표시
const MAX_ITEMS = 20;       // 데스크톱 최대 노출 이슈 수
const MOBILE_MAX_ITEMS = 14; // 모바일은 화면이 좁아 박스가 너무 작아지지 않도록 더 적게 표시
const AREA_CAP_RATIO = 0.42; // 극단적으로 이슈가 적을 때만 작동하는 안전장치용 상한
const KEYWORD_AREA_CAP_RATIO = 0.24;
const CUSTOM_KEYWORD_STORAGE_KEY = 'news-map-custom-keywords';
const CUSTOM_KEYWORD_WINDOW_HOURS = 48;

// 면적 값 계산: 단순 점수가 아니라 (점수-40)^2를 써서 격차를 크게 벌린다.
// 예: 85점과 55점은 원점수로는 1.5배 차이지만 이 공식으로는 면적이 9배,
// 한 변 길이로는 약 3배 차이가 나서 "가장 중요한 기사"가 한눈에 띈다.
// 90점 이상 기사가 없어도 그 회차의 1위는 항상 상대적으로 확 커 보인다.
function areaValue(score) {
  return Math.pow(Math.max(score - 40, 5), 2);
}

function keywordAreaValue(item) {
  const scorePart = Math.max(item.score, 18);
  const articlePart = Math.min(Math.max(item.article_count || 1, 1), 6) * 7;
  return scorePart + articlePart;
}

// 재귀 이등분(recursive bisection) 트리맵. 항상 더 긴 변을 잘라 정사각형에
// 가까운 조각을 만들고, 빈 공간 없이 컨테이너 전체를 채운다.
// 입력 순서를 그대로 보존해 반환한다(그룹A 결과 -> 그룹B 결과 순).
function layoutTreemap(items, x, y, w, h, out) {
  if (items.length === 0 || w <= 0 || h <= 0) return;
  if (items.length === 1) {
    items[0].rect = { x, y, w, h };
    out.push(items[0]);
    return;
  }
  const total = items.reduce((s, i) => s + i.value, 0);
  if (total <= 0) {
    items.forEach(i => { i.rect = { x, y, w: 0, h: 0 }; out.push(i); });
    return;
  }
  const half = total / 2;
  let acc = 0, splitIdx = 1;
  for (let i = 0; i < items.length; i++) {
    acc += items[i].value;
    if (acc >= half) { splitIdx = i + 1; break; }
  }
  splitIdx = Math.min(Math.max(splitIdx, 1), items.length - 1);
  const groupA = items.slice(0, splitIdx);
  const groupB = items.slice(splitIdx);
  const ratioA = groupA.reduce((s, i) => s + i.value, 0) / total;

  if (w >= h) {
    const wA = w * ratioA;
    layoutTreemap(groupA, x, y, wA, h, out);
    layoutTreemap(groupB, x + wA, y, w - wA, h, out);
  } else {
    const hA = h * ratioA;
    layoutTreemap(groupA, x, y, w, hA, out);
    layoutTreemap(groupB, x, y + hA, w, h - hA, out);
  }
}

function squarify(items, x, y, w, h) {
  const out = [];
  layoutTreemap(items, x, y, w, h, out);
  return out;
}

// 모바일은 컨테이너 자체가 훨씬 좁아서 데스크톱 기준(px)을 그대로 쓰면
// 1위 박스조차 작은 글씨 등급에 묶여버린다. 화면 폭에 맞는 별도 기준을 쓴다.
function sizeClass(rect, mobile) {
  const shortSide = Math.min(rect.w, rect.h);
  const area = rect.w * rect.h;
  const t = mobile
    ? [{ cls: 'size-xl', side: 150, area: 24000 }, { cls: 'size-lg', side: 110, area: 12000 },
       { cls: 'size-md', side: 80, area: 6000 }, { cls: 'size-sm', side: 55, area: 0 }]
    : [{ cls: 'size-xl', side: 260, area: 130000 }, { cls: 'size-lg', side: 190, area: 65000 },
       { cls: 'size-md', side: 130, area: 30000 }, { cls: 'size-sm', side: 90, area: 0 }];
  for (const tier of t) {
    if (shortSide >= tier.side && area >= tier.area) return tier.cls;
  }
  return 'size-xs';
}

// 분야별 고정 색상 (요청에 따라 점수가 아닌 카테고리로 색상 구분)
// 정치=빨강/파랑은 한국에서 특정 정당 색으로 강하게 읽혀 편향 오해 소지가 있어
// 정치는 중립적인 회색으로, 경제는 빨강으로 지정(사용자 지정). 나머지는 정치색과
// 무관한 색상 중 서로 뚜렷이 구분되는 색으로 배정.
const CATEGORY_COLORS = {
  '정치': '#f5a623',
  '경제': '#c0392b',
  '사회': '#1a9e7c',
  '국제': '#7d3c98',
  '산업': '#d2691e',
  'IT':   '#2470a0',
  '기타': '#a89f91',
};

function categoryColor(category) {
  return CATEGORY_COLORS[category] || CATEGORY_COLORS['기타'];
}

function fmtTime(isoStr) {
  const d = new Date(isoStr);
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtDateTime(isoStr) {
  const d = new Date(isoStr);
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}

function timeAgoLabel(isoStr) {
  const diffMin = Math.round((new Date() - new Date(isoStr)) / 60000);
  if (diffMin < 60) return `${diffMin}분 전`;
  return `${Math.floor(diffMin / 60)}시간 전`;
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

let DATA = null;
let DOMESTIC_DATA = null;
let KEYWORD_DATA = null;
let currentMode = 'domestic';
let selectedKeywordId = null;
let customKeywords = [];
let selectedId = null;
let lastViewportWidth = window.innerWidth;
let mobileTreemapHeight = null;
let mobileTreemapWidth = null;

function setTreemapHeight(el, mobile) {
  const footerH = document.querySelector('.footer').offsetHeight;
  const pageTop = el.getBoundingClientRect().top + window.scrollY;
  const nextHeight = Math.max(window.innerHeight - pageTop - footerH - 20, 320);

  if (mobile) {
    const width = el.clientWidth;
    if (mobileTreemapHeight === null || mobileTreemapWidth !== width) {
      mobileTreemapHeight = nextHeight;
      mobileTreemapWidth = width;
    }
    el.style.height = mobileTreemapHeight + 'px';
    return;
  }

  el.style.height = nextHeight + 'px';
}

function resetRenderState() {
  selectedId = null;
  mobileTreemapHeight = null;
  mobileTreemapWidth = null;
}

function renderCurrent() {
  if (currentMode === 'keyword') {
    render(buildCustomKeywordViewData());
    return;
  }
  if (DOMESTIC_DATA) render(DOMESTIC_DATA);
}

function normalizeKeywordValue(value) {
  return value.trim().replace(/\s+/g, ' ');
}

function loadCustomKeywords() {
  try {
    const saved = JSON.parse(localStorage.getItem(CUSTOM_KEYWORD_STORAGE_KEY) || '[]');
    customKeywords = Array.isArray(saved) ? saved.map(normalizeKeywordValue).filter(Boolean) : [];
  } catch (_) {
    customKeywords = [];
  }
}

function saveCustomKeywords() {
  localStorage.setItem(CUSTOM_KEYWORD_STORAGE_KEY, JSON.stringify(customKeywords));
}

function addCustomKeyword(value) {
  const keyword = normalizeKeywordValue(value);
  if (!keyword) return;
  const exists = customKeywords.some(k => k.toLowerCase() === keyword.toLowerCase());
  if (!exists) {
    customKeywords.push(keyword);
    saveCustomKeywords();
  }
  document.getElementById('keyword-input').value = '';
  resetRenderState();
  renderKeywordChips();
  renderCurrent();
}

function removeCustomKeyword(keyword) {
  customKeywords = customKeywords.filter(k => k !== keyword);
  saveCustomKeywords();
  resetRenderState();
  renderKeywordChips();
  renderCurrent();
}

function clearCustomKeywords() {
  customKeywords = [];
  saveCustomKeywords();
  resetRenderState();
  renderKeywordChips();
  renderCurrent();
}

function renderKeywordChips() {
  const wrap = document.getElementById('keyword-chips');
  wrap.innerHTML = customKeywords.map(keyword => `
    <button class="keyword-chip" type="button" data-keyword="${escapeHtml(keyword)}" title="키워드 삭제">
      <span>${escapeHtml(keyword)}</span><span class="remove">×</span>
    </button>
  `).join('');
  wrap.querySelectorAll('.keyword-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      removeCustomKeyword(btn.dataset.keyword);
    });
  });
}

function keywordMatchText(article) {
  return `${article.title || ''} ${article.norm_title || ''} ${article.summary || ''}`.toLowerCase();
}

function articleMatchesCustomKeywords(article) {
  const text = keywordMatchText(article);
  return customKeywords.some(keyword => text.includes(keyword.toLowerCase()));
}

function titleGrams(text) {
  const compact = (text || '').toLowerCase().replace(/\s+/g, '');
  const grams = new Set();
  for (let i = 0; i < compact.length - 2; i += 1) grams.add(compact.slice(i, i + 3));
  return grams;
}

function titleSimilarity(a, b) {
  const ga = titleGrams(a);
  const gb = titleGrams(b);
  if (ga.size === 0 || gb.size === 0) return 0;
  let inter = 0;
  ga.forEach(v => { if (gb.has(v)) inter += 1; });
  return inter / Math.min(ga.size, gb.size);
}

function clusterCustomKeywordArticles(articles) {
  const clusters = [];
  articles.forEach(article => {
    let best = null;
    let bestScore = 0;
    clusters.forEach(cluster => {
      const score = titleSimilarity(article.norm_title || article.title, cluster.rep.norm_title || cluster.rep.title);
      if (score > bestScore) {
        bestScore = score;
        best = cluster;
      }
    });
    if (best && bestScore >= 0.45) {
      best.members.push(article);
    } else {
      clusters.push({ rep: article, members: [article] });
    }
  });
  return clusters;
}

function customKeywordScore(members, nonSyn, latestDate) {
  const articleCount = nonSyn.length;
  const sources = new Set(nonSyn.map(a => a.source_name));
  const sourceTypes = new Set(nonSyn.map(a => a.source_type));
  const articleScore = articleCount <= 1 ? 3 : articleCount <= 3 ? 7 : articleCount <= 6 ? 11 : articleCount <= 10 ? 15 : 20;
  const diversityScore = sources.size <= 1 ? 3 : sources.size === 2 ? 6 : sources.size <= 4 ? 10 : sources.size <= 7 ? 15 : 20;
  let majorScore = 0;
  if (nonSyn.some(a => a.is_wire_service)) majorScore += 3;
  if (sourceTypes.has('방송사')) majorScore += 4;
  if (sourceTypes.has('종합일간지')) majorScore += 4;
  if (sourceTypes.has('경제지') || sourceTypes.has('IT매체')) majorScore += 2;
  if (sourceTypes.size >= 3) majorScore += 2;
  majorScore = Math.min(majorScore, 15);

  const breakingWords = ['속보', '단독', '최초', '긴급', '발표', '확정', '체포', '압수수색'];
  const breakingHits = members.filter(a => breakingWords.some(w => `${a.title} ${a.summary}`.includes(w))).length;
  const breakingScore = breakingHits >= 2 ? 8 : breakingHits === 1 ? 4 : 1;
  const ageH = (Date.now() - latestDate.getTime()) / 3600000;
  const timeScore = ageH <= 1 ? 10 : ageH <= 3 ? 8 : ageH <= 6 ? 6 : ageH <= 12 ? 4 : ageH <= 24 ? 2 : 1;
  const focusScore = 5;
  const raw80 = articleScore + diversityScore + majorScore + breakingScore + timeScore + focusScore;
  return {
    article_score: articleScore,
    diversity_score: diversityScore,
    major_score: majorScore,
    breaking_score: breakingScore,
    time_score: timeScore,
    keyword_focus_score: focusScore,
    raw80,
    score100: Math.round(raw80 * 100 / 80),
  };
}

function buildCustomKeywordViewData() {
  const base = DOMESTIC_DATA || {};
  const articles = base.public_articles || [];
  if (customKeywords.length === 0) {
    return {
      date: base.date,
      batch_time: base.batch_time,
      source_count_total: base.source_count_total || 0,
      article_count_total: 0,
      cluster_count_total: 0,
      clusters: [],
      view_mode: 'keyword',
      keyword_label: '키워드 미설정',
      window_hours: CUSTOM_KEYWORD_WINDOW_HOURS,
    };
  }

  const cutoff = Date.now() - CUSTOM_KEYWORD_WINDOW_HOURS * 3600000;
  const matched = articles
    .filter(article => new Date(article.published_at).getTime() >= cutoff)
    .filter(articleMatchesCustomKeywords)
    .sort((a, b) => new Date(a.published_at) - new Date(b.published_at));
  const rawClusters = clusterCustomKeywordArticles(matched);
  const clusters = rawClusters.map((cluster, idx) => {
    const members = cluster.members;
    const nonSyn = members.filter(m => !m.is_syndicated);
    const scoredMembers = nonSyn.length ? nonSyn : members;
    const sources = [...new Set(scoredMembers.map(m => m.source_name))].sort();
    const rep = members.find(m => m.is_wire_service) || members[0];
    const firstDate = new Date(Math.min(...members.map(m => new Date(m.published_at).getTime())));
    const latestDate = new Date(Math.max(...members.map(m => new Date(m.published_at).getTime())));
    const score = customKeywordScore(members, scoredMembers, latestDate);
    const excerptSrc = members.reduce((best, item) => (item.summary || '').length > (best.summary || '').length ? item : best, members[0]);
    return {
      id: `custom-${idx}`,
      title: rep.norm_title || rep.title,
      title_source: rep.is_wire_service ? 'wire_pick' : 'earliest_pick',
      title_url: rep.url,
      category: rep.category || '기타',
      excerpt: excerptSrc.summary || '',
      excerpt_source: excerptSrc.source_name,
      excerpt_url: excerptSrc.url,
      score: score.score100,
      score_breakdown: {
        article_score: score.article_score,
        diversity_score: score.diversity_score,
        major_score: score.major_score,
        breaking_score: score.breaking_score,
        time_score: score.time_score,
        keyword_focus_score: score.keyword_focus_score,
        raw80: score.raw80,
      },
      area_value: scoredMembers.length,
      article_count: scoredMembers.length,
      syndicated_count: members.length - scoredMembers.length,
      source_count: sources.length,
      sources,
      first_published_at: firstDate.toISOString(),
      latest_published_at: latestDate.toISOString(),
      articles: members
        .slice()
        .sort((a, b) => new Date(a.published_at) - new Date(b.published_at))
        .slice(0, 15)
        .map(a => ({
          title: a.title,
          source_name: a.source_name,
          url: a.url,
          published_at: a.published_time || fmtTime(a.published_at),
          is_syndicated: a.is_syndicated,
        })),
    };
  }).sort((a, b) => b.score - a.score);

  return {
    date: base.date,
    batch_time: base.batch_time,
    source_count_total: base.source_count_total || 0,
    article_count_total: matched.length,
    cluster_count_total: clusters.length,
    clusters,
    view_mode: 'keyword',
    keyword_label: customKeywords.join(' 또는 '),
    window_hours: CUSTOM_KEYWORD_WINDOW_HOURS,
  };
}

function setMode(mode) {
  currentMode = mode;
  document.getElementById('domestic-tab').classList.toggle('active', mode === 'domestic');
  document.getElementById('keyword-tab').classList.toggle('active', mode === 'keyword');
  document.getElementById('keyword-toolbar').classList.toggle('hidden', mode !== 'keyword');
  resetRenderState();
  renderCurrent();
}

function render(data) {
  DATA = data;
  document.getElementById('today-date').textContent =
    new Date(data.date + 'T00:00:00+09:00').toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
  // GitHub Actions cron 실행 지연과 무관하게 목표 회차 시각(06/12/18/24시)을 그대로 표시한다.
  document.getElementById('updated-at').textContent = `업데이트: ${data.batch_time} 기준`;

  const isMobile = window.innerWidth < 640;
  const limit = isMobile ? MOBILE_MAX_ITEMS : MAX_ITEMS;
  const scoreThreshold = data.view_mode === 'keyword' ? KEYWORD_SCORE_THRESHOLD : SCORE_THRESHOLD;
  const items = data.clusters.filter(c => c.score >= scoreThreshold).slice(0, limit);

  document.getElementById('meta-line').textContent = data.view_mode === 'keyword'
    ? `키워드: ${data.keyword_label} · 최근 ${data.window_hours}시간 매칭 기사 ${data.article_count_total}건 · 이슈 ${data.cluster_count_total}개 중 ${items.length}개 표시`
    : `수집원 ${data.source_count_total}곳 · 수집 기사 ${data.article_count_total}건 · 이슈 ${data.cluster_count_total}개 중 중요도 ${SCORE_THRESHOLD}점 이상 ${items.length}개 표시`;

  // 상세 패널이 뜰지 여부를 트리맵 폭을 재기 "전"에 먼저 확정한다.
  // 그렇지 않으면 패널이 없는 상태의 폭으로 박스를 배치한 뒤 패널이 나타나면서
  // 폭이 줄어들어, 이미 배치된 박스들이 패널 위에 겹쳐 보이는 문제가 생긴다.
  const panel = document.getElementById('detail-panel');
  if (items.length > 0) {
    panel.classList.remove('hidden');
  } else {
    panel.classList.add('hidden');
  }

  const el = document.getElementById('treemap');
  el.classList.remove('mobile-list');
  el.innerHTML = '';

  if (items.length === 0) {
    panel.classList.add('hidden');
    const emptyMessage = data.view_mode === 'keyword' && customKeywords.length === 0
      ? '키워드를 추가하면 최근 수집 기사에서 OR 조건으로 검색합니다.'
      : '표시할 뉴스가 없습니다.';
    el.innerHTML = `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
    return;
  }

  // 모바일은 상단 버튼·범례가 줄바꿈되어 헤더 높이가 유동적이므로,
  // CSS 고정값 대신 실제 위치를 기준으로 남은 높이를 계산한다.
  setTreemapHeight(el, isMobile);

  // 면적 값 = 중요도 점수 기반. 1위가 화면을 과점하지 않도록 상한을 둔다(11-1장).
  const isKeywordView = data.view_mode === 'keyword';
  let sized = items.map(c => ({ ...c, value: isKeywordView ? keywordAreaValue(c) : areaValue(c.score) }));
  const total = sized.reduce((s, i) => s + i.value, 0);
  const cap = total * (isKeywordView ? KEYWORD_AREA_CAP_RATIO : AREA_CAP_RATIO);
  sized = sized.map(i => i.value > cap ? { ...i, value: cap } : i);

  const w = el.clientWidth, h = el.clientHeight;
  const laid = squarify(sized, 0, 0, w, h);

  laid.forEach((item, idx) => {
    const cls = sizeClass(item.rect, isMobile);
    const div = buildCell(item, idx + 1, cls);
    div.style.left = item.rect.x + 'px';
    div.style.top = item.rect.y + 'px';
    div.style.width = Math.max(item.rect.w - 3, 0) + 'px';
    div.style.height = Math.max(item.rect.h - 3, 0) + 'px';
    div.style.background = categoryColor(item.category);
    el.appendChild(div);
  });

  // 클릭 없이도 바로 보이도록 기본으로 1위 이슈 상세를 띄운다.
  if (selectedId === null && items.length > 0) {
    selectedId = items[0].id;
    renderDetail(items[0].id, 1);
  } else if (selectedId !== null) {
    const stillShown = items.find(x => x.id === selectedId);
    if (stillShown) renderDetail(selectedId, items.indexOf(stillShown) + 1);
  }
}

function buildCell(item, rank, sizeCls) {
  const div = document.createElement('div');
  div.className = `cell ${sizeCls}` + (item.id === selectedId ? ' selected' : '');
  const showDesc = (sizeCls === 'size-xl' || sizeCls === 'size-lg' || sizeCls === 'size-md') && item.excerpt;
  div.innerHTML = `
    <div class="badge-row">
      <span class="rank">${rank}</span>
      <span class="category">${escapeHtml(item.category)}</span>
    </div>
    <div class="title">${escapeHtml(item.title)}</div>
    ${showDesc ? `<div class="desc">${escapeHtml(item.excerpt)}</div>` : ''}
    <div class="spacer"></div>
    <div class="score-line">중요도 ${item.score}점</div>
    <div class="stats-line">기사 ${item.article_count}건 · 언론사 ${item.source_count}곳 · ${fmtTime(item.latest_published_at)} 업데이트</div>
  `;
  div.addEventListener('click', () => selectCluster(item.id));
  return div;
}

function selectCluster(id) {
  selectedId = id;
  render(DATA);
}

function renderDetail(id, rank) {
  const c = DATA.clusters.find(x => x.id === id);
  const panel = document.getElementById('detail-panel');
  panel.classList.remove('hidden');
  const b = c.score_breakdown;
  const isKeyword = DATA.view_mode === 'keyword';
  const maxes = isKeyword
    ? { article_score: 20, diversity_score: 20, major_score: 15, breaking_score: 10, time_score: 10, keyword_focus_score: 5 }
    : { article_score: 25, diversity_score: 20, major_score: 15, breaking_score: 10, time_score: 5 };
  const labels = isKeyword
    ? { article_score: '기사량(전재 제외)', diversity_score: '언론사 다양성', major_score: '주요 언론 보도', breaking_score: '속보성·긴급성', time_score: '시간 가중치', keyword_focus_score: '키워드 집중도' }
    : { article_score: '기사량(전재 제외)', diversity_score: '언론사 다양성', major_score: '주요 언론 보도', breaking_score: '속보성·긴급성', time_score: '시간 가중치' };
  const shown = c.articles.slice(0, 10);

  panel.innerHTML = `
    <button class="close-btn" id="close-detail">×</button>
    <span class="top-badge">${rank} ${escapeHtml(c.category)}</span>
    <h2>${c.title_url ? `<a href="${c.title_url}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>` : escapeHtml(c.title)}</h2>
    <div class="time-row">🕐 최초 보도: ${fmtDateTime(c.first_published_at)} · 최근 보도: ${fmtDateTime(c.latest_published_at)}</div>
    <div class="title-source-note">대표 제목: ${c.title_source === 'wire_pick' ? '통신사 제목 선택(1순위 규칙)' : '이슈 내 최이른 기사 제목'}${isKeyword ? ` · 키워드: ${escapeHtml(DATA.keyword_label)}` : ''} (제목 클릭 시 원문으로 이동)</div>
    <span class="summary-label">인용 요약 (0.1버전: 대표 기사 발췌 인용 · AI 요약은 1.0버전부터)</span>
    <div class="summary">${escapeHtml(c.excerpt || '요약문을 제공하는 기사가 없습니다.')}${c.excerpt_source ? ` — ${c.excerpt_url ? `<a href="${c.excerpt_url}" target="_blank" rel="noopener" style="color:#999">${escapeHtml(c.excerpt_source)}</a>` : `<span style="color:#999">${escapeHtml(c.excerpt_source)}</span>`}` : ''}</div>
    <div class="score-box">
      <div class="score-num">${c.score}<span style="font-size:14px;color:#999">/100</span></div>
      ${Object.keys(labels).map(k => `
        <div class="score-row">
          <span class="label">${labels[k]}</span>
          <span class="bar-bg"><span class="bar-fg" style="width:${(((b[k] || 0) / maxes[k]) * 100).toFixed(0)}%"></span></span>
          <span class="val">${b[k] || 0}/${maxes[k]}</span>
        </div>
      `).join('')}
    </div>
    <div style="font-size:12px;color:#777">
      기사 ${c.article_count}건(전재 ${c.syndicated_count}건 제외) · 언론사 ${c.source_count}곳 · (${timeAgoLabel(c.latest_published_at)} 최근 보도)
    </div>
    <h3>관련 기사 <span class="see-all">${c.articles.length > 10 ? `상위 10건 표시 (총 ${c.article_count + c.syndicated_count}건 수집)` : `${c.articles.length}건`}</span></h3>
    ${shown.map(a => `
      <div class="article-item ${a.is_syndicated ? 'syn' : ''}">
        <span class="src">${escapeHtml(a.source_name)}</span>
        <a href="${a.url}" target="_blank" rel="noopener">${escapeHtml(a.title)}</a>
        <span class="time">${a.published_at}${a.is_syndicated ? ' <span class="syn-tag">전재</span>' : ''}</span>
      </div>
    `).join('')}
    <h3>출처 언론사 (${c.sources.length}곳)</h3>
    <div class="sources-list">${c.sources.map(s => `<span class="source-pill ${['연합뉴스', '뉴시스', '뉴스1'].includes(s) ? 'wire' : ''}">${escapeHtml(s)}</span>`).join('')}</div>
  `;
  document.getElementById('close-detail').addEventListener('click', () => {
    selectedId = null;
    render(DATA);
  });
}

const ALGO_HTML = `
  <section>
    <h4>1. 중요도 점수 산식 (0.1버전, 75점 만점 -&gt; 100점 환산)</h4>
    기사량(전재 제외) 25점 + 언론사 다양성 20점 + 주요 언론 보도 15점 + 속보성·긴급성 10점 + 시간 가중치 5점 = 75점,
    표시 점수 = 75점 환산값 × 4/3 (반올림). LLM 평가(사회·경제 파급력, 검색·관심도)는 1.0버전부터 추가됩니다.
  </section>
  <section>
    <h4>2. 수집 언론사</h4>
    RSS 생존 검증(check_feeds.py)을 통과한 통신사·방송사·종합일간지·경제지·인터넷신문·IT매체 31곳의 최근 24시간 기사만 사용합니다.
  </section>
  <section>
    <h4>3. 전재(통신사 복제) 처리</h4>
    "(서울=연합뉴스)" 등 통신사 크레딧 패턴, 또는 통신사 기사와의 문장 유사도가 매우 높은 기사는 전재로 판정해 기사량·다양성 점수 집계에서 제외합니다.
  </section>
  <section>
    <h4>4. 대표 제목 선정</h4>
    이슈 내 통신사(연합뉴스·뉴시스·뉴스1) 기사 제목 중 가장 이른 것을 1순위로 선택합니다. 통신사 기사가 없으면 이슈 내 가장 이른 기사의 제목을 사용합니다.
  </section>
  <section>
    <h4>5. 면적·색상이 의미하는 것</h4>
    카드 면적은 (중요도 점수-40)²에 비례합니다. 점수를 그대로 쓰면 실제 이슈들이 50~90점 구간에 몰려 있어
    한눈에 크기 차이가 잘 안 보이기 때문에, 격차를 의도적으로 크게 벌렸습니다(예: 85점과 55점은 원점수로는 1.5배 차이지만
    화면 면적으로는 약 9배 차이). 90점 이상 기사가 없는 회차에도 그날의 1위 이슈는 상대적으로 확실히 크게 보입니다.
    색상은 카테고리(정치·경제·사회·국제·산업·IT·기타) 구분용입니다.
  </section>
  <section>
    <h4>한계 고지</h4>
    이 화면은 보도량과 규칙 기반 점수를 반영하며, 편집자의 판단이나 특정 관점을 대변하지 않습니다.
    클러스터링은 임베딩 모델이 아닌 문자 n-gram TF-IDF 임시 구현이라 일부 이슈가 잘못 묶일 수 있습니다.
    카테고리 배지는 키워드 추정치이며 공식 분류 기능(2차 확장)이 아닙니다.
  </section>
`;

document.getElementById('algo-link').addEventListener('click', () => {
  document.getElementById('algo-body').innerHTML = ALGO_HTML;
  document.getElementById('algo-modal').classList.remove('hidden');
});
document.getElementById('algo-close').addEventListener('click', () => {
  document.getElementById('algo-modal').classList.add('hidden');
});
document.getElementById('refresh-btn').addEventListener('click', () => window.location.reload());

document.getElementById('category-legend').innerHTML = Object.entries(CATEGORY_COLORS)
  .map(([cat, color]) => `<span class="legend-chip"><span class="dot" style="background:${color}"></span>${cat}</span>`)
  .join('');

document.getElementById('domestic-tab').addEventListener('click', () => setMode('domestic'));
document.getElementById('keyword-tab').addEventListener('click', () => setMode('keyword'));
document.getElementById('keyword-form').addEventListener('submit', event => {
  event.preventDefault();
  addCustomKeyword(document.getElementById('keyword-input').value);
});
document.getElementById('keyword-clear').addEventListener('click', clearCustomKeywords);
loadCustomKeywords();
renderKeywordChips();

Promise.allSettled([
  fetch('news_map.json').then(r => r.json()),
  fetch('keyword_news_map.json').then(r => r.json()),
]).then(([domesticResult, keywordResult]) => {
  if (domesticResult.status === 'fulfilled') {
    DOMESTIC_DATA = domesticResult.value;
    KEYWORD_DATA = DOMESTIC_DATA.keyword_news || null;
  }
  if (!KEYWORD_DATA && keywordResult.status === 'fulfilled') {
    KEYWORD_DATA = keywordResult.value;
  }
  if (KEYWORD_DATA) {
    const examples = KEYWORD_DATA.keyword_groups || [];
    if (customKeywords.length === 0 && examples.length > 0) {
      document.getElementById('keyword-input').placeholder = `예: ${examples.slice(0, 3).map(g => g.label).join(', ')}`;
    }
  }

  if (DOMESTIC_DATA) {
    renderCurrent();
    return;
  }

  document.getElementById('treemap').innerHTML =
    `<p style="padding:20px;color:#c00">데이터 로드 실패: 국내뉴스 데이터를 불러오지 못했습니다.</p>`;
});

window.addEventListener('resize', () => {
  if (!DATA) return;

  const width = window.innerWidth;
  const widthChanged = width !== lastViewportWidth;
  lastViewportWidth = width;

  if (widthChanged) {
    mobileTreemapHeight = null;
    mobileTreemapWidth = null;
    render(DATA);
  }
});
