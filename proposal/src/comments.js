/* 3D 제안서 — 페이지 객체 코멘트 (수정 요청 전달용)
   · 코멘트 모드(C)에서 장표 안의 제목·문장·표 칸·숫자·그림·3D 모델을 클릭 → 번호 핀 + 코멘트
   · 3D를 클릭하면 그 자리의 실·세대 이름과 현재 보기 상태(층·세대·시점)를 함께 기록
   · 저장: 이 브라우저(localStorage) 자동 보관 + 수정 요청 Markdown/JSON 내보내기 + 코멘트 포함 HTML 저장 */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pad = n => String(n).padStart(2, '0');
  const KEY = 'deck-comments-v1-' + (DATA.deckId || DATA.slug || 'deck');
  const slides = $$('#stage>.slide');
  const panel = $('#comment-panel'), list = $('#comment-list');
  let data = { comments: [], seq: 0 };
  let on = false, scope = 'page', draft = null, openId = null, downAt = null, hoverEl = null;

  /* ---------- storage ---------- */
  function load() {
    let embedded = null, local = null;
    try { embedded = JSON.parse($('#deck-comments').textContent || 'null'); } catch (e) { }
    try { local = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { }
    const pick = [embedded, local].filter(x => x && Array.isArray(x.comments));
    if (!pick.length) return;
    const byId = new Map();
    pick.forEach(src => src.comments.forEach(c => { const o = byId.get(c.id); if (!o || (c.updated || '') > (o.updated || '')) byId.set(c.id, c); }));
    data.comments = [...byId.values()].sort((a, b) => a.n - b.n);
    data.seq = Math.max(0, ...data.comments.map(c => c.n));
  }
  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify(data)); } catch (e) { }
    const s = $('#deck-comments'); if (s) s.textContent = JSON.stringify(data).replace(/</g, '\\u003c');
    renderAll();
  }
  function author() { try { return localStorage.getItem(KEY + '-author') || ''; } catch (e) { return ''; } }

  /* ---------- page helpers ---------- */
  const cur = () => (window.deck ? window.deck.state().current - 1 : 0);
  const slideTitle = s => (($('h2', s) || $('h1', s) || {}).textContent || s.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const chapter = s => (($$('header span', s)[2] || {}).textContent || '').trim();

  /* ---------- describe a clicked target ---------- */
  const TARGETS = 'h1,h2,h3,p,li,td,th,figcaption,.numbers>div,.explain,.unit-cards button,.tag-list button,.mass-row,.bar,.stack>div,.stack-legend>div,.rule-cards>div,.far-ladder>div,.zone-change>div,.ab-cards>div,.area-defs>div,.decisions>div,.flow,.agenda .jump,.variants button,.unit-nums>div,.room-chips span,.sim-panel,.image-button,.scene-host,table,header,footer';
  function cssPath(el, root) {
    const parts = [];
    while (el && el !== root) {
      const p = el.parentElement; if (!p) break;
      const same = [...p.children].filter(x => x.tagName === el.tagName);
      parts.unshift(el.tagName.toLowerCase() + (same.length > 1 ? `:nth-of-type(${same.indexOf(el) + 1})` : ''));
      el = p;
    }
    return parts.join('>');
  }
  const KIND = { H1: '제목', H2: '제목', H3: '소제목', P: '문장', LI: '항목', TD: '표 칸', TH: '표 머리', FIGCAPTION: '그림 설명', TABLE: '표', HEADER: '머리글', FOOTER: '바닥글' };
  function labelOf(el) {
    if (el.classList.contains('scene-host')) return '3D 모델';
    if (el.classList.contains('image-button')) return '이미지';
    if (el.closest('.numbers')) return '수치';
    if (el.closest('.unit-cards,.variants')) return '세대 타입 버튼';
    if (el.closest('.bars,.stack,.stack-legend,.far-ladder,#mass-chart')) return '차트';
    if (el.tagName === 'TD' || el.tagName === 'TH') {
      const tr = el.parentElement, row = tr.children[0] ? tr.children[0].textContent.trim() : '';
      const table = el.closest('table'), th = table && table.querySelector('thead tr');
      const col = th && th.children[[...tr.children].indexOf(el)] ? th.children[[...tr.children].indexOf(el)].textContent.trim() : '';
      return `표 칸${row && el !== tr.children[0] ? ` [${row}${col ? ' · ' + col : ''}]` : ''}`;
    }
    return KIND[el.tagName] || (el.className && typeof el.className === 'string' ? el.className.split(' ')[0] : el.tagName.toLowerCase());
  }
  function snippet(el) {
    if (el.classList.contains('scene-host')) return '';
    if (el.classList.contains('image-button')) { const im = $('img', el); return im ? im.alt : ''; }
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return t.length > 70 ? t.slice(0, 68) + '…' : t;
  }
  function sceneState() {
    const S = window.SB3D; if (!S) return null;
    const c = S.config || {};
    const MODE = { exterior: '외관', cinema: '연출 외관', program: '층별 구성', floor: '층 평면', unit: '세대', core: '코어', struct: '구조', basement: '지하', parksim: '주차 시뮬레이션', context: '대지 맥락', compare: '준공안 비교' };
    return [MODE[c.mode] || c.mode, c.floor, c.unit ? c.unit + '호' : '', c.view ? '시점 ' + c.view : ''].filter(Boolean).join(' · ');
  }

  /* ---------- comment mode: hover + click ---------- */
  function targetFrom(e) {
    const s = slides[cur()]; if (!s || !s.contains(e.target)) return null;
    if (e.target.closest('.c-pin,.c-pop,.scene-ui,.floor-rail,.legend,.cinema-ctrl')) return null;
    const el = e.target.closest(TARGETS);
    return el && s.contains(el) ? el : s;
  }
  function onMove(e) {
    if (!on || draft) return;
    const el = targetFrom(e);
    if (hoverEl && hoverEl !== el) hoverEl.classList.remove('c-hover');
    hoverEl = el; if (el && el !== slides[cur()]) el.classList.add('c-hover');
  }
  function onDown(e) { if (on) downAt = [e.clientX, e.clientY]; }
  function onClick(e) {
    if (!on) return;
    const s = slides[cur()]; if (!s.contains(e.target)) return;
    if (e.target.closest('.c-pin,.c-pop')) return;
    if (e.target.closest('.scene-ui,.floor-rail,.cinema-ctrl')) return; // 3D 조작 버튼은 그대로 동작
    if (downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 6) return; // 3D 드래그 후 놓기
    e.preventDefault(); e.stopPropagation();
    const el = targetFrom(e) || s;
    const r = s.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width * 100, y = (e.clientY - r.top) / r.height * 100;
    const t = { path: el === s ? '' : cssPath(el, s), label: el === s ? '장표 전체' : labelOf(el), text: el === s ? '' : snippet(el) };
    if (el.closest('.scene-host')) {
      const h = el.closest('.scene-host');
      t.label = '3D 모델'; t.scene = sceneState();
      const hit = window.SB3D && window.SB3D.pickAt ? window.SB3D.pickAt(e.clientX, e.clientY) : null;
      if (hit) t.text = hit;
      if (!h.contains(e.target)) t.scene = null;
    }
    openDraft({ page: cur() + 1, x, y, target: t });
  }

  /* ---------- pins & popovers ---------- */
  function pinLayer(s) {
    let L = $(':scope>.c-layer', s);
    if (!L) { L = document.createElement('div'); L.className = 'c-layer'; L.dataset.rt = ''; s.appendChild(L); }
    return L;
  }
  function renderPins() {
    $$('.c-layer').forEach(l => l.remove());
    if (!on) return;
    slides.forEach((s, i) => {
      const cs = data.comments.filter(c => c.page === i + 1);
      if (!cs.length) return;
      const L = pinLayer(s);
      cs.forEach(c => {
        const p = document.createElement('button'); p.className = 'c-pin' + (c.status === 'done' ? ' done' : '') + (openId === c.id ? ' open' : '');
        p.style.left = c.x + '%'; p.style.top = c.y + '%'; p.textContent = c.n; p.title = c.text;
        p.onclick = ev => { ev.stopPropagation(); openId = openId === c.id ? null : c.id; draft = null; renderAll(); };
        L.appendChild(p);
        if (openId === c.id) L.appendChild(popView(c));
      });
    });
    if (draft) { const s = slides[draft.page - 1]; const L = pinLayer(s); const p = document.createElement('span'); p.className = 'c-pin new'; p.style.left = draft.x + '%'; p.style.top = draft.y + '%'; p.textContent = '+'; L.appendChild(p); L.appendChild(popDraft()); }
  }
  function placePop(pop, x, y) {
    pop.style.left = Math.min(x, 72) + '%'; pop.style.top = (y > 60 ? y - 2 : y + 3) + '%';
    if (y > 60) pop.style.transform = 'translateY(-100%)';
  }
  function targetLine(t) {
    return `<div class="c-target"><b>${esc(t.label)}</b>${t.text ? ` · ${esc(t.text)}` : ''}${t.scene ? `<br><small>3D: ${esc(t.scene)}</small>` : ''}</div>`;
  }
  function popDraft() {
    const d = draft, pop = document.createElement('div'); pop.className = 'c-pop'; placePop(pop, d.x, d.y);
    pop.innerHTML = `<div class="c-head">새 코멘트 · ${pad(d.page)}쪽</div>${targetLine(d.target)}
      <textarea rows="4" placeholder="수정할 내용을 적어 주세요 (Ctrl+Enter 저장)"></textarea>
      <div class="c-row"><select><option value="수정">수정</option><option value="질문">질문</option><option value="추가">추가</option><option value="삭제">삭제</option></select><span></span><button data-a="cancel">취소</button><button data-a="save" class="c-primary">등록</button></div>`;
    const ta = $('textarea', pop);
    const save = () => {
      const text = ta.value.trim(); if (!text) { ta.focus(); return; }
      const now = new Date().toISOString();
      const c = { id: 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), n: ++data.seq, page: d.page, title: slideTitle(slides[d.page - 1]), chapter: chapter(slides[d.page - 1]),
        x: +d.x.toFixed(2), y: +d.y.toFixed(2), target: d.target, kind: $('select', pop).value, text, author: author(), status: 'open', created: now, updated: now };
      data.comments.push(c); draft = null; openId = null; persist();
    };
    $('[data-a=save]', pop).onclick = save;
    $('[data-a=cancel]', pop).onclick = () => { draft = null; renderAll(); };
    ta.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save(); if (e.key === 'Escape') { draft = null; renderAll(); } });
    setTimeout(() => ta.focus(), 30);
    return pop;
  }
  function popView(c) {
    const pop = document.createElement('div'); pop.className = 'c-pop'; placePop(pop, c.x, c.y);
    pop.innerHTML = `<div class="c-head">#${c.n} · ${esc(c.kind || '수정')}${c.author ? ' · ' + esc(c.author) : ''}<small>${c.created.slice(0, 10)}</small></div>${targetLine(c.target)}
      <textarea rows="4">${esc(c.text)}</textarea>${c.reply ? `<div class="c-reply">↳ ${esc(c.reply)}</div>` : ''}
      <div class="c-row"><button data-a="del">삭제</button><span></span><button data-a="done">${c.status === 'done' ? '다시 열기' : '반영 완료'}</button><button data-a="save" class="c-primary">저장</button></div>`;
    $('[data-a=save]', pop).onclick = () => { c.text = $('textarea', pop).value.trim() || c.text; c.updated = new Date().toISOString(); openId = null; persist(); };
    $('[data-a=done]', pop).onclick = () => { c.status = c.status === 'done' ? 'open' : 'done'; c.updated = new Date().toISOString(); persist(); };
    $('[data-a=del]', pop).onclick = () => { if (!confirm(`#${c.n} 코멘트를 삭제할까요?`)) return; data.comments = data.comments.filter(x => x !== c); openId = null; persist(); };
    pop.addEventListener('click', e => e.stopPropagation());
    return pop;
  }
  function openDraft(d) { draft = d; openId = null; if (hoverEl) hoverEl.classList.remove('c-hover'); renderAll(); }

  /* ---------- side panel ---------- */
  function renderList() {
    const page = cur() + 1;
    const items = data.comments.filter(c => scope === 'all' || c.page === page);
    const open = data.comments.filter(c => c.status !== 'done').length;
    $('#comment-count').textContent = `전체 ${data.comments.length} · 미반영 ${open}`;
    const badge = $('#comment-toggle .c-badge'); if (badge) { badge.textContent = open || ''; badge.hidden = !open; }
    list.innerHTML = items.length ? items.map(c => `<li class="${c.status === 'done' ? 'done' : ''}${openId === c.id ? ' open' : ''}" data-id="${c.id}"><span class="c-n">${c.n}</span><div><small>${pad(c.page)}쪽 · ${esc(c.title)}</small><b>${esc(c.target.label)}${c.target.text ? ' · ' + esc(c.target.text.slice(0, 30)) : ''}</b><p>${esc(c.text)}</p>${c.reply ? `<p class="c-reply">↳ ${esc(c.reply)}</p>` : ''}</div></li>`).join('')
      : `<li class="empty">${scope === 'all' ? '아직 코멘트가 없습니다.' : '이 쪽에는 코멘트가 없습니다.'}<br>장표에서 고칠 곳을 클릭해 코멘트를 남기세요.</li>`;
    $$('li[data-id]', list).forEach(li => li.onclick = () => {
      const c = data.comments.find(x => x.id === li.dataset.id); if (!c) return;
      if (c.page !== page) window.deck.go(c.page - 1);
      openId = c.id; draft = null; renderAll();
    });
  }
  function renderAll() { renderPins(); if (panel) renderList(); }

  function setOn(v) {
    on = v; document.body.classList.toggle('commenting', on);
    panel.hidden = !on; $('#comment-toggle').setAttribute('aria-pressed', String(on));
    if (!on) { draft = null; openId = null; if (hoverEl) hoverEl.classList.remove('c-hover'); }
    if (on && document.body.classList.contains('editing')) $('#edit-close').click();
    renderAll(); setTimeout(() => window.SB3D && window.SB3D.resize(), 60);
  }

  /* ---------- export ---------- */
  function markdown(onlyOpen) {
    const cs = data.comments.filter(c => !onlyOpen || c.status !== 'done').sort((a, b) => a.page - b.page || a.n - b.n);
    const out = [`# ${DATA.title} — 수정 요청`, ``, `- 작성: ${new Date().toLocaleString('ko-KR')}${author() ? ' · ' + author() : ''}`, `- 코멘트 ${cs.length}건 (${onlyOpen ? '미반영만' : '전체'})`, ``];
    let pg = 0;
    cs.forEach(c => {
      if (c.page !== pg) { if (pg) out.push(''); pg = c.page; out.push(`## ${pad(c.page)}쪽 · ${c.title}${c.chapter ? ` (${c.chapter})` : ''}`, ''); }
      out.push(`- **#${c.n} [${c.kind || '수정'}]${c.status === 'done' ? ' ~~반영 완료~~' : ''}** ${c.text.replace(/\n/g, ' / ')}`);
      if (c.reply) out.push(`  - 반영: ${c.reply}`);
      out.push(`  - 대상: ${c.target.label}${c.target.text ? ` — "${c.target.text}"` : ''}`);
      if (c.target.scene) out.push(`  - 3D 상태: ${c.target.scene}`);
      if (c.target.path) out.push(`  - 위치: \`${c.target.path}\``);
    });
    return out.join('\n') + '\n';
  }
  function download(name, text, type) {
    const b = new Blob([text], { type }), u = URL.createObjectURL(b), a = document.createElement('a');
    a.href = u; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 8000);
  }
  const stamp = () => { const d = new Date(); return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`; };
  function toast(s) { const t = $('#toast'); if (!t) return; t.textContent = s; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2600); }

  /* ---------- wire up ---------- */
  load();
  $('#comment-toggle').onclick = () => setOn(!on);
  $('#comment-close').onclick = () => setOn(false);
  $$('[data-scope]').forEach(b => b.onclick = () => { scope = b.dataset.scope; $$('[data-scope]').forEach(x => x.setAttribute('aria-pressed', String(x === b))); renderList(); });
  const au = $('#comment-author'); au.value = author(); au.onchange = () => { try { localStorage.setItem(KEY + '-author', au.value.trim()); } catch (e) { } };
  $('#comment-copy').onclick = async () => {
    const md = markdown(true);
    try { await navigator.clipboard.writeText(md); toast('미반영 수정 요청을 복사했습니다. 대화창에 붙여 넣으세요.'); }
    catch (e) { download(`${DATA.slug}_수정요청_${stamp()}.md`, md, 'text/markdown'); toast('클립보드를 쓸 수 없어 파일로 저장했습니다.'); }
  };
  $('#comment-md').onclick = () => download(`${DATA.slug}_수정요청_${stamp()}.md`, markdown(false), 'text/markdown');
  $('#comment-json').onclick = () => download(`${DATA.slug}_코멘트_${stamp()}.json`, JSON.stringify(data, null, 2), 'application/json');
  $('#comment-import').onchange = e => {
    const f = e.target.files[0]; if (!f) return;
    f.text().then(t => {
      const inc = JSON.parse(t); if (!inc || !Array.isArray(inc.comments)) throw new Error();
      const byId = new Map(data.comments.map(c => [c.id, c]));
      inc.comments.forEach(c => { const o = byId.get(c.id); if (!o || (c.updated || '') > (o.updated || '')) byId.set(c.id, c); });
      data.comments = [...byId.values()].sort((a, b) => a.n - b.n); data.seq = Math.max(data.seq, ...data.comments.map(c => c.n));
      persist(); toast(`코멘트 ${inc.comments.length}건을 합쳤습니다.`);
    }).catch(() => toast('코멘트 JSON 파일이 아닙니다.')); e.target.value = '';
  };
  $('#comment-html').onclick = () => { persist(); window.deck && window.deck.saveHTML && window.deck.saveHTML('코멘트포함'); toast('코멘트가 들어 있는 HTML을 저장했습니다.'); };
  $('#comment-clear-done').onclick = () => { const n = data.comments.filter(c => c.status === 'done').length; if (!n || !confirm(`반영 완료된 코멘트 ${n}건을 목록에서 지울까요?`)) return; data.comments = data.comments.filter(c => c.status !== 'done'); persist(); };

  document.addEventListener('pointermove', onMove, true);
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('deck-page-change', () => { draft = null; openId = null; renderAll(); });
  addEventListener('keydown', e => { if (e.key === 'Escape' && on && (draft || openId)) { draft = null; openId = null; renderAll(); } });

  window.deckComments = { toggle: () => setOn(!on), get data() { return data; }, markdown };
  renderAll();
})();
