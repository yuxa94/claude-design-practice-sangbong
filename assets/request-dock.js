/* 공통 수정 요청 바 — 모든 작업 페이지 아래 가운데.
   - 채팅처럼 입력해 요청 남기기
   - 🖱 마우스 모드: 페이지의 요소를 눌러 대상으로 붙이기 (여러 개 가능, Esc로 끝)
   - 저장: POST /api/requests → requests/requests.json (서버가 꺼져 있으면 브라우저에 보관했다가 다시 보냄)
   페이지별 이름표: <script src="assets/request-dock.js" data-page="plan" data-title="계획설계"></script>
   페이지가 window.requestDescribe(el) 를 두면 {label, id, floor, ...} 로 대상 이름을 더 정확히 붙입니다. */
(function(){
  if(window.__requestDock) return; window.__requestDock=true;
  const me=document.currentScript||document.querySelector('script[src*="request-dock.js"]');
  const PAGE=(me&&me.dataset.page)||location.pathname.split('/').pop().replace(/\.html$/,'')||'hub';
  const TITLE=(me&&me.dataset.title)||document.title;
  const API=location.protocol.startsWith('http')?'/api/requests':null;
  const QKEY='design-requests-queue';
  const css=`
  .rq-dock{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:2147483000;width:min(600px,calc(100vw - 28px));font:13px/1.45 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif;color:#1d2326}
  .rq-bar{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.97);border:1.5px solid #f0507a;border-radius:16px;padding:6px 6px 6px 8px;box-shadow:0 10px 30px rgba(0,0,0,.22)}
  .rq-ico{flex:none;width:34px;height:34px;border-radius:11px;border:1.5px solid #d9d5cc;background:#fff;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;color:#5f6b70;position:relative}
  .rq-ico:hover{border-color:#f0507a;color:#f0507a} .rq-ico.on{background:linear-gradient(135deg,#ff7a45,#e8467c);border-color:transparent;color:#fff}
  .rq-ico b{position:absolute;top:-6px;right:-6px;background:#f0507a;color:#fff;border-radius:999px;font-size:10px;padding:0 5px;line-height:16px;min-width:16px;text-align:center}
  .rq-in{flex:1;min-width:0;border:none;outline:none;background:transparent;font:inherit;font-size:14px;padding:6px 4px;color:#1d2326}
  .rq-send{flex:none;border:none;border-radius:11px;padding:8px 13px;font-weight:800;color:#fff;background:linear-gradient(135deg,#ff7a45,#e8467c);cursor:pointer;font:inherit;font-weight:800}
  .rq-send:disabled{opacity:.45;cursor:default}
  .rq-chips{display:flex;flex-wrap:wrap;gap:5px;margin:0 6px 6px;max-height:70px;overflow:auto}
  .rq-chip{display:inline-flex;align-items:center;gap:4px;background:#fff;border:1px solid rgba(240,80,122,.45);border-radius:999px;padding:2px 9px;font-size:11.5px;box-shadow:0 2px 6px rgba(0,0,0,.12)}
  .rq-chip button{border:none;background:none;cursor:pointer;color:#5f6b70;font-size:13px;padding:0 0 0 2px;line-height:1}
  .rq-hint{position:absolute;left:50%;transform:translateX(-50%);bottom:calc(100% + 8px);background:#1d2326;color:#fff;border-radius:8px;padding:5px 10px;font-size:12px;white-space:nowrap;display:none}
  .rq-hint.on{display:block}
  .rq-box{position:fixed;z-index:2147482999;pointer-events:none;border:2px solid #f0507a;background:rgba(240,80,122,.10);border-radius:4px;display:none;transition:all .05s}
  .rq-tag{position:fixed;z-index:2147483001;pointer-events:none;background:#f0507a;color:#fff;font:700 11px/1.3 -apple-system,"Apple SD Gothic Neo",sans-serif;padding:2px 7px;border-radius:6px;display:none;max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  body.rq-picking, body.rq-picking *{cursor:crosshair!important}
  .rq-panel{position:absolute;left:0;right:0;bottom:calc(100% + 8px);background:#fff;border:1px solid #d9d5cc;border-radius:14px;box-shadow:0 16px 40px rgba(0,0,0,.25);max-height:min(56vh,460px);overflow:auto;padding:10px 12px;display:none}
  .rq-panel.on{display:block}
  .rq-ph{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-weight:800}
  .rq-ph .sp{flex:1} .rq-ph button{border:1px solid #d9d5cc;background:#fff;border-radius:8px;padding:2px 8px;font:inherit;font-size:12px;cursor:pointer}
  .rq-item{border:1px solid #e3dfd6;border-left:4px solid #f0507a;border-radius:10px;padding:7px 9px;margin:6px 0;font-size:12.5px}
  .rq-item.done{border-left-color:#1f7a4c;opacity:.75}
  .rq-item .h{display:flex;gap:6px;align-items:center;color:#5f6b70;font-size:11px;margin-bottom:3px}
  .rq-item .h b{background:#f0507a;color:#fff;border-radius:999px;padding:0 7px;font-size:11px} .rq-item.done .h b{background:#1f7a4c}
  .rq-item .t{white-space:pre-wrap;color:#1d2326} .rq-item .r{background:#f2f4f5;border-radius:8px;padding:5px 8px;margin-top:5px}
  .rq-item .a{display:flex;gap:5px;margin-top:5px} .rq-item .a button{border:1px solid #d9d5cc;background:#fff;border-radius:7px;padding:1px 7px;font:inherit;font-size:11.5px;cursor:pointer}
  .rq-flash{outline:3px solid #f0507a!important;outline-offset:2px;transition:outline .2s}
  @media (prefers-color-scheme: dark){ .rq-bar,.rq-panel,.rq-chip,.rq-ico{background:#1d2225;color:#e7ecee;border-color:#3a4246} .rq-in{color:#e7ecee} .rq-item{border-color:#333b3f} .rq-item .t{color:#e7ecee} .rq-item .r{background:#262d31} .rq-ph button,.rq-item .a button{background:#262d31;color:#e7ecee;border-color:#3a4246} }`;
  const st=document.createElement('style'); st.textContent=css; document.head.appendChild(st);
  const dock=document.createElement('div'); dock.className='rq-dock'; dock.setAttribute('data-rq','1');
  dock.innerHTML=`<div class="rq-panel" id="rqPanel"></div><div class="rq-hint" id="rqHint">요소를 누르면 대상으로 붙습니다 · 여러 개 가능 · Esc로 끝</div>
    <div class="rq-chips" id="rqChips"></div>
    <div class="rq-bar">
      <button class="rq-ico" id="rqMouse" title="마우스로 요소 골라 요청 달기 (Esc로 끝)"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 3l14 7-6 2-2 6z"/><path d="M13 13l5 5"/></svg></button>
      <input class="rq-in" id="rqIn" placeholder="수정 요청을 입력하세요 — 예) 이 표의 기준값을 서울시 조례로 바꿔줘" autocomplete="off">
      <button class="rq-send" id="rqSend" disabled>요청 ↵</button>
      <button class="rq-ico" id="rqList" title="이 페이지의 수정 요청 목록"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H9l-5 4z"/></svg><b id="rqCnt" style="display:none">0</b></button>
    </div>`;
  const box=document.createElement('div'); box.className='rq-box'; const tag=document.createElement('div'); tag.className='rq-tag';
  const mount=()=>{ document.body.appendChild(dock); document.body.appendChild(box); document.body.appendChild(tag); };
  if(document.body) mount(); else document.addEventListener('DOMContentLoaded',mount);
  const $=id=>dock.querySelector('#'+id);
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let picking=false, targets=[], items=[], showAll=false, hoverEl=null;
  // ---- 대상 설명
  function selectorOf(el){ if(el.id) return '#'+CSS.escape(el.id); const h=el.closest('[data-id]'); if(h&&h.getAttribute('data-id')) return `[data-id="${h.getAttribute('data-id')}"]`;
    const parts=[]; let e=el; for(let i=0;e&&e.nodeType===1&&i<5;i++){ if(e.id){ parts.unshift('#'+CSS.escape(e.id)); break; } const p=e.parentElement; if(!p) break; const same=[...p.children].filter(c=>c.tagName===e.tagName); parts.unshift(e.tagName.toLowerCase()+(same.length>1?`:nth-of-type(${same.indexOf(e)+1})`:'')); e=p; }
    return parts.join(' > '); }
  function describe(el){ let extra=null; try{ extra=window.requestDescribe? window.requestDescribe(el) : null; }catch(e){}
    const text=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim().slice(0,80);
    const near=(()=>{ let e=el; for(let i=0;e&&i<6;i++,e=e.parentElement){ const h=e.querySelector&&e.querySelector('h1,h2,h3,h4,b'); if(h&&h.innerText) return h.innerText.trim().slice(0,40); } return ''; })();
    const label=(extra&&extra.label)||(text?(text.length>40?text.slice(0,40)+'…':text):(el.getAttribute('title')||el.getAttribute('aria-label')||el.tagName.toLowerCase()));
    const r=el.getBoundingClientRect();
    return Object.assign({selector:selectorOf(el),tag:el.tagName.toLowerCase(),label,text,near,rect:[Math.round(r.left+scrollX),Math.round(r.top+scrollY),Math.round(r.width),Math.round(r.height)]},extra||{}); }
  // ---- 마우스 모드
  function setPicking(on){ picking=on; $('rqMouse').classList.toggle('on',on); $('rqHint').classList.toggle('on',on); document.body.classList.toggle('rq-picking',on); if(!on){ box.style.display='none'; tag.style.display='none'; hoverEl=null; } }
  const inDock=e=>e&&e.closest&&(e.closest('[data-rq]')||e===box||e===tag);
  function topEl(x,y){ const els=document.elementsFromPoint(x,y).filter(e=>!inDock(e)&&e!==document.documentElement&&e!==document.body); return els[0]||null; }
  window.addEventListener('pointermove',e=>{ if(!picking) return; const el=topEl(e.clientX,e.clientY); if(!el){ box.style.display='none'; tag.style.display='none'; return; } hoverEl=el; const r=el.getBoundingClientRect();
    Object.assign(box.style,{display:'block',left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'}); const d=describe(el); tag.textContent=d.label; Object.assign(tag.style,{display:'block',left:Math.max(4,r.left)+'px',top:Math.max(4,r.top-20)+'px'}); },true);
  ['pointerdown','mousedown','mouseup','pointerup','click','dblclick'].forEach(t=>window.addEventListener(t,e=>{ if(!picking||inDock(e.target)) return; e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    if(t==='click'){ const el=topEl(e.clientX,e.clientY); if(!el) return; const d=describe(el); if(!targets.some(x=>x.selector===d.selector&&x.label===d.label)) targets.push(d); renderChips(); $('rqIn').focus(); } },true));
  window.addEventListener('keydown',e=>{ if(e.key==='Escape'&&picking){ e.stopPropagation(); setPicking(false); } },true);
  function renderChips(){ $('rqChips').innerHTML=targets.map((t,i)=>`<span class="rq-chip">🎯 ${esc(t.label)}<button data-i="${i}" title="빼기">×</button></span>`).join(''); upd(); }
  dock.addEventListener('click',e=>{ const b=e.target.closest('.rq-chip button'); if(b){ targets.splice(+b.dataset.i,1); renderChips(); } });
  const upd=()=>{ $('rqSend').disabled=!$('rqIn').value.trim(); };
  // ---- 저장
  const queue=()=>{ try{ return JSON.parse(localStorage.getItem(QKEY)||'[]'); }catch(e){ return []; } };
  const setQueue=q=>{ try{ localStorage.setItem(QKEY,JSON.stringify(q)); }catch(e){} };
  async function post(body){ if(!API) throw new Error('로컬 서버 주소(http://localhost:8766/)로 열면 저장됩니다'); const r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const j=await r.json(); if(!r.ok||!j.ok) throw new Error(j.error||r.status); return j; }
  async function flush(){ const q=queue(); if(!q.length||!API) return; const rest=[]; for(const it of q){ try{ await post({op:'add',item:it}); }catch(e){ rest.push(it); } } setQueue(rest); if(rest.length<q.length){ toast(`보관 중이던 요청 ${q.length-rest.length}개를 저장했습니다`); load(); } }
  async function send(){ const text=$('rqIn').value.trim(); if(!text) return; const item={id:'r'+Date.now().toString(36)+Math.random().toString(36).slice(2,5),page:PAGE,pageTitle:TITLE,url:location.pathname,text,targets,created:new Date().toISOString(),author:'사용자'};
    try{ const j=await post({op:'add',item}); items=j.requests; toast('요청 저장됨 — 대화창에서 “요청 반영해줘”라고 하면 Claude가 처리합니다'); }
    catch(e){ const q=queue(); q.push(item); setQueue(q); toast('서버에 연결되지 않아 이 브라우저에 보관했습니다 — 연결되면 자동으로 저장합니다'); }
    $('rqIn').value=''; targets=[]; renderChips(); setPicking(false); render(); }
  $('rqIn').addEventListener('input',upd); $('rqIn').addEventListener('keydown',e=>{ e.stopPropagation(); if(e.key==='Enter'&&!e.isComposing){ e.preventDefault(); send(); } if(e.key==='Escape'){ $('rqIn').blur(); } });
  $('rqIn').addEventListener('keyup',e=>e.stopPropagation()); $('rqIn').addEventListener('keypress',e=>e.stopPropagation());
  $('rqSend').onclick=send; $('rqMouse').onclick=()=>setPicking(!picking);
  $('rqList').onclick=()=>{ $('rqPanel').classList.toggle('on'); render(); };
  // ---- 목록
  async function load(){ if(!API) return; try{ const j=await (await fetch(API,{cache:'no-store'})).json(); items=j.requests||[]; render(); }catch(e){} }
  function render(){ const mine=items.filter(r=>showAll||r.page===PAGE); const open=items.filter(r=>r.page===PAGE&&r.status!=='done').length+queue().filter(r=>r.page===PAGE).length;
    const c=$('rqCnt'); c.style.display=open?'':'none'; c.textContent=open;
    if(!$('rqPanel').classList.contains('on')) return;
    const list=mine.slice().sort((a,b)=>(a.status==='done')-(b.status==='done')||b.n-a.n); const q=queue().filter(r=>showAll||r.page===PAGE);
    $('rqPanel').innerHTML=`<div class="rq-ph">💬 ${showAll?'모든 페이지':'이 페이지'} 수정 요청 <span style="font-weight:400;color:#5f6b70">열림 ${list.filter(r=>r.status!=='done').length} · 전체 ${list.length}</span><span class="sp"></span><button data-act="all">${showAll?'이 페이지만':'모든 페이지'}</button><button data-act="close">닫기</button></div>
      ${q.map(r=>`<div class="rq-item"><div class="h"><b>보관</b>${esc(r.pageTitle)} · 서버 연결 대기</div><div class="t">${esc(r.text)}</div></div>`).join('')}
      ${list.length?list.map(r=>`<div class="rq-item ${r.status==='done'?'done':''}" data-id="${esc(r.id)}"><div class="h"><b>#${r.n}</b>${showAll?esc(r.pageTitle)+' · ':''}${new Date(r.created).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})} · ${r.status==='done'?'반영됨':'대기 중'}</div>
        <div class="t">${esc(r.text)}</div>${(r.targets||[]).length?`<div style="margin-top:3px;color:#5f6b70;font-size:11.5px">🎯 ${(r.targets||[]).map(t=>esc(t.label)).join(' · ')}</div>`:''}${r.reply?`<div class="r"><b style="color:#f0507a">✦ Claude</b> ${esc(r.reply)}</div>`:''}
        <div class="a">${r.page===PAGE&&(r.targets||[]).length?'<button data-act="show">위치 보기</button>':''}<button data-act="done">${r.status==='done'?'다시 열기':'완료 처리'}</button><button data-act="del">삭제</button></div></div>`).join(''):'<div style="color:#5f6b70;padding:6px 2px">아직 요청이 없습니다. 아래 입력창에 쓰거나, 🖱 버튼으로 요소를 골라 요청하세요.</div>'}`; }
  dock.addEventListener('click',async e=>{ const b=e.target.closest('button[data-act]'); if(!b) return; const act=b.dataset.act;
    if(act==='close'){ $('rqPanel').classList.remove('on'); return; } if(act==='all'){ showAll=!showAll; render(); return; }
    const card=b.closest('.rq-item'); const r=items.find(x=>x.id===card.dataset.id); if(!r) return;
    try{ if(act==='done'){ const done=r.status!=='done'; const j=await post({op:'update',id:r.id,patch:{status:done?'done':'open',resolvedAt:done?new Date().toISOString():null,reopened:!done||undefined}}); items=j.requests; }
      if(act==='del'){ if(!confirm(`요청 #${r.n}을 삭제할까요?`)) return; const j=await post({op:'delete',id:r.id}); items=j.requests; }
      if(act==='show'){ (r.targets||[]).forEach(t=>{ let el=null; try{ el=document.querySelector(t.selector); }catch(err){} if(window.requestFocus&&window.requestFocus(t)) return; if(el){ el.scrollIntoView({block:'center',behavior:'smooth'}); el.classList.add('rq-flash'); setTimeout(()=>el.classList.remove('rq-flash'),1800); } }); }
      render(); }catch(err){ toast('저장 실패: '+err.message); } });
  let tt; function toast(t){ let el=document.getElementById('rqToast'); if(!el){ el=document.createElement('div'); el.id='rqToast'; el.setAttribute('data-rq','1'); Object.assign(el.style,{position:'fixed',left:'50%',bottom:'78px',transform:'translateX(-50%)',background:'#1d2326',color:'#fff',padding:'7px 13px',borderRadius:'8px',font:'12.5px -apple-system,"Apple SD Gothic Neo",sans-serif',zIndex:2147483002,transition:'opacity .2s'}); document.body.appendChild(el); } el.textContent=t; el.style.opacity='1'; clearTimeout(tt); tt=setTimeout(()=>el.style.opacity='0',2600); }
  load(); flush(); setInterval(()=>{ flush(); load(); },15000);
  window.requestDock={ pick:()=>setPicking(true), reload:load };
})();
