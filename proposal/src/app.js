/* 3D 제안서 — 장표 조작·3D 연결·데이터 장표·편집 (수치는 모두 DATA.metrics에서) */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pad = n => String(n).padStart(2, '0');
  const nf = (v, d = 2) => Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const MX = DATA.metrics;
  const S3 = window.SB3D;
  const TITLE = DATA.title || '건축계획 제안';
  const SLUG = DATA.slug || 'project';
  const slides = $$('#stage>.slide');
  let current = 0, priorFocus = null, toastTimer = 0, activeHost = null;

  slides.forEach((s, i) => { s.id = 'slide-' + (i + 1); const pg = $('.pg', s); if (pg) pg.textContent = `${pad(i + 1)} / ${pad(slides.length)}`; });

  function toast(s) { const t = $('#toast'); t.textContent = s; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 3000); }
  function fit() { document.documentElement.style.setProperty('--fit', Math.min(innerWidth / 1600, (innerHeight - 55) / 900)); }
  addEventListener('resize', fit); fit();

  /* ---------------- navigation ---------------- */
  function go(n, hash = true) {
    current = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach((s, i) => { s.classList.toggle('active', i === current); s.setAttribute('aria-hidden', String(i !== current)); });
    $('#counter').textContent = `${pad(current + 1)} / ${slides.length}`;
    $('#prev').disabled = current === 0; $('#next').disabled = current === slides.length - 1;
    $('.progress>div').style.width = `${(current + 1) / slides.length * 100}%`;
    if (hash) history.replaceState(null, '', '#' + (current + 1));
    document.title = `${current + 1}. ${slideTitle(slides[current])} | ${TITLE}`;
    if (innerWidth <= 700) scrollTo(0, 0);
    $$('.overview-item').forEach((b, i) => b.setAttribute('aria-current', String(i === current)));
    syncScene();
    document.dispatchEvent(new CustomEvent('deck-page-change', { detail: current }));
  }
  const slideTitle = s => ($('h2', s) || $('h1', s) || {}).textContent?.replace(/\s+/g, ' ').trim() || s.getAttribute('aria-label');
  window.deck = { go, state: () => ({ current: current + 1, total: slides.length }) };

  /* ---------------- 3D hosts ---------------- */
  const VLABEL = { iso: '조감', aerial: '광역', top: '평면도', street: '보행자', north: '북측', side: '측면', low: '저층', floor: '층 모델', plan: '평면', core: '코어', struct: '구조', basement: '지하', hero: '정면', unit: '세대', park: '주차장', flows: '동선' };
  function cfgOf(host) { try { return JSON.parse(host.dataset.scene || '{}'); } catch (e) { return {}; } }
  function syncScene() {
    if (!S3) return;
    const host = $('.scene-host', slides[current]);
    if (activeHost && activeHost !== host) { $$('[data-rt]', activeHost).forEach(e => e.remove()); S3.detach(); activeHost = null; }
    if (!host || host === activeHost) return;
    activeHost = host;
    const cfg = cfgOf(host);
    if (host.id === 'explore-scene' && pendingExplore) { Object.assign(cfg, pendingExplore); pendingExplore = null; }
    S3.attach(host, cfg);
    buildUI(host, cfg);
    if (host.id === 'program-scene') resetTags();
    if (host.id === 'mass-scene') { $$('[data-group]').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.group === String(typGroup)))); }
    if (host.id === 'unit-scene') $$('.unit-cards button').forEach(b => b.setAttribute('aria-pressed', 'false'));
    updateCinemaBtn();
  }
  function rt(tag, cls, html) { const e = document.createElement(tag); e.className = cls; e.dataset.rt = ''; if (html) e.innerHTML = html; return e; }
  function buildUI(host, cfg) {
    const opts = (host.dataset.ui || '').split('|').filter(Boolean);
    const hint = rt('div', 'scene-ui tl'); hint.innerHTML = '<span class="scene-hint">드래그 회전 · 휠 확대 · 오른쪽 드래그 이동</span>';
    if (!host.closest('.cinematic')) host.appendChild(hint);
    const br = rt('div', 'scene-ui br');
    opts.forEach(o => {
      const [k, v] = o.split(':');
      if (k === 'views') {
        v.split(',').forEach((name, i) => {
          const b = document.createElement('button'); b.textContent = VLABEL[name] || name; b.setAttribute('aria-pressed', String((cfg.view || '') === name || (!cfg.view && i === 0)));
          b.onclick = () => { S3.apply({ view: name }); $$('button', b.parentNode).forEach(x => { if (x.dataset.view != null) x.setAttribute('aria-pressed', String(x === b)); }); };
          b.dataset.view = name; br.appendChild(b);
        });
      }
      if (k === 'explode') {
        const b = rt('button', ''); b.removeAttribute('data-rt'); b.textContent = '분해 보기'; b.setAttribute('aria-pressed', String(!!cfg.explode));
        b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on)); S3.apply({ explode: on }); };
        br.prepend(b);
      }
      if (k === 'cut') {
        const b = document.createElement('button'); b.textContent = '벽 전체 높이'; b.setAttribute('aria-pressed', 'false'); b.dataset.cutBtn = '';
        b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on)); S3.apply({ cut: !on }); };
        br.prepend(b);
      }
      if (k === 'auto') {
        const b = document.createElement('button'); b.textContent = '자동 회전'; b.setAttribute('aria-pressed', 'false');
        b.onclick = () => b.setAttribute('aria-pressed', String(S3.toggleAuto())); br.prepend(b);
      }
      if (k === 'floors') {
        const tr = rt('div', 'scene-ui tr');
        v.split(',').forEach(id => {
          const b = document.createElement('button'); b.textContent = id; b.setAttribute('aria-pressed', String(id === cfg.floor));
          b.onclick = () => { S3.apply({ floor: id }); $$('button', tr).forEach(x => x.setAttribute('aria-pressed', String(x === b))); fillZones(host.closest('.slide'), id); };
          tr.appendChild(b);
        });
        host.appendChild(tr);
      }
      if (k === 'rail') {
        const rail = rt('div', 'floor-rail');
        const ext = document.createElement('button'); ext.textContent = '외관'; ext.dataset.floor = '';
        rail.appendChild(ext);
        S3.floors.forEach(f => { const b = document.createElement('button'); b.textContent = f.id; b.dataset.floor = f.id; rail.appendChild(b); });
        const mark = id => $$('button', rail).forEach(x => x.setAttribute('aria-pressed', String(x.dataset.floor === (id || ''))));
        $$('button', rail).forEach(b => b.onclick = () => {
          if (!b.dataset.floor) { S3.set({ mode: 'exterior', view: 'iso' }); setExploreCaption('외관 · 오른쪽 층 버튼으로 층별 평면 모델 열기'); }
          else { const f = S3.floors.find(x => x.id === b.dataset.floor); S3.set({ mode: 'floor', floor: f.id, view: f.lv > 2 ? 'plan' : 'floor' }); setExploreCaption(`${f.id} · ${floorDesc(f.id)}`); }
          mark(b.dataset.floor); $$('.explore-menu button').forEach(x => x.setAttribute('aria-pressed', 'false'));
        });
        mark(cfg.mode === 'floor' ? cfg.floor : (cfg.mode === 'exterior' ? '' : '-'));
        host.appendChild(rail);
      }
      if (k === 'legend') host.appendChild(legend(v, cfg));
      if (k === 'flows') {
        const L = rt('div', 'legend flow-legend');
        Object.entries(S3.flows.defs).forEach(([key, d]) => {
          const b = document.createElement('button'); b.setAttribute('aria-pressed', 'true'); b.innerHTML = `<i style="background:${d.color}"></i>${d.label}`;
          b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on)); S3.flows.show(key, on); };
          L.appendChild(b); S3.flows.show(key, true);
        });
        host.appendChild(L);
      }
    });
    if (br.children.length) host.appendChild(br);
  }
  function legend(kind) {
    const L = rt('div', 'legend');
    const sw = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
    if (kind === 'context') L.innerHTML = sw('#cfd8cc', '대상 대지') + sw('#c0563b', '일반상업지역') + sw('#d6a64a', '제3종일반주거') + sw('#7aa06b', '제1종일반주거') + sw('#c98a6a', '철도 부지') + sw('#b9bcbc', '도로');
    else if (kind === 'units') L.innerHTML = DATA.model.ut.map((t, i) => sw(S3.UCOL[i], t)).join('') + sw(S3.ZCOL.core, '코어') + sw(S3.ZCOL.corridor, '복도') + sw(S3.ZCOL.mep, '설비');
    else L.innerHTML = ['retail', 'public', 'lobby', 'community', 'core', 'corridor', 'mep', 'parking', 'aisle', 'ramp'].map(k => sw(S3.ZCOL[k], S3.ZLABEL[k])).join('') + sw(S3.UCOL[1], '세대');
    return L;
  }
  function floorDesc(id) {
    const f = MX.floors.find(x => x.id === id);
    if (!f) return '';
    if (f.level < 0) return `지하 주차 ${f.stalls}면 · ${nf(f.area)}㎡`;
    if (f.units) return `${f.planType || '주거층'} · ${f.units}세대 · ${nf(f.area)}㎡`;
    return `기단 · ${nf(f.area)}㎡ · 층고 ${f.height}m`;
  }
  function setExploreCaption(t) { const c = $('#explore-caption'); if (c) c.textContent = t; }
  let pendingExplore = null;
  $$('[data-explore]').forEach(b => b.onclick = () => {
    const cfg = JSON.parse(b.dataset.explore);
    S3.set(cfg); $$('[data-explore]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    $$('#explore-scene .floor-rail button').forEach(x => x.setAttribute('aria-pressed', 'false'));
    setExploreCaption($('span', b).textContent + ' · ' + $('small', b).textContent);
  });
  function updateCinemaBtn() { $$('[data-cinema=auto]').forEach(b => { const c = S3.config; b.textContent = c && c.auto ? '회전 정지' : '회전 시작'; }); }
  $$('[data-cinema=auto]').forEach(b => b.onclick = () => { const on = S3.toggleAuto(); S3.config.auto = on; b.textContent = on ? '회전 정지' : '회전 시작'; });

  /* ---------------- data-driven pages ---------------- */
  const up = MX.floors.filter(f => f.level > 0);
  const zsum = k => up.reduce((s, f) => s + (f.zones[k] || 0), 0);
  const unitArea = up.reduce((s, f) => s + f.unitArea, 0);

  // program tags
  const byFloor = k => up.filter(f => f.zones[k]).map(f => `${f.id} ${nf(f.zones[k])}㎡`);
  const flRange = ids => ids.length ? (ids.length > 1 ? `${ids[0]}–${ids[ids.length - 1]}` : ids[0]) : '';
  const resIds = up.filter(f => f.units).map(f => f.id);
  const listNote = (k, lead) => { const a = byFloor(k); return a.length ? `${lead} ${a.length > 4 ? a.slice(0, 2).join(' · ') + ` 외 ${a.length - 2}개 층` : a.join(' · ')}` : ''; };
  const TAGS = [
    { k: ['unit', 'u0', 'u1', 'u2', 'u3'], c: S3.UCOL[1], t: '세대', a: unitArea, n: `${MX.units}세대 · ${flRange(resIds)}. ${DATA.model.ut.filter(u => MX.unitTypes[u] && MX.unitTypes[u].n).join('·')} 타입을 색으로 구분합니다.` },
    { k: ['core'], c: S3.ZCOL.core, t: '코어', a: zsum('core'), n: `계단 ${MX.stairs}개소 · 승강기 ${MX.elevators}대${MX.emergencyElevators ? ` (비상용 ${MX.emergencyElevators})` : ''}가 ${MX.floors[0].id}부터 ${up[up.length - 1].id}까지 이어집니다.` },
    { k: ['corridor'], c: S3.ZCOL.corridor, t: '복도·홀', a: zsum('corridor'), n: listNote('corridor', '복도·홀') },
    { k: ['mep'], c: S3.ZCOL.mep, t: '설비 샤프트', a: zsum('mep'), n: 'PS·EPS·TPS·AD — 층마다 같은 위치로 수직 계통을 잇습니다.' },
    { k: ['retail'], c: S3.ZCOL.retail, t: '근린생활시설', a: zsum('retail'), n: listNote('retail', '근린생활시설') },
    { k: ['public'], c: S3.ZCOL.public, t: '공공시설', a: zsum('public'), n: listNote('public', '공공시설') + (MX.districtPlan && MX.districtPlan.publicArea ? ` · 기부채납 고시 면적 ${nf(MX.districtPlan.publicArea)}㎡` : '') },
    { k: ['lobby'], c: S3.ZCOL.lobby, t: '주거 로비', a: zsum('lobby'), n: listNote('lobby', '주거 출입홀·로비') },
    { k: ['community'], c: S3.ZCOL.community, t: '주민공동시설', a: zsum('community'), n: listNote('community', '주민공동시설') + (MX.districtPlan && MX.districtPlan.communityMin ? ` · 지침 ${MX.districtPlan.communityMin}㎡ 이상` : '') },
  ].filter(x => x.a > 0);
  function resetTags() { $$('.tag-list button').forEach(b => b.setAttribute('aria-pressed', 'false')); const n = $('#tag-note'); if (n) n.textContent = '전체 프로그램을 표시하고 있습니다.'; }
  function fillTags() {
    const box = $('[data-runtime=tags]'); if (!box) return;
    box.innerHTML = TAGS.map((t, i) => `<button data-tag="${i}" aria-pressed="false"><i style="background:${t.c}"></i><span>${t.t}</span><small>${nf(t.a)}㎡</small></button>`).join('');
    $$('[data-tag]', box).forEach(b => b.onclick = () => {
      const on = b.getAttribute('aria-pressed') !== 'true', t = TAGS[+b.dataset.tag];
      $$('[data-tag]', box).forEach(x => x.setAttribute('aria-pressed', String(on && x === b)));
      S3.apply({ highlight: on ? t.k : null });
      $('#tag-note').textContent = on ? t.n : '전체 프로그램을 표시하고 있습니다.';
    });
  }

  // mass chart
  const GROUPS = MX.groups || [];
  const typGroup = Math.max(0, GROUPS.findIndex(g => g.ids.includes(MX.typFloor)));
  function fillMass() {
    const box = $('[data-runtime=mass]'); if (!box) return;
    const max = Math.max(...GROUPS.map(g => g.area));
    box.innerHTML = GROUPS.map((g, i) => {
      const f = MX.floors.find(x => x.id === g.ids[0]);
      return `<div class="mass-row" style="height:${Math.max(56, g.ids.length * 15 + 30)}px"><button class="mass-shape" data-group="${i}" aria-pressed="${i === typGroup}" style="width:${f.area / max * 430}px">${g.label}</button><div class="mass-info"><strong>${nf(f.area)} ㎡</strong><small>층당 외곽 면적 · ${g.sub}</small></div></div>`;
    }).join('') + '<p id="mass-detail">각 구간을 선택하면 오른쪽 모델에서 해당 층을 표시합니다.</p>';
    $$('[data-group]', box).forEach(b => b.onclick = () => {
      const g = GROUPS[+b.dataset.group], f = MX.floors.find(x => x.id === g.ids[0]);
      $$('[data-group]', box).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      const base = g.kind === 'basement';
      S3.apply(base ? { mode: 'basement', view: 'side', highlightFloors: g.ids } : { mode: 'exterior', view: 'side', highlightFloors: g.ids });
      $('#mass-detail').textContent = `${g.label} · 바닥 레벨 ${f.elev >= 0 ? '+' : ''}${f.elev.toFixed(1)}m · 층고 ${f.height.toFixed(1)}m · ${g.ids.length}개 층 합계 ${nf(f.area * g.ids.length)}㎡`;
    });
  }

  // composition
  function fillComposition() {
    const box = $('[data-runtime=composition]'); if (!box) return;
    const tot = MX.gfaUp;
    const parts = [
      ['세대 내부', unitArea, '#b97a45'], ['복도·홀', zsum('corridor'), '#a7ada6'], ['코어', zsum('core'), S3.ZCOL.core], ['설비', zsum('mep'), '#6f7a75'],
      ['공공(도서관)', zsum('public'), S3.ZCOL.public], ['근린생활', zsum('retail'), '#d39434'], ['주민공동', zsum('community'), S3.ZCOL.community], ['로비·램프', zsum('lobby') + zsum('ramp'), '#5f8ea0'],
    ];
    box.innerHTML = `<div class="stack">${parts.map(p => `<div style="width:${p[1] / tot * 100}%;background:${p[2]}" title="${p[0]} ${nf(p[1])}㎡">${p[1] / tot > .06 ? p[0] : ''}</div>`).join('')}</div>
      <div class="stack-legend">${parts.map(p => `<div><b>${(p[1] / tot * 100).toFixed(1)}%</b><span><i style="background:${p[2]}"></i>${p[0]} · ${nf(p[1])}㎡</span></div>`).join('')}</div>`;
  }

  // unit cards
  function fillUnits() {
    const box = $('[data-runtime=units]'); if (!box) return;
    box.innerHTML = DATA.model.ut.map((t, i) => { const u = MX.unitTypes[t]; return `<button data-ut="${i}" aria-pressed="false" style="--c:${S3.UCOL[i]}"><strong>${u.n}</strong><span>${t}</span><small>평균 ${u.avg.toFixed(1)}㎡ · ${(u.n / MX.units * 100).toFixed(0)}%</small></button>`; }).join('');
    $$('[data-ut]', box).forEach(b => b.onclick = () => {
      const on = b.getAttribute('aria-pressed') !== 'true';
      $$('[data-ut]', box).forEach(x => x.setAttribute('aria-pressed', String(on && x === b)));
      if (b.closest('.slide').querySelector('.scene-host')) S3.apply({ highlight: on ? ['u' + b.dataset.ut, 'core'] : null });
      const t = DATA.model.ut[+b.dataset.ut];
      $$('[data-runtime=subtypes] tr', b.closest('.slide')).forEach(r => r.hidden = on && r.dataset.type !== t);
    });
  }

  // fire table
  function fillFire() {
    const box = $('[data-runtime=fire]'); if (!box || !MX.review || !MX.review.fire) return;
    box.innerHTML = MX.review.fire.filter(f => f.pri === 'P0').map(f => `<tr><td>${f.id}</td><td>${esc(f.title)}<br><small style="font-size:13px;color:#68726b;font-weight:400">${esc(f.law)}</small></td><td><span class="pill">${esc(f.state)}</span></td></tr>`).join('');
  }

  // cost bars
  function fillCost() {
    const box = $('[data-runtime=cost]'); if (!box || !(MX.costGroups || []).length) return;
    const g = MX.costGroups.slice().sort((a, b) => b.amount - a.amount), max = g[0].amount;
    box.innerHTML = g.map(x => `<div class="bar"><span>${esc(x.grp)}</span><div class="track"><div class="fill" style="width:${x.amount / max * 100}%"></div></div><b>${(x.amount / 1e8).toFixed(1)}억</b></div>`).join('');
  }

  // legal status
  function fillLegal() {
    const box = $('[data-runtime=legal]'); if (!box || !MX.legal) return;
    const s = MX.legal, L = [['ok', '적합'], ['bad', '부적합'], ['chk', '확인 필요'], ['proc', '절차'], ['na', '해당 없음']];
    box.innerHTML = L.map(([k, t]) => `<div class="st-${k}" style="width:${s[k] / s.total * 100}%" title="${t} ${s[k]}">${s[k] > 2 ? `${t} ${s[k]}` : s[k]}</div>`).join('');
  }

  // schedule
  const inGroup = (f, g) => g === 'all' || (GROUPS[+g] && GROUPS[+g].ids.includes(f.id));
  const sfSel = $('#schedule-filter'); if (sfSel) sfSel.innerHTML = '<option value="all">전체</option>' + GROUPS.map((g, i) => `<option value="${i}">${esc(g.label)} · ${esc(g.sub)}</option>`).join('');
  function fillSchedule() {
    const g = $('#schedule-filter') ? $('#schedule-filter').value : 'all';
    const rows = MX.floors.slice().reverse().filter(f => inGroup(f, g));
    const c = $('[data-runtime=count]'); if (c) c.textContent = rows.length + '개 층';
    const tb = $('[data-runtime=schedule]'); if (!tb) return;
    tb.innerHTML = rows.map(f => { const u = f.unitTypes; return `<tr tabindex="0" role="button" data-floor="${f.id}" aria-label="${f.id} 3D로 보기"><td>${f.id}</td><td>${esc(f.planType || (f.level < 0 ? '지하주차' : '기단'))}</td><td>${f.elev >= 0 ? '+' : ''}${f.elev.toFixed(1)}</td><td>${f.height.toFixed(1)}</td><td>${nf(f.area)}</td><td>${f.units || '—'}</td><td>${f.units ? DATA.model.ut.map(k => u[k] || 0).join(' / ') : '—'}</td><td>${f.stalls || '—'}</td><td>${f.sprinklers ?? '—'}</td></tr>`; }).join('');
    $$('tr[data-floor]', tb).forEach(r => {
      r.onclick = () => openFloor(r.dataset.floor);
      r.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.click(); } };
    });
  }
  function openFloor(id) {
    const i = slides.findIndex(s => $('#explore-scene', s));
    const f = S3.floors.find(x => x.id === id);
    pendingExplore = { mode: 'floor', floor: id, view: f.lv > 2 ? 'plan' : 'floor' };
    go(i);
    setExploreCaption(`${id} · ${floorDesc(id)}`);
    $$('#explore-scene .floor-rail button').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.floor === id)));
  }
  const sf = $('#schedule-filter'); if (sf) sf.onchange = fillSchedule;
  const dl = $('#download-data');
  if (dl) dl.onclick = () => {
    const b = new Blob([JSON.stringify({ project: MX.project, metrics: { siteArea: MX.siteArea, gfa: MX.gfa, gfaUp: MX.gfaUp, far: MX.far, units: MX.units, unitTypes: MX.unitTypes, stalls: MX.stalls }, floors: MX.floors, cost: MX.cost, legal: MX.legal }, null, 2)], { type: 'application/json' });
    const u = URL.createObjectURL(b), a = document.createElement('a'); a.href = u; a.download = `${SLUG}_계획수치.json`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 5000);
  };

  // workflow stages
  function fillWorkflow() {
    const box = $('[data-runtime=workflow]'); if (!box) return;
    const ST = { done: ['완료', 'st-done'], active: ['진행', 'st-active'], planned: ['예정', 'st-plan'] };
    box.innerHTML = MX.workflow.map(w => `<div class="stage ${ST[w.status][1]}"><b>${pad(w.no)}</b><span>${esc(w.title)}</span><small>${ST[w.status][0]}</small></div>`).join('');
  }
  // review logs (request -> reply)
  function fillLogs() {
    const z = $('[data-runtime=log-zoning]');
    if (z) z.innerHTML = MX.comments.zoning.map(c => `<tr><td>${c.n}</td><td>${esc(c.text)}</td><td>${esc(c.reply) || '—'}</td><td><span class="pill ${c.status === 'done' ? 'ok' : ''}">${c.status === 'done' ? '반영' : '열림'}</span></td></tr>`).join('');
    const p = $('[data-runtime=log-plan]');
    const V = { type: '타입', '2d': '2D', '3d': '3D' };
    if (p) p.innerHTML = MX.comments.plan.map(c => `<tr><td>${c.n}</td><td>${V[c.view] || esc(c.view)}</td><td>${esc(c.text)}</td><td>${esc(c.reply)}</td></tr>`).join('');
  }
  // lightbox
  $$('[data-zoom]').forEach(b => b.onclick = () => {
    const im = $('img', b); $('#lightbox-img').src = im.src;
    const fig = b.closest('figure'); const cap = fig && $('figcaption', fig);
    $('#lightbox-caption').textContent = cap ? cap.textContent : im.alt;
    openDialog('#lightbox');
  });

  /* ---------------- client pages ---------------- */
  const ZL = S3.ZLABEL;
  function fillZones(slide, id) {
    $$('[data-runtime=zones]', slide || document).forEach(tb => {
      const fid = id || tb.dataset.floor, f = MX.floors.find(x => x.id === fid); if (!f) return;
      const rows = Object.entries(f.zones).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td><i class="dot" style="background:${S3.ZCOL[k]}"></i>${ZL[k] || k}</td><td class="num-right">${nf(v)}㎡</td></tr>`);
      if (f.units) rows.unshift(`<tr><td><i class="dot" style="background:${S3.UCOL[1]}"></i>세대 ${f.units}호</td><td class="num-right">${nf(f.unitArea)}㎡</td></tr>`);
      if (f.stalls) rows.unshift(`<tr><td><i class="dot" style="background:#f2f1ec;border:1px solid #bbb"></i>주차 ${f.stalls}면</td><td class="num-right">—</td></tr>`);
      tb.innerHTML = `<thead><tr><th>${fid} 구획</th><th class="num-right">면적</th></tr></thead><tbody>${rows.join('')}<tr class="sum"><td>층 합계 · 층고 ${f.height}m</td><td class="num-right">${nf(f.area)}㎡</td></tr></tbody>`;
    });
  }
  const A = MX.areas;
  const supply = ex => ex * (1 + A.kc), contract = ex => ex * (1 + A.kc + A.ko);
  const WIN = w => Object.entries(w || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}향 ${v.toFixed(1)}m`).join(' · ') || '—';
  function fillSubtypes() {
    const tb = $('[data-runtime=subtypes]'); if (!tb) return;
    tb.innerHTML = MX.subTypes.map(t => `<tr data-type="${t.type}"><td><b style="font-weight:600">${t.code}</b></td><td>${t.n}세대</td><td>${t.avg.toFixed(1)}㎡</td><td>${t.floors}</td></tr>`).join('');
  }
  function unitInfo(box, code) {
    const t = MX.subTypes.find(x => x.code === code), r = t.rep;
    const dyn = $('.unit-dyn', box);
    $$('.variants button', box).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.code === code)));
    $('.unit-body', dyn).innerHTML = `<h3 class="unit-title">${t.code} <small>${r.floor} ${r.name}호 기준 · ${t.n}세대 · ${t.floors}</small></h3>
      <div class="unit-nums"><div><b>${r.area.toFixed(1)}</b><span>주거전용 ㎡</span></div><div><b>${supply(r.area).toFixed(1)}</b><span>공급면적 ㎡</span></div><div><b>${(supply(r.area) / 3.3058).toFixed(1)}</b><span>공급 평</span></div><div><b>${r.W}×${r.D}</b><span>폭×깊이 m</span></div></div>
      <p class="unit-win">창 ${WIN(r.win)}</p>
      <div class="room-chips">${r.rooms.map(x => `<span>${esc(x[0])} <em>${x[1].toFixed(1)}</em></span>`).join('')}</div>`;
  }
  function fillUnitPages() {
    $$('[data-unit-type]').forEach(box => {
      const type = box.dataset.unitType, list = MX.subTypes.filter(t => t.type === type);
      const host = $('.scene-host', box.closest('.slide')), cfg = cfgOf(host);
      const start = (list.find(t => t.rep.floor === cfg.floor && t.rep.name === cfg.unit) || list[0]).code;
      const dyn = rt('div', 'unit-dyn'); dyn.innerHTML = `<div class="variants">${list.map(t => `<button data-code="${t.code}">${t.code.split('-')[1] || t.code}<small>${t.n}</small></button>`).join('')}</div><div class="unit-body"></div>`;
      box.prepend(dyn);
      $$('.variants button', dyn).forEach(b => b.onclick = () => {
        const t = MX.subTypes.find(x => x.code === b.dataset.code);
        if (activeHost === host) S3.set({ mode: 'unit', floor: t.rep.floor, unit: t.rep.name, view: 'unit', cut: true });
        $$('[data-cut-btn]', host).forEach(x => x.setAttribute('aria-pressed', 'false'));
        unitInfo(box, t.code);
      });
      unitInfo(box, start);
    });
  }
  function fillAreas() {
    const st = $('[data-runtime=area-stack]');
    if (st) {
      const parts = [['주거전용', A.excl, '#b97a45'], ['주거공용', A.resCommon, '#a7ada6'], ['비주거', A.nonres, '#5f9f86'], ['지하 기타공용', A.other, '#7d8a96']];
      const tot = parts.reduce((s, p) => s + p[1], 0);
      st.innerHTML = `<div class="stack">${parts.map(p => `<div style="width:${p[1] / tot * 100}%;background:${p[2]}">${p[0]}</div>`).join('')}</div>
      <div class="stack-legend">${parts.map(p => `<div><b>${nf(p[1])}㎡</b><span><i style="background:${p[2]}"></i>${p[0]} · ${(p[1] / tot * 100).toFixed(1)}%</span></div>`).join('')}</div>
      <div class="ratio-line"><span>공급면적 = 전용 ${nf(A.excl)} + 주거공용 ${nf(A.resCommon)} = <b>${nf(A.excl + A.resCommon)}㎡</b></span><span>전용률 <b>${(A.exclRatio * 100).toFixed(1)}%</b></span></div>`;
    }
    const fl = $('[data-runtime=area-floors]');
    if (fl) {
      const rows = A.rows.slice().reverse(), sum = k => rows.reduce((s, r) => s + r[k], 0);
      const cell = v => `<td class="num-right">${v ? nf(v) : '—'}</td>`;
      fl.innerHTML = rows.map(r => { const f = MX.floors.find(x => x.id === r.id); return `<tr><td>${r.id}</td>${cell(r.excl)}${cell(r.common)}${cell(r.comm)}${cell(r.nonres)}${cell(r.other)}<td class="num-right"><b style="font-weight:600">${nf(r.area)}</b></td><td class="num-right">${f.units || '—'}</td></tr>`; }).join('')
        + `<tr class="sum"><td>합계</td>${cell(sum('excl'))}${cell(sum('common'))}${cell(sum('comm'))}${cell(sum('nonres'))}${cell(sum('other'))}<td class="num-right">${nf(sum('area'))}</td><td class="num-right">${MX.units}</td></tr>`;
    }
    const ty = $('[data-runtime=area-types]');
    if (ty) ty.innerHTML = MX.subTypes.map(t => { const e = t.avg; return `<tr><td>${t.code}</td><td class="num-right">${t.n}</td><td class="num-right">${e.toFixed(2)}</td><td class="num-right">${(e * A.kc).toFixed(2)}</td><td class="num-right"><b style="font-weight:600">${supply(e).toFixed(2)}</b></td><td class="num-right">${(e * A.ko).toFixed(2)}</td><td class="num-right">${contract(e).toFixed(2)}</td><td class="num-right">${(supply(e) / 3.3058).toFixed(1)}</td></tr>`; }).join('');
    const bs = $('[data-runtime=area-basis]');
    if (bs) bs.textContent = `안분 계수: 주거공용 = 전용 × ${A.kc.toFixed(4)} (주거공용 ${nf(A.resCommon)}㎡ ÷ 전용 ${nf(A.excl)}㎡) · 기타공용 = 전용 × ${A.ko.toFixed(4)} (지하 ${nf(A.other)}㎡ × 주거 비율 ${(A.resShare * 100).toFixed(1)}% ÷ 전용). 면적은 타입 평균 전용 기준입니다.`;
  }
  function fillParking() {
    const tb = $('[data-runtime=parking]'); if (!tb) return;
    const P = MX.parking, ids = MX.parkingFloors || Object.keys(P); if (!ids.length) { tb.innerHTML = '<tbody><tr><td>주차구획이 없습니다.</td></tr></tbody>'; return; }
    const r = (lab, k) => `<tr><td>${lab}</td>${ids.map(i => `<td class="num-right">${P[i][k]}</td>`).join('')}<td class="num-right"><b style="font-weight:600">${ids.reduce((s, i) => s + P[i][k], 0)}</b></td></tr>`;
    tb.innerHTML = `<thead><tr><th>구분</th>${ids.map(i => `<th class="num-right">${i}</th>`).join('')}<th class="num-right">합계</th></tr></thead><tbody>${r('일반형 2.5×5.0', '일반형')}${r('확장형 2.6×5.2', '확장형')}${r('장애인 3.3×5.0', '장애인')}<tr class="sum"><td>합계</td>${ids.map(i => `<td class="num-right">${P[i].total}</td>`).join('')}<td class="num-right">${MX.stalls}</td></tr></tbody>`;
  }
  function fillSim() {
    const box = $('[data-runtime=parksim]'); if (!box) return;
    box.innerHTML = `<div class="sim-btns"><button data-sim="in">저녁 입차 피크 ▶</button><button data-sim="out">아침 출차 피크 ▶</button><button data-sim="mix">평시 ▶</button><button data-sim="pause">일시정지</button></div>
      <div class="sim-btns small"><button data-sim="reset0">빈 주차장</button><button data-sim="reset25">25% 점유</button><button data-sim="reset90">90% 점유</button><span>속도</span><button data-speed="1">1×</button><button data-speed="3" aria-pressed="true">3×</button><button data-speed="8">8×</button></div>
      <div class="sim-occ"><div class="sim-big"><b id="sim-parked">0</b><span>/ ${MX.stalls}면 주차</span></div><div class="sim-bar"><div id="sim-fill"></div></div><div class="sim-floors" id="sim-floors"></div></div>
      <div class="sim-stats" id="sim-stats"></div>`;
    const S = S3.sim;
    if (!S.ok) { box.innerHTML = '<p class="footnote">지상 1층에서 지하로 내려가는 램프와 지하 차로(aisle) 구역이 있어야 시뮬레이션을 만들 수 있습니다. 조닝에서 램프·차로 구역을 확인하세요.</p>'; return; }
    S.speed(3);
    $$('[data-sim]', box).forEach(b => b.onclick = () => {
      const k = b.dataset.sim;
      if (k === 'pause') S.pause(); else if (k.startsWith('reset')) S.reset(+k.slice(5) / 100); else S.play(k);
      $$('[data-sim]', box).forEach(x => x.setAttribute('aria-pressed', String(x === b && !k.startsWith('reset') && k !== 'pause')));
    });
    $$('[data-speed]', box).forEach(b => b.onclick = () => { S.speed(+b.dataset.speed); $$('[data-speed]', box).forEach(x => x.setAttribute('aria-pressed', String(x === b))); });
    const sec = m => Math.round(m / (10 / 3.6));
    S.onStat(st => {
      $('#sim-parked').textContent = st.parked; $('#sim-fill').style.width = (st.parked / st.total * 100) + '%';
      $('#sim-floors').innerHTML = Object.keys(st.per).map(id => `<span><b>${id}</b> ${st.per[id].busy}/${st.per[id].total}</span>`).join('');
      $('#sim-stats').innerHTML = `<div><b>${st.moving}</b><span>주행 중</span></div><div><b>${st.done.in}</b><span>입차 완료</span></div><div><b>${st.done.out}</b><span>출차 완료</span></div>
        <p>도로 진입부 → 주차면 주행거리 <b>${Math.round(st.minLen)}–${Math.round(st.maxLen)}m</b> (평균 ${Math.round(st.avgLen)}m) · 시속 10km 기준 <b>${sec(st.minLen)}–${sec(st.maxLen)}초</b></p>
        <p>${st.last ? `최근 입차: ${st.lastFloor} · ${Math.round(st.last)}m · 약 ${sec(st.last)}초` : '재생 버튼을 누르면 차량이 램프로 진입합니다.'}</p>`;
    });
  }

  /* ---------------- 2D drawings from plan data (comments #2, #3) ---------------- */
  const MD = DATA.model;
  const ptsStr = p => p.map(q => `${q[0].toFixed(2)},${(-q[1]).toFixed(2)}`).join(' ');
  const F4 = MD.floors.find(f => f.id === MX.typFloor) || MD.floors.find(f => f.lv > 0), F1 = MD.floors.filter(f => f.lv > 0).sort((a, b) => a.lv - b.lv)[0];
  const centroid = p => { let a = 0, x = 0, y = 0; for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length], c = x1 * y2 - x2 * y1; a += c; x += (x1 + x2) * c; y += (y1 + y2) * c; } return a ? [x / (3 * a), y / (3 * a)] : p[0]; };
  const isPodium = f => !(MX.floors.find(m => m.id === f.id) || {}).units;
  function sitePlanSVG(opt) {
    opt = opt || {};
    const xs = MD.site.map(p => p[0]), ys = MD.site.map(p => p[1]);
    const pad = opt.pad != null ? opt.pad : 14, x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad - (opt.extraSouth || 0), y1 = Math.max(...ys) + pad;
    const vb = `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`;
    const NS = 'vector-effect="non-scaling-stroke"';
    let g = '';
    g += `<defs><pattern id="hatch-${opt.id}" width="1.2" height="1.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="1.2" stroke="#c0563b" stroke-width=".25" opacity=".55"/></pattern><clipPath id="clip-${opt.id}"><rect x="${x0}" y="${-y1}" width="${x1 - x0}" height="${y1 - y0}"/></clipPath></defs><g clip-path="url(#clip-${opt.id})">`;
    if (opt.context !== false) {
      MD.ctx.roads.forEach(r => { if (r.p.length > 3) g += `<polygon points="${ptsStr(r.p)}" fill="#dcddd8" stroke="none"/>`; });
      MD.ctx.rail.forEach(r => { if (r.p.length > 3) g += `<polygon points="${ptsStr(r.p)}" fill="#ecd3c5" stroke="none"/>`; });
      MD.ctx.parcels.forEach(p => g += `<polyline points="${ptsStr(p)}" fill="none" stroke="#b9bdb6" stroke-width=".8" ${NS}/>`);
      MD.ctx.bld.forEach(b => { if (!b.on) g += `<polygon points="${ptsStr(b.p)}" fill="#e9e9e5" stroke="#c5c7c1" stroke-width=".8" ${NS}/>`; });
      const ZC = { '일반상업지역': '#c0563b', '제3종일반주거지역': '#d6a64a', '제1종일반주거지역': '#7aa06b' };
      if (opt.zoning) MD.ctx.zoning.forEach(z => g += `<polyline points="${ptsStr(z.p)}" fill="none" stroke="${ZC[z.n] || '#555'}" stroke-width="1.6" stroke-dasharray="6 4" ${NS}/>`);
    }
    g += `<polygon points="${ptsStr(MD.site)}" fill="#eef3ec" stroke="#2f5a4c" stroke-width="2" ${NS}/>`;
    if (opt.setback !== false) {
      const roadEdge = [[0.38, -3.0], [35.5, -3.0]];
      g += `<polygon points="${ptsStr([[0.38, -3.0], [35.5, -3.0], [35.54, 0], [0, 0]])}" fill="url(#hatch-${opt.id})" stroke="none"/>`;
      g += `<polyline points="${ptsStr(MD.env)}" fill="none" stroke="#6f8f69" stroke-width="1.4" stroke-dasharray="3 3" ${NS}/>`;
      g += `<polyline points="${ptsStr(roadEdge)}" fill="none" stroke="#2a2a2a" stroke-width="2.4" ${NS}/>`;
      g += `<polyline points="${ptsStr(MD.bline)}" fill="none" stroke="#c0563b" stroke-width="2.4" ${NS}/>`;
    }
    if (opt.building !== false) {
      g += `<polygon points="${ptsStr(F4.ol)}" fill="#2f5a4c" fill-opacity="${opt.fillOpacity || .28}" stroke="#2f5a4c" stroke-width="2.6" ${NS}/>`;
      if (opt.ramp !== false && F1 && F1.rp[0]) { const r = F1.rp[0]; g += `<rect x="${r[0]}" y="${-(r[1] + r[3])}" width="${r[2]}" height="${r[3]}" fill="#7d8995" fill-opacity=".35" stroke="#7d8995" stroke-width="1" stroke-dasharray="4 3" ${NS}/>`; }
    }
    g += '</g>';
    if (opt.labels !== false) {
      const T = (x, y, t, c, sz, anchor) => `<text x="${x}" y="${-y}" font-size="${sz || 2.1}" fill="${c || '#2a2a2a'}" text-anchor="${anchor || 'middle'}">${t}</text>`;
      g += T(18, -9.5, '망우로', '#555', 2.6);
      g += T(39.5, -2.2, '3.0m', '#c0563b', 1.9, 'start');
      g += `<line x1="38.6" y1="0" x2="38.6" y2="3" stroke="#c0563b" stroke-width="1" ${NS}/><line x1="37.6" y1="0" x2="39.6" y2="0" stroke="#c0563b" stroke-width="1" ${NS}/><line x1="37.6" y1="3" x2="39.6" y2="3" stroke="#c0563b" stroke-width="1" ${NS}/>`;
      if (opt.building !== false) { const c = centroid(F4.ol); g += T(c[0], c[1], `기준층 ${nf(MX.typArea)}㎡`, '#1f3f35', 2.2); if (F1 && F1.rp[0]) { const r = F1.rp[0]; g += T(r[0] + r[2] / 2, r[1] + Math.min(r[3], 22) / 2, '램프', '#55606a', 1.7); } }
      g += T(10, 50.5, '철도', '#a86a4c', 2.1);
      // north arrow (v axis is ~17.5° west of true north)
      const nx = x1 - 6, ny = y1 - 7;
      g += `<g transform="translate(${nx},${-ny}) rotate(17.5)"><polygon points="0,-3.4 1.3,1.6 0,0.8 -1.3,1.6" fill="#2a2a2a"/><text x="0" y="-4.2" font-size="2" text-anchor="middle">N</text></g>`;
      // scale bar 10m
      const sx = x0 + 3, sy = y0 + 3;
      g += `<line x1="${sx}" y1="${-sy}" x2="${sx + 10}" y2="${-sy}" stroke="#2a2a2a" stroke-width="2" ${NS}/><text x="${sx + 5}" y="${-sy - 1}" font-size="1.7" text-anchor="middle">10m</text>`;
    }
    return `<svg class="plan-svg" viewBox="${vb}" preserveAspectRatio="xMidYMid meet" font-family="inherit">${g}</svg>`;
  }
  function sectionSVG() {
    const fl = DATA.model.floors.filter(f => f.lv > 0), dn = DATA.model.floors.filter(f => f.lv < 0);
    const top = MX.height, L = MX.limits.height || 0;
    const exM = ((MX.built || []).map(r => (r[1] || '').match(/최고높이\s*([\d.]+)\s*m/)).find(Boolean) || [])[1], ex = exM ? +exM : 0;
    const depth = dn.length ? -Math.min(...dn.map(f => f.el)) : 0;
    const W = 120, H = 100, base = 86 - depth * 0, sc = 74 / Math.max(top, L, ex, 10), bx = 26, bw = 40;
    let g = '';
    g += `<line x1="4" y1="${base}" x2="${W - 4}" y2="${base}" stroke="#2a2a2a" stroke-width="1.2"/>`;
    if (ex) g += `<rect x="${bx + 46}" y="${base - ex * sc}" width="14" height="${ex * sc}" fill="none" stroke="#9aa09b" stroke-dasharray="2 2" stroke-width=".7"/><text x="${bx + 53}" y="${base - ex * sc - 1.2}" font-size="3.6" fill="#7a817c" text-anchor="middle">준공안 ${ex}m</text>`;
    if (L) g += `<line x1="4" y1="${base - L * sc}" x2="${W - 4}" y2="${base - L * sc}" stroke="#c0563b" stroke-width=".9" stroke-dasharray="4 2"/><text x="6" y="${base - L * sc - 1.5}" font-size="3.8" fill="#c0563b">최고높이 ${L}m</text>`;
    fl.forEach(f => { const y = base - (f.el + f.h) * sc; g += `<rect x="${bx}" y="${y}" width="${bw}" height="${f.h * sc}" fill="${isPodium(f) ? '#5f9f86' : '#e5c08c'}" stroke="#fff" stroke-width=".35"/>`; });
    if (depth) g += `<rect x="${bx}" y="${base}" width="${bw}" height="${Math.min(12, depth * sc)}" fill="#8f9ca6" opacity=".55"/><text x="${bx + bw / 2}" y="${base + 6}" font-size="3" fill="#2a2a2a" text-anchor="middle">${dn.length > 1 ? `${dn[dn.length - 1].id}–${dn[0].id}` : dn[0].id}</text>`;
    g += `<line x1="${bx + bw + 3}" y1="${base}" x2="${bx + bw + 3}" y2="${base - top * sc}" stroke="#2a2a2a" stroke-width=".6"/><text x="${bx + bw + 4.5}" y="${base - top * sc + 3}" font-size="4.2" font-weight="600">${top.toFixed(1)}m</text>`;
    const pod = fl.filter(isPodium), res = fl.filter(f => !isPodium(f));
    const rng = a => a.length > 1 ? `${a[0].lv}–${a[a.length - 1].lv}F` : `${a[0].lv}F`;
    if (pod.length) { const p = pod[pod.length - 1]; g += `<text x="${bx + bw / 2}" y="${base - (p.el + p.h) * sc / 2 + 1.2}" font-size="3.2" fill="#fff" text-anchor="middle">${rng(pod)} 기단</text>`; }
    if (res.length) g += `<text x="${bx + bw / 2}" y="${base - (res[0].el + top) * sc / 2 + 1.2}" font-size="3.4" text-anchor="middle" fill="#5a4630">${rng(res)} 주거</text>`;
    if (L && L > top) g += `<text x="${W - 6}" y="${base - (top + L) / 2 * sc}" font-size="3.6" fill="#2f5a4c" text-anchor="end">여유 ${(L - top).toFixed(1)}m</text><line x1="${W - 10}" y1="${base - top * sc}" x2="${W - 10}" y2="${base - L * sc}" stroke="#2f5a4c" stroke-width=".6"/>`;
    return `<svg class="sec-svg" viewBox="2 2 ${W - 4} ${H - 2}" font-family="inherit">${g}</svg>`;
  }
  function farSVG() {
    const site = MX.siteArea, fl = DATA.model.floors.filter(f => f.lv > 0), W = 300, H = 92, Lm = MX.limits;
    const span = Math.max(Lm.far || 0, MX.far, 100) * 1.04, sc = W / span;
    let x = 0, g = '';
    const lab = new Set([fl[0] && fl[0].id, fl[fl.length - 1] && fl[fl.length - 1].id, fl[Math.floor(fl.length / 2)] && fl[Math.floor(fl.length / 2)].id]);
    fl.forEach(f => { const a = MX.floors.find(m => m.id === f.id).area, w = a / site * 100 * sc; g += `<rect x="${x}" y="30" width="${w}" height="26" fill="${isPodium(f) ? '#5f9f86' : '#e5c08c'}" stroke="#fff" stroke-width=".6"/>${lab.has(f.id) ? `<text x="${x + w / 2}" y="47" font-size="6" text-anchor="middle" fill="#3a3a3a">${f.id}</text>` : ''}`; x += w; });
    const mark = (p, t, c, up) => `<line x1="${p * sc}" y1="${up ? 22 : 30}" x2="${p * sc}" y2="${up ? 62 : 66}" stroke="${c}" stroke-width="1" stroke-dasharray="${up ? '3 2' : ''}"/><text x="${p * sc}" y="${up ? 18 : 76}" font-size="7" fill="${c}" text-anchor="middle">${t}</text>`;
    g += `<rect x="0" y="30" width="${100 * sc}" height="26" fill="none" stroke="#2a2a2a" stroke-width="1.2"/>`;
    g += mark(100, '대지면적 = 100%', '#2a2a2a', false);
    if (Lm.farBase && Lm.farBase !== Lm.far) g += mark(Lm.farBase, `기본 ${Lm.farBase}%`, '#7a817c', true);
    if (Lm.far) g += mark(Lm.far, `상한 ${Lm.far}%`, '#c0563b', true);
    g += `<text x="0" y="90" font-size="6.5" fill="#5f6863">지상 ${fl.length}개 층 바닥 = 대지면적의 ${(MX.far / 100).toFixed(2)}배 (용적률 ${MX.far.toFixed(2)}%)</text>`;
    return `<svg class="far-svg" viewBox="-4 0 ${W + 12} ${H}" font-family="inherit">${g}</svg>`;
  }
  function fillDrawings() {
    $$('[data-runtime=site-svg]').forEach(b => { b.innerHTML = sitePlanSVG({ id: 'site', zoning: true, pad: 12, extraSouth: 4 }); });
    $$('[data-runtime=bcr-svg]').forEach(b => { b.innerHTML = sitePlanSVG({ id: 'bcr', context: false, labels: false, pad: 3, ramp: false, fillOpacity: .75 }); });
    $$('[data-runtime=far-svg]').forEach(b => { b.innerHTML = farSVG(); });
    $$('[data-runtime=height-svg]').forEach(b => { b.innerHTML = sectionSVG(); });
  }
  function fillAll() { fillWorkflow(); fillLogs(); fillTags(); fillMass(); fillComposition(); fillUnits(); fillFire(); fillCost(); fillLegal(); fillSchedule(); fillZones(); fillSubtypes(); fillUnitPages(); fillAreas(); fillParking(); fillSim(); fillDrawings(); }
  fillAll();

  /* ---------------- dialogs ---------------- */
  function openDialog(id) { priorFocus = document.activeElement; $(id).showModal(); }
  $$('dialog').forEach(d => {
    d.addEventListener('click', e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } });
    d.addEventListener('close', () => { priorFocus?.focus?.(); });
  });
  $$('[data-close]').forEach(b => b.onclick = () => b.closest('dialog').close());
  $$('[data-go]').forEach(b => b.onclick = () => go(+b.dataset.go - 1));
  $$('[data-goto]').forEach(b => b.onclick = () => { const i = slides.findIndex(s => s.dataset.ch === b.dataset.goto); if (i >= 0) go(i); });
  $('#prev').onclick = () => go(current - 1); $('#next').onclick = () => go(current + 1);

  let thumbs = null;
  function buildOverview() {
    if (!thumbs && S3) {
      thumbs = {};
      const keep = S3.config;
      slides.forEach((s, i) => { const h = $('.scene-host', s); if (h) thumbs[i] = S3.snapshot(cfgOf(h), 400, 225); });
      if (activeHost) S3.set(keep, true);
    }
    $('#overview-grid').innerHTML = slides.map((s, i) => `<button class="overview-item" data-page="${i}" aria-current="${i === current}"><small>${pad(i + 1)} / ${esc(($$('header span', s)[2] || $$('header span', s)[1] || {}).textContent || '')}</small>${thumbs && thumbs[i] ? `<img src="${thumbs[i]}" alt="">` : ($('img', s) ? `<img src="${$('img', s).src}" alt="">` : `<span class="overview-placeholder">${pad(i + 1)}</span>`)}<strong>${esc(slideTitle(s))}</strong></button>`).join('');
    $$('[data-page]').forEach(b => b.onclick = () => { $('#overview').close(); go(+b.dataset.page); });
  }
  function openOverview() { buildOverview(); openDialog('#overview'); }
  $('#overview-open').onclick = openOverview;

  let notesSlide = null;
  function showNotes() {
    const s = slides[current]; notesSlide = s;
    $('#detail-ch').textContent = ($$('header span', s)[2] || $$('header span', s)[1] || {}).textContent || '';
    $('#detail-title').textContent = slideTitle(s);
    const notes = $('.notes', s);
    $('#detail-body').innerHTML = notes ? notes.innerHTML : '<p>이 장표에는 상세 설명이 없습니다.</p>';
    if (document.body.classList.contains('editing')) $$('#detail-body p').forEach(p => p.contentEditable = 'true');
    $('#detail-source').textContent = '출처: ' + (($('footer span', s) || {}).textContent || '');
    openDialog('#details');
  }
  $('#details').addEventListener('close', () => {
    if (!document.body.classList.contains('editing') || !notesSlide) return;
    const notes = $('.notes', notesSlide); if (!notes) return;
    notes.innerHTML = $$('#detail-body p').map(p => `<p>${esc(p.textContent)}</p>`).join('');
  });
  $('#notes-open').onclick = showNotes;
  async function full() { try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); } catch (e) { toast('브라우저의 전체 화면 메뉴를 사용해 주세요.'); } }
  $('#fullscreen').onclick = full;

  /* ---------------- print: 3D → still image ---------------- */
  function printShots() {
    if (!S3) return;
    const keep = S3.config;
    slides.forEach(s => { const h = $('.scene-host', s); if (!h) return; const cin = s.classList.contains('cinematic'); const url = S3.snapshot(cfgOf(h), cin ? 1600 : 1100, cin ? 900 : 760); if (url) { const im = rt('img', 'scene-shot'); im.src = url; im.alt = ''; h.appendChild(im); } });
    if (activeHost) S3.set(keep, true);
  }
  addEventListener('beforeprint', printShots);
  addEventListener('afterprint', () => $$('.scene-shot').forEach(e => e.remove()));
  $('#print').onclick = () => window.print();

  /* ---------------- keys / touch ---------------- */
  addEventListener('keydown', e => {
    if ($('dialog[open]') || e.altKey || e.ctrlKey || e.metaKey || e.target.closest('input,select,textarea,[contenteditable=true]')) return;
    const k = e.key.toLowerCase(); if (k === ' ' && e.target.closest('button')) return;
    if (['arrowright', 'pagedown', ' '].includes(k)) { e.preventDefault(); go(current + 1); }
    else if (['arrowleft', 'pageup'].includes(k)) { e.preventDefault(); go(current - 1); }
    else if (k === 'home') { e.preventDefault(); go(0); } else if (k === 'end') { e.preventDefault(); go(slides.length - 1); }
    else if (k === 'o') openOverview(); else if (k === 'n') showNotes(); else if (k === 'f') full(); else if (k === 'c' && window.deckComments) window.deckComments.toggle();
  });
  addEventListener('hashchange', () => go((parseInt(location.hash.slice(1)) || 1) - 1, false));
  let start = null;
  $('#stage').addEventListener('touchstart', e => { if (e.target.closest('input,select,button,.schedule-scroll,.scene-host')) return; start = [e.touches[0].clientX, e.touches[0].clientY]; }, { passive: true });
  $('#stage').addEventListener('touchend', e => { if (!start) return; const dx = e.changedTouches[0].clientX - start[0], dy = e.changedTouches[0].clientY - start[1]; if (Math.abs(dx) > 85 && Math.abs(dx) > Math.abs(dy) * 2) go(current + (dx < 0 ? 1 : -1)); start = null; }, { passive: true });

  /* ---------------- edit mode ---------------- */
  const EDITABLE = ['h1', 'h2', 'h3', 'p', 'td', 'th', 'figcaption', '.numbers strong', '.numbers span', 'footer span:first-child', '.flow span:not(.arrow)', '.decisions b', '.cinema-kicker'].map(s => '.slide ' + s).join(',');
  function setEditing(on) {
    if (on && document.body.classList.contains('commenting') && window.deckComments) window.deckComments.toggle();
    document.body.classList.toggle('editing', on);
    $('#deck-editor').hidden = !on;
    $('#edit-toggle').setAttribute('aria-pressed', String(on));
    $$(EDITABLE).forEach(el => {
      if (el.closest('[data-runtime],button,.notes')) return;
      if (on) el.contentEditable = 'true'; else el.removeAttribute('contenteditable');
    });
    setTimeout(() => S3 && S3.resize(), 60);
  }
  $('#edit-toggle').onclick = () => setEditing(!document.body.classList.contains('editing'));
  $('#edit-close').onclick = () => setEditing(false);
  $('#edit-notes').onclick = showNotes;
  function saveHTML(label) {
    const clone = document.documentElement.cloneNode(true);
    clone.querySelectorAll('[data-rt],canvas,.scene-tip').forEach(e => e.remove());
    clone.querySelectorAll('[data-runtime]').forEach(e => { e.innerHTML = ''; });
    clone.querySelectorAll('[contenteditable]').forEach(e => e.removeAttribute('contenteditable'));
    clone.querySelectorAll('.slide').forEach(s => { s.classList.remove('active'); s.removeAttribute('aria-hidden'); s.removeAttribute('id'); });
    clone.querySelectorAll('dialog').forEach(d => d.removeAttribute('open'));
    clone.querySelector('#overview-grid').innerHTML = '';
    clone.querySelector('body').classList.remove('editing', 'commenting');
    clone.querySelectorAll('.c-hover').forEach(e => e.classList.remove('c-hover'));
    if (clone.querySelector('#comment-panel')) clone.querySelector('#comment-panel').hidden = true;
    clone.querySelector('#deck-editor').hidden = true;
    clone.querySelector('#toast').className = '';
    clone.querySelector('title').textContent = TITLE;
    clone.removeAttribute('style');
    const html = '<!doctype html>\n' + clone.outerHTML;
    const b = new Blob([html], { type: 'text/html' }), u = URL.createObjectURL(b), a = document.createElement('a');
    const d = new Date(), stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
    a.href = u; a.download = `${SLUG}_제안서_${label || '수정본'}_${stamp}.html`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 8000);
  }
  window.deck.saveHTML = saveHTML;
  $('#edit-save').onclick = () => { saveHTML('수정본'); $('#edit-status').textContent = '수정본을 내려받았습니다. 원본 파일은 바뀌지 않습니다.'; };

  go((parseInt(location.hash.slice(1)) || 1) - 1, false);
})();
