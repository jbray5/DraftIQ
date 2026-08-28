/* SeasonIQ shared runtime: nav injection, fetch helper, tiny utils. */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const pct = v => (v * 100).toFixed(1) + '%';

async function j(u){ const r = await fetch(u); const d = await r.json();
  if (d.error) throw new Error(d.error); return d; }

function seasonNav(active){
  const pages = [['index','DASHBOARD'],['matchup','MATCHUP'],['wire','WIRE'],['standings','STANDINGS'],['trades','TRADES'],['performance','RESULTS']];
  document.body.insertAdjacentHTML('afterbegin', `<header>
    <h1><b>SEASON</b>IQ</h1>
    <nav>${pages.map(([p,l])=>`<a href="${p}.html" class="${p===active?'on':''}">${l}</a>`).join('')}</nav>
    <span class="tag" id="hdrTag">Derek Jeter's Taco Hole</span>
    <span style="flex:1"></span>
    <button onclick="location.reload()">↻</button>
    <a href="/index.html" class="tag">→ DraftIQ</a>
  </header>`);
}

function setHdr(team, week){
  const el = $('hdrTag');
  if (el && team) el.textContent = `Derek Jeter's Taco Hole · ${team} · week ${week}`;
}

/* shared renderers used by more than one page */
function renderWire(d, el, compact){
  let html = '';
  if (d.allClear) html += '<div class="allclear">✓ ALL CLEAR — nothing on the wire needs action.</div>';
  (d.actions || []).forEach(a => {
    const drop = a.drop ? ` · drop <b class="bad">${esc(a.drop.name)}</b>` : '';
    html += `<div class="action"><div class="hd">▶ ${a.type}: ${esc(a.add.name)} (${a.add.pos})${drop} · NET ${a.netVorp>=0?'+':''}${a.netVorp}</div>
      <div class="muted">${esc(a.why)}</div><div class="dim">▸ ${esc(a.urgency)}</div></div>`;
  });
  html += `<div class="rowline dim">${esc((d.stream||{}).line||'')}</div>`;
  if (!compact){
    (d.watchlist||[]).forEach(m => { html += `<div class="rowline">watch: ${esc(m.add.name)} over ${esc(m.drop.name)} <span class="dim">(+${m.netVorp})</span></div>`; });
    (d.injuryFlags||[]).forEach(f => {
      html += `<div class="rowline">⚕ <b class="bad">${esc(f.name)}</b> (${f.pos}) ${f.injury}${f.newsDate?' <span class="dim">['+f.newsDate+']</span>':''}`
        + (f.news ? `<div class="dim">${esc(f.news)}</div>` : '') + '</div>';
    });
  }
  el.innerHTML = html;
}
