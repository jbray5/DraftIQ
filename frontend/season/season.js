/* SeasonIQ shared runtime: nav, fetch, avatars, animated numbers, sparklines. */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const pct = v => (v * 100).toFixed(1) + '%';

async function j(u){ const r = await fetch(u); const d = await r.json();
  if (d.error) throw new Error(d.error);
  if (d.stale && !$('staleBanner')) document.body.insertAdjacentHTML('afterbegin',
    `<div id="staleBanner" style="background:#4a3200;color:#ffcf7d;padding:6px 14px;font-size:12px;letter-spacing:.03em">⚠ ESPN unreachable — showing the last good pull (${Math.max(1,Math.round((d.staleAgeSec||60)/60))} min old). Hit ↻ to retry.</div>`);
  return d; }

function seasonNav(active){
  const pages = [['index','DASHBOARD'],['matchup','MATCHUP'],['wire','WIRE'],['standings','STANDINGS'],['trades','TRADES'],['performance','RESULTS']];
  document.body.insertAdjacentHTML('afterbegin', `<header>
    <h1><b>SEASON</b>IQ</h1>
    <nav>${pages.map(([p,l])=>`<a href="${p}.html" class="${p===active?'on':''}">${l}</a>`).join('')}</nav>
    <span class="tag" id="hdrTag">Derek Jeter's Taco Hole</span>
    <span style="flex:1"></span>
    <button onclick="location.reload()" title="refresh">↻</button>
    <a href="/index.html" class="tag">→ DraftIQ</a>
  </header>`);
}

function setHdr(team, week, fetchedAt){
  const el = $('hdrTag');
  if (el && team) el.textContent = `Derek Jeter's Taco Hole · ${team} · week ${week}`
    + (fetchedAt ? ` · as of ${fetchedAt}` : '');
}

/* ---------- animated count-up (respects reduced-motion) ---------- */
function countUp(el, val, {dec=0, suffix=''}={}){
  if (!el) return;
  const target = +val || 0;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches){ el.textContent = target.toFixed(dec)+suffix; return; }
  const t0 = performance.now(), dur = 700, from = 0;
  const tick = t => {
    const k = Math.min(1,(t-t0)/dur), e = 1-Math.pow(1-k,3);
    el.textContent = (from+(target-from)*e).toFixed(dec)+suffix;
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* ---------- headshots: one lazy /api/board fetch, name-keyed ---------- */
let _hsP = null;
function HS(){
  if (_hsP) return _hsP;
  _hsP = fetch('/api/board').then(r=>r.json()).then(rows=>{
    const m = new Map();
    (rows||[]).forEach(p=>{ if(p.name) m.set(_nk(p.name), p.headshot||null); });
    return m;
  }).catch(()=>new Map());
  return _hsP;
}
const _nk = s => String(s||'').toLowerCase().replace(/\b(jr|sr|ii|iii|iv|v)\b/g,'').replace(/[^a-z]/g,'');
function avHTML(map, name){
  const url = map && map.get(_nk(name));
  if (url) return `<img class="av" src="${esc(url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'av-i',textContent:'${esc((name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase())}'))">`;
  const init = (name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase();
  return `<span class="av-i">${esc(init)}</span>`;
}

/* ---------- sparkline (single series, endpoint dot, no axes) ---------- */
function spark(canvas, values, color='#D08004'){
  if (!canvas || !values || values.length < 2) return;
  const dpr = devicePixelRatio||1, r = canvas.getBoundingClientRect();
  const W = r.width||canvas.parentElement.getBoundingClientRect().width||160, H = r.height||34;
  canvas.width = W*dpr; canvas.height = H*dpr;
  const x = canvas.getContext('2d'); x.scale(dpr,dpr);
  const lo = Math.min(...values), hi = Math.max(...values), sp = (hi-lo)||1;
  const X = i => 2 + i*(W-8)/(values.length-1);
  const Y = v => H-5 - (v-lo)*(H-10)/sp;
  x.beginPath(); values.forEach((v,i)=> i?x.lineTo(X(i),Y(v)):x.moveTo(X(i),Y(v)));
  x.strokeStyle = color; x.lineWidth = 2; x.lineJoin='round'; x.stroke();
  x.beginPath(); x.arc(X(values.length-1), Y(values.at(-1)), 3, 0, 7);
  x.fillStyle = color; x.fill();
}

/* ---------- market heat chip (display-only tiebreak) ---------- */
function heatTag(p){
  if (p == null || p.own == null) return '';
  const d = p.ownDelta;
  const arrow = d==null ? '' : d > 0.4 ? ` <b class="good">▲${d}</b>` : d < -0.4 ? ` <b class="bad">▼${Math.abs(d)}</b>` : '';
  return ` <span class="chip">${Math.round(p.own)}% rostered${arrow}</span>`;
}

/* ---------- shared wire renderer ---------- */
function renderWire(d, el, compact){
  let html = '';
  if (d.allClear) html += '<div class="allclear">✓ ALL CLEAR — nothing on the wire needs action.</div>';
  (d.actions || []).forEach(a => {
    const drop = a.drop ? ` · drop <b class="bad">${esc(a.drop.name)}</b>` : '';
    const net = a.netVorpWk!=null ? `${a.netVorpWk>=0?'+':''}${a.netVorpWk}/wk <span class="dim">(${a.netVorp>=0?'+':''}${a.netVorp} ROS)</span>`
                                  : `${a.netVorp>=0?'+':''}${a.netVorp} ROS`;
    html += `<div class="action"><div class="hd">▶ ${a.type}${a.submitOrder?' <span class="dim">(claim #'+a.submitOrder+')</span>':''}: ${esc(a.add.name)} <span class="pos">${a.add.pos}</span>${drop} · ${net}${heatTag(a.add)}</div>
      <div class="muted">${esc(a.why)}</div>`
      + (a.claim?`<div class="${a.claim.aheadWanting?'muted':'dim'}">◈ priority ${a.claim.myPriority}: ${esc(a.claim.verdict)}</div>`:'')
      + (a.dropNote?`<div class="dim">⚕ ${esc(a.dropNote)}</div>`:'')
      + ((a.ladder&&a.ladder.length)?`<div class="dim">if outclaimed: ${a.ladder.map(l=>esc(l.name)+' (#'+l.rank+')').join(' → ')}</div>`:'')
      + `<div class="dim">▸ ${esc(a.urgency)}</div></div>`;
  });
  html += `<div class="rowline dim">${esc((d.stream||{}).line||'')}</div>`;
  if ((d.stream||{}).planLine) html += `<div class="rowline dim">↳ ${esc(d.stream.planLine)}</div>`;
  if (!compact){
    (d.watchlist||[]).forEach(m => { html += `<div class="rowline">watch: <b>${esc(m.add.name)}</b> over ${esc(m.drop.name)} <span class="dim">(+${m.netVorpWk!=null?m.netVorpWk+'/wk':m.netVorp})</span>${heatTag(m.add)}</div>`; });
    (d.injuryFlags||[]).forEach(f => {
      html += `<div class="rowline">⚕ <b class="bad">${esc(f.name)}</b> <span class="pos">${f.pos}</span> ${f.injury}${f.newsDate?' <span class="dim">['+f.newsDate+']</span>':''}`
        + (f.news ? `<div class="dim">${esc(f.news)}</div>` : '') + '</div>';
    });
  }
  el.innerHTML = html;
}
