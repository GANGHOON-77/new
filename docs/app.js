// 오늘의 뉴스지도 0.1 - 등급별 고정 크기 그리드 렌더러

const SCORE_THRESHOLD = 50; // 이 점수 미만 이슈는 화면에서 제외
const MAX_ITEMS = 20;       // 최대 노출 이슈 수

const TIER_DEFS = [
  { cls: 't1', count: 3, showDesc: true },
  { cls: 't2', count: 4, showDesc: true },
  { cls: 't3', count: 8, showDesc: false },
  { cls: 't4', count: 5, showDesc: false },
  { cls: 't5', count: Infinity, showDesc: false },
];

// 분야별 고정 색상 (요청에 따라 점수가 아닌 카테고리로 색상 구분)
const CATEGORY_COLORS = {
  '정치': '#c0392b',
  '경제': '#1f6fb2',
  '사회': '#1a9e7c',
  '국제': '#7d3c98',
  '산업': '#d2691e',
  'IT':   '#34495e',
  '기타': '#8a8f98',
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
let selectedId = null;

function render(data) {
  DATA = data;
  document.getElementById('today-date').textContent =
    new Date(data.date + 'T00:00:00+09:00').toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
  document.getElementById('updated-at').textContent = `업데이트: ${fmtTime(data.updated_at)} 기준`;

  const items = data.clusters.filter(c => c.score >= SCORE_THRESHOLD).slice(0, MAX_ITEMS);

  document.getElementById('meta-line').textContent =
    `수집원 ${data.source_count_total}곳 · 수집 기사 ${data.article_count_total}건 · 이슈 ${data.cluster_count_total}개 중 중요도 ${SCORE_THRESHOLD}점 이상 ${items.length}개 표시`;

  const el = document.getElementById('treemap');
  el.innerHTML = '';

  let cursor = 0;
  let rank = 1;
  for (const tier of TIER_DEFS) {
    if (cursor >= items.length) break;
    const slice = items.slice(cursor, cursor + tier.count);
    if (slice.length === 0) continue;
    const row = document.createElement('div');
    row.className = `tier-row ${tier.cls}`;
    slice.forEach(item => {
      row.appendChild(buildCell(item, rank, tier));
      rank++;
    });
    el.appendChild(row);
    cursor += tier.count;
  }

  // 클릭 없이도 바로 보이도록 기본으로 1위 이슈 상세를 띄운다.
  if (selectedId === null && items.length > 0) {
    selectedId = items[0].id;
    renderDetail(items[0].id, items.findIndex(x => x.id === items[0].id) + 1);
  } else if (selectedId !== null) {
    const stillShown = items.find(x => x.id === selectedId);
    if (stillShown) renderDetail(selectedId, items.indexOf(stillShown) + 1);
  }
}

function buildCell(item, rank, tier) {
  const div = document.createElement('div');
  div.className = 'cell' + (item.id === selectedId ? ' selected' : '');
  div.style.background = categoryColor(item.category);
  div.innerHTML = `
    <div class="badge-row">
      <span class="rank">${rank}</span>
      <span class="category">${escapeHtml(item.category)}</span>
    </div>
    <div class="title">${escapeHtml(item.title)}</div>
    ${tier.showDesc && item.excerpt ? `<div class="desc">${escapeHtml(item.excerpt)}</div>` : ''}
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
  const maxes = { article_score: 25, diversity_score: 20, major_score: 15, breaking_score: 10, time_score: 5 };
  const labels = { article_score: '기사량(전재 제외)', diversity_score: '언론사 다양성', major_score: '주요 언론 보도', breaking_score: '속보성·긴급성', time_score: '시간 가중치' };
  const shown = c.articles.slice(0, 10);

  panel.innerHTML = `
    <button class="close-btn" id="close-detail">×</button>
    <span class="top-badge">${rank} ${escapeHtml(c.category)}</span>
    <h2>${c.title_url ? `<a href="${c.title_url}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>` : escapeHtml(c.title)}</h2>
    <div class="time-row">🕐 최초 보도: ${fmtDateTime(c.first_published_at)} · 최근 보도: ${fmtDateTime(c.latest_published_at)}</div>
    <div class="title-source-note">대표 제목: ${c.title_source === 'wire_pick' ? '통신사 제목 선택(1순위 규칙)' : '이슈 내 최이른 기사 제목'} (제목 클릭 시 원문으로 이동)</div>
    <span class="summary-label">인용 요약 (0.1버전: 대표 기사 발췌 인용 · AI 요약은 1.0버전부터)</span>
    <div class="summary">${escapeHtml(c.excerpt || '요약문을 제공하는 기사가 없습니다.')}${c.excerpt_source ? ` — ${c.excerpt_url ? `<a href="${c.excerpt_url}" target="_blank" rel="noopener" style="color:#999">${escapeHtml(c.excerpt_source)}</a>` : `<span style="color:#999">${escapeHtml(c.excerpt_source)}</span>`}` : ''}</div>
    <div class="score-box">
      <div class="score-num">${c.score}<span style="font-size:14px;color:#999">/100</span></div>
      ${Object.keys(labels).map(k => `
        <div class="score-row">
          <span class="label">${labels[k]}</span>
          <span class="bar-bg"><span class="bar-fg" style="width:${(b[k] / maxes[k] * 100).toFixed(0)}%"></span></span>
          <span class="val">${b[k]}/${maxes[k]}</span>
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
    카드 크기는 등급(1~3위/4~7위/8~15위/16~24위/그 외)에 따른 고정 크기이며, 색상이 중요도 점수를 나타냅니다(진한 빨강일수록 고득점).
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

fetch('news_map.json')
  .then(r => r.json())
  .then(render)
  .catch(err => {
    document.getElementById('treemap').innerHTML =
      `<p style="padding:20px;color:#c00">데이터 로드 실패: ${err}</p>`;
  });

window.addEventListener('resize', () => { if (DATA) render(DATA); });
