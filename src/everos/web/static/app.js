/* EverOS Console - bundled web UI for self-hosted EverOS */
const ICONS = {
  code: '<path d="M9 8l-4 4 4 4M15 8l4 4-4 4"/>',
  key: '<circle cx="8" cy="15" r="3.2"/><path d="M10.5 12.5L20 3M16 4l3 3M13 7l3 3"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 01-5 5 5 5 0 01-5-5V8zM12 16v5"/>',
  grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
  bank: '<path d="M4 9h16M6 9v9M10 9v9M14 9v9M18 9v9M3 20h18M12 3l9 5H3l9-5z"/>',
  log: '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v4l3 2"/>',
  doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>'
};

function paintIcons() {
  document.querySelectorAll('a[data-ico]').forEach(a => {
    if (a.querySelector('svg')) return;
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.innerHTML = ICONS[a.dataset.ico] || '';
    s.style.cssText = 'width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.6';
    a.prepend(s);
  });
}

const $ = s => document.querySelector(s);
const view = () => $('#view');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = n => (n ?? 0).toLocaleString('en-US');
const unwrap = r => (r && typeof r === 'object' && 'data' in r ? r.data : r);

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: 'include',
    cache: 'no-store',
    headers: { 'Accept': 'application/json', ...opts.headers },
    ...opts
  });
  if (r.status === 401 && !path.includes('/auth/session')) {
    showLogin();
    throw new Error('Unauthorized');
  }
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}

async function apiPost(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
}

async function apiDelete(path) {
  return api(path, { method: 'DELETE' });
}

function ago(ts) {
  if (!ts) return '-';
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return ts;
  const d = (Date.now() - t) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  if (d < 86400) return Math.floor(d / 3600) + 'h ago';
  if (d < 86400 * 30) return Math.floor(d / 86400) + 'd ago';
  return new Date(t).toLocaleDateString('en-US');
}

function shortTime(ts) {
  if (!ts) return '-';
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return ts.slice(0, 19).replace('T', ' ');
  return new Date(t).toLocaleString('en-US', { hour12: false });
}

const statusPill = s => {
  const c = s < 300 ? 'ok' : s < 400 ? 'warnp' : s < 500 ? 'warnp' : 'err';
  return `<span class="pill ${c}">${s}</span>`;
};

/* ---------- modal ---------- */
function showModal(title, body, buttons = []) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  $('#modalFooter').innerHTML = buttons.map(b =>
    `<button class="select ${b.primary ? 'primary' : ''}" data-action="${esc(b.action)}">${esc(b.label)}</button>`
  ).join('');
  $('#modalOverlay').style.display = 'flex';
  $('#modalFooter').onclick = e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'cancel') hideModal();
    else if (buttons.find(b => b.action === action)?.onClick) {
      buttons.find(b => b.action === action).onClick();
    }
  };
}

function hideModal() {
  $('#modalOverlay').style.display = 'none';
}

$('#modalClose').onclick = hideModal;
$('#modalOverlay').onclick = e => { if (e.target === $('#modalOverlay')) hideModal(); };

/* ---------- auth ---------- */
let isAuthenticated = false;

function showLogin() {
  $('#loginOverlay').style.display = 'flex';
  $('#loginKey').focus();
}

function hideLogin() {
  $('#loginOverlay').style.display = 'none';
  $('#loginKey').value = '';
  $('#loginError').style.display = 'none';
}

async function doLogin() {
  const key = $('#loginKey').value.trim();
  if (!key) return;
  try {
    await apiPost('/api/v2/auth/session', { key });
    isAuthenticated = true;
    hideLogin();
    $('#logoutBtn').style.display = 'block';
    route();
  } catch (e) {
    $('#loginError').textContent = 'Invalid API key';
    $('#loginError').style.display = 'block';
  }
}

async function doLogout() {
  try {
    await apiDelete('/api/v2/auth/session');
  } catch (e) { }
  isAuthenticated = false;
  $('#logoutBtn').style.display = 'none';
  showLogin();
}

$('#loginBtn').onclick = doLogin;
$('#loginKey').onkeydown = e => { if (e.key === 'Enter') doLogin(); };
$('#logoutBtn').onclick = doLogout;

/* ---------- chart ---------- */
function barChart(points, opts = {}) {
  const w = 900, h = opts.height || 180, pad = 28;
  const max = Math.max(1, ...points.map(p => p.v));
  const bw = Math.max(5, Math.min(26, (w - pad * 2) / Math.max(points.length, 1) * 0.55));
  const step = (w - pad * 2) / Math.max(points.length, 1);
  const bars = points.map((p, i) => {
    const bh = Math.round((p.v / max) * (h - pad - 24));
    const x = pad + step * i + (step - bw) / 2;
    const y = h - pad - bh;
    return `<g><rect x="${x.toFixed(1)}" y="${y}" width="${bw.toFixed(1)}" height="${Math.max(bh, p.v ? 2 : 0)}" rx="4" fill="${p.v ? '#f5c542' : '#eceae5'}"/>
      <title>${esc(p.label || '')}: ${p.v}</title></g>`;
  }).join('');
  const ticks = points.map((p, i) => {
    if (points.length > 10 && i % Math.ceil(points.length / 8) !== 0) return '';
    const x = pad + step * i + step / 2;
    return `<text x="${x.toFixed(1)}" y="${h - 8}" font-size="10" fill="#9a958c" text-anchor="middle">${esc(p.short || '')}</text>`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="#eceae5"/>
    <text x="${pad}" y="${pad - 10}" font-size="10" fill="#9a958c">${max}</text>
    ${bars}${ticks}</svg>`;
}

/* ---------- dashboard ---------- */
async function renderDashboard() {
  const d = unwrap(await api('/api/v2/admin/overview'));
  const logsData = unwrap(await api('/api/v2/admin/request-logs?limit=400'));
  const logs = logsData.items || [];
  const hourly = bucketByHour(logs);
  const c = d.counts || {};
  const sys = {
    data_mb: ((d.storage_bytes || 0) / 1048576).toFixed(1),
    store_files: (c.profile_items || 0) + (c.episodes || 0) + (c.cases || 0) + (c.knowledge_docs || 0)
  };

  view().innerHTML = `
  <div class="head">
    <div class="plan-mark">V</div>
    <div><h1>Self-hosted - ${fmt(c.episodes)} episodes</h1>
      <div class="sub">Last write: ${esc(ago(d.last_write))} - Storage: ${sys.data_mb || 0} MB - ${sys.store_files || 0} md files</div></div>
    <div class="right"><button class="select" onclick="location.reload()">Refresh</button></div>
  </div>

  <div class="h2">Total Account Usage</div>
  <div class="grid2">
    <div class="card">
      <div class="stat-top"><span class="ico"><svg viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg></span>
        MemCell Units</div>
      <p class="desc">MemCells are atomic memory units that group related messages into semantic memories. Self-hosted has no quota limit.</p>
      <div class="bar"><i style="width:${Math.min(100, (c.episodes || 0) / 80).toFixed(1)}%"></i></div>
      <div class="big">${fmt(c.episodes)} <span>Episodes (extracted)</span></div>
      <div class="legend"><span class="dot"></span>Local md-first storage, no monthly reset</div>
    </div>
    <div class="card">
      <div class="stat-top"><span class="ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg></span>
        Retrieval API Calls</div>
      <p class="desc">Total calls to Get memories and Search memories (from request logs, last 7 days).</p>
      <div class="bar"><i style="width:${Math.min(100, (logsData.total / 2000) * 100).toFixed(1)}%"></i></div>
      <div class="big">${fmt(d.search_calls || 0)} <span>search calls - ${fmt(logsData.total)} total requests in 7 days</span></div>
      <div class="legend"><span class="dot"></span>${fmt(d.calls_today || 0)} calls today</div>
    </div>
  </div>

  <div class="h2">API Usage</div>
  <div class="card">
    <div class="toolbar" style="margin-bottom:14px">
      <div style="font-weight:700;font-size:15px">Request Volume</div>
      <div class="grow"></div>
      <div class="seg" id="usageSeg"><button class="on" data-b="hour">Hourly</button><button data-b="day">Daily</button></div>
    </div>
    <div id="usageChart">${barChart(hourly, '')}</div>
  </div>

  <div class="h2">Recent Activity</div>
  <div class="card tight">
    <table><thead><tr><th>Method</th><th>Endpoint</th><th>Status</th><th>Latency</th><th>Time</th></tr></thead>
    <tbody>${logs.slice(0, 8).map(l => `<tr>
      <td><span class="m ${(l.method || '').toLowerCase()}"><i></i>${esc(l.method)}</span></td>
      <td class="mono">${esc(l.path)}</td><td>${statusPill(l.status)}</td>
      <td class="mono">${l.latency_ms || '-'}ms</td><td>${esc(shortTime(l.ts))}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">No requests yet</td></tr>'}
    </tbody></table>
  </div>

  <div class="h2">Instance</div>
  <div class="card">
    <div class="kv">
      <div class="k">Status</div><div><span class="pill ok">active</span> EverOS ${esc(sys.version || d.version || '-')}</div>
      <div class="k">Model</div><div class="mono">${esc(sys.model || d.model || '-')}</div>
      <div class="k">Profiles / Episodes</div><div>${fmt(c.profile_items)} profile facts - ${fmt(c.episodes)} episodes</div>
      <div class="k">Cases / Skills / Agents</div><div>${fmt(c.cases)} cases - ${fmt(c.skills)} skills - ${fmt(c.agents)} agents</div>
      <div class="k">Storage</div><div>${sys.data_mb || 0} MB</div>
    </div>
  </div>`;

  const seg = $('#usageSeg');
  seg.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    [...seg.children].forEach(x => x.classList.toggle('on', x === b));
    const pts = b.dataset.b === 'hour' ? bucketByHour(logs) : bucketByDay(logs);
    $('#usageChart').innerHTML = barChart(pts);
  };
}

function bucketByHour(items) {
  const out = []; const now = new Date();
  for (let i = 23; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 3600e3); d.setMinutes(0, 0, 0);
    const key = d.toISOString().slice(0, 13);
    out.push({ key, label: d.getHours() + ':00', short: String(d.getHours()).padStart(2, '0'), v: 0 });
  }
  const idx = Object.fromEntries(out.map(o => [o.key, o]));
  items.forEach(it => { if (!it.ts) return; const k = new Date(it.ts).toISOString().slice(0, 13); if (idx[k]) idx[k].v++; });
  return out;
}

function bucketByDay(items) {
  const out = []; const now = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 86400e3);
    const key = d.toISOString().slice(0, 10);
    out.push({ key, label: key, short: key.slice(5), v: 0 });
  }
  const idx = Object.fromEntries(out.map(o => [o.key, o]));
  items.forEach(it => { if (!it.ts) return; const k = new Date(it.ts).toISOString().slice(0, 10); if (idx[k]) idx[k].v++; });
  return out;
}

/* ---------- memory bank ---------- */
const bankState = { tab: 'overview', user: '', q: '', agent: null, limit: 30 };

async function renderBank() {
  const d = unwrap(await api('/api/v2/admin/overview'));
  const c = d.counts || {};
  const users = d.users || [];
  if (!bankState.user && users.length) bankState.user = users[0].id;

  const tabs = `
  <div style="display:flex;justify-content:center;margin:4px 0 18px">
    <div class="tabs" id="bankTabs">
      <button data-t="overview" class="${bankState.tab === 'overview' ? 'on' : ''}">Overview</button>
      <button data-t="user" class="${bankState.tab === 'user' ? 'on' : ''}">User Memories</button>
      <button data-t="agent" class="${bankState.tab === 'agent' ? 'on' : ''}">Agent Memories</button>
    </div>
  </div>`;

  if (bankState.tab === 'overview') {
    view().innerHTML = tabs + `
      <div class="searchbig"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/></svg>
        <input id="bankSearch" placeholder="Search memory content..."></div>
      <div class="tiles">
        <div class="tile t-blue"><div class="t"><span>Profiles</span></div><div class="n">${fmt(c.profile_items)}</div></div>
        <div class="tile t-purple"><div class="t"><span>Episodes</span></div><div class="n">${fmt(c.episodes)}</div></div>
        <div class="tile t-mint"><div class="t"><span>Cases</span></div><div class="n">${fmt(c.cases)}</div></div>
        <div class="tile t-green"><div class="t"><span>Skills</span></div><div class="n">${fmt(c.skills)}</div></div>
      </div>
      <div class="card">
        <div class="toolbar" style="margin-bottom:8px">
          <div style="font-weight:700;font-size:15px">Users</div>
        </div>
        <table><thead><tr><th>User ID</th><th>Facts</th><th>Episodes</th><th>Last Activity</th></tr></thead>
        <tbody>${users.map(u => `<tr style="cursor:pointer" onclick="bankState.user='${esc(u.id)}';bankState.tab='user';renderBank()">
          <td>${esc(u.id)}</td><td>${u.profiles || 0}</td><td>${u.episodes || 0}</td><td>${esc(ago(u.last))}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">No users</td></tr>'}
        </tbody></table>
      </div>`;
    const inp = $('#bankSearch');
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter' && inp.value.trim()) {
        bankState.q = inp.value.trim(); bankState.tab = 'user'; renderBank();
      }
    });
  }

  if (bankState.tab === 'user') {
    const [profData, epData] = await Promise.all([
      apiPost('/api/v2/memory/get', { user_id: bankState.user, memory_type: 'profile' }),
      apiPost('/api/v2/memory/get', { user_id: bankState.user, memory_type: 'episode', page_size: 100 })
    ]);
    const prof = profData.data?.profiles?.[0] || {};
    const profD = prof.profile_data || {};
    const explicitItems = (profD.explicit_info || []).map(i => ({ ...i, item_type: 'explicit_info' }));
    const implicitItems = (profD.implicit_traits || []).map(i => ({ ...i, item_type: 'implicit_traits', category: i.trait }));
    const items = [...explicitItems, ...implicitItems];
    const episodes = epData.data?.episodes || [];

    view().innerHTML = tabs + `
      <div class="mem">
        <select id="userSel">${users.map(u => `<option value="${esc(u.id)}" ${u.id === bankState.user ? 'selected' : ''}>${esc(u.id)} (${u.episodes || 0} episodes)</option>`).join('')}</select>
        <div class="grow"></div>
        <span class="tagline">${fmt(items.length)} profile facts</span>
      </div>
      <div class="bank">
        <div class="pcol">
          <div class="pcard" style="background:#fbfaf8">
            <h4 style="margin:0 0 6px">Profile Summary</h4>
            <div style="font-size:12.5px;color:#6b6660;line-height:1.6">${esc(prof.summary || 'No summary yet')}</div>
          </div>
          <div class="pcard">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
              <h4 style="margin:0">Profile Facts</h4>
              <button class="select" onclick="showAddProfile()">+ Add</button>
            </div>
            <div id="profileItems">
              ${items.map(i => `<div class="profile-item" data-id="${esc(i.item_id || '')}" data-type="${esc(i.item_type || 'explicit_info')}">
                <div class="pi-content">
                  <span class="tagline">${esc(i.category || 'General')}</span>
                  <div class="pi-desc">${esc(i.description)}</div>
                </div>
                <div class="pi-actions">
                  <button class="pi-btn" onclick="showEditProfile('${esc(i.item_id)}', '${esc(i.category)}', '${esc(i.description.replace(/'/g, "\\'"))}', '${esc(i.item_type || 'explicit_info')}')">Edit</button>
                  <button class="pi-btn del" onclick="confirmDeleteProfile('${esc(i.item_id)}', '${esc(i.item_type || 'explicit_info')}')">Delete</button>
                </div>
              </div>`).join('') || '<div class="empty">No profile facts</div>'}
            </div>
          </div>
        </div>
        <div>
          <div class="toolbar" style="margin-bottom:12px">
            <div style="font-weight:700;font-size:15px">Episodes</div>
            <div class="grow"></div>
            <input class="input" id="epSearch" placeholder="Search episodes..." value="${esc(bankState.q)}" style="width:220px">
          </div>
          <div id="epList">
            ${episodes.slice(0, bankState.limit).map(e => `<div class="ep" data-id="${esc(e.id)}">
              <div class="top">
                <span>${esc(shortTime(e.timestamp))}</span>
                <span class="tagline">${esc(e.id?.slice(0, 8) || '')}</span>
                <div class="ep-actions">
                  <button class="pi-btn" onclick="event.stopPropagation();showFeedback('${esc(e.id)}',1)" title="Thumbs up">+</button>
                  <button class="pi-btn" onclick="event.stopPropagation();showFeedback('${esc(e.id)}',-1)" title="Thumbs down">-</button>
                  <button class="pi-btn" onclick="event.stopPropagation();showEditEpisode('${esc(e.id)}')" title="Edit">Edit</button>
                </div>
                <span class="tog">+</span>
              </div>
              <div class="txt">${esc((e.summary || e.subject || '').slice(0, 300))}</div>
              <div class="more"><b>Subject</b>\n${esc(e.subject || '')}\n\n<b>Summary</b>\n${esc(e.summary || '')}\n\n<b>Content</b>\n${esc(e.content || '')}</div>
            </div>`).join('') || '<div class="empty">No episodes</div>'}
            ${episodes.length > bankState.limit ? `<button class="select" id="moreEp" style="width:100%">Load more (${bankState.limit}/${episodes.length})</button>` : ''}
          </div>
        </div>
      </div>`;

    $('#userSel').onchange = e => { bankState.user = e.target.value; bankState.limit = 30; renderBank(); };
    $('#epSearch').addEventListener('keydown', e => {
      if (e.key === 'Enter') { bankState.q = e.target.value.trim(); bankState.limit = 30; renderBank(); }
    });
    wireEpisodes();
    const more = $('#moreEp');
    if (more) more.onclick = () => { bankState.limit += 30; renderBank(); };
  }

  if (bankState.tab === 'agent') {
    const agentsData = unwrap(await api('/api/v2/admin/agents'));
    const agents = agentsData.agents || [];
    if (!bankState.agent && agents.length) bankState.agent = agents[0].id;

    view().innerHTML = tabs + `
      <div class="mem">
        <select id="agentSel">${agents.map(a => `<option value="${esc(a.id)}" ${a.id === bankState.agent ? 'selected' : ''}>${esc(a.id)}</option>`).join('')}</select>
        <div class="grow"></div>
      </div>
      <div class="card">
        <div class="empty">Agent memory browser coming soon</div>
      </div>`;

    $('#agentSel').onchange = e => { bankState.agent = e.target.value; renderBank(); };
  }

  $('#bankTabs').onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    bankState.tab = b.dataset.t; renderBank();
  };
}

function wireEpisodes() {
  document.querySelectorAll('.ep').forEach(el => {
    el.querySelector('.tog')?.addEventListener('click', () => {
      el.classList.toggle('open');
      el.querySelector('.tog').textContent = el.classList.contains('open') ? '-' : '+';
    });
  });
}

/* ---------- profile editing ---------- */
window.showAddProfile = function() {
  showModal('Add Profile Fact', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Category</label>
      <input type="text" id="profCategory" class="input" style="width:100%" placeholder="e.g. Preferences, Goals, Identity">
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Description</label>
      <textarea id="profDesc" class="input" style="width:100%;height:80px" placeholder="Describe the fact..."></textarea>
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Add', action: 'add', primary: true, onClick: async () => {
      const cat = $('#profCategory').value.trim();
      const desc = $('#profDesc').value.trim();
      if (!desc) return;
      try {
        await apiPost('/api/v2/memory/edit', {
          user_id: bankState.user,
          operations: [{
            action: 'add',
            type: 'explicit_info',
            data: { category: cat || 'General', description: desc }
          }]
        });
        hideModal();
        renderBank();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.showEditProfile = function(itemId, category, description, itemType) {
  showModal('Edit Profile Fact', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Category</label>
      <input type="text" id="profCategory" class="input" style="width:100%" value="${esc(category)}">
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Description</label>
      <textarea id="profDesc" class="input" style="width:100%;height:80px">${esc(description)}</textarea>
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Save', action: 'save', primary: true, onClick: async () => {
      const cat = $('#profCategory').value.trim();
      const desc = $('#profDesc').value.trim();
      if (!desc) return;
      try {
        await apiPost('/api/v2/memory/edit', {
          user_id: bankState.user,
          operations: [{
            action: 'update',
            type: itemType || 'explicit_info',
            item_id: itemId,
            data: { category: cat || 'General', description: desc }
          }]
        });
        hideModal();
        renderBank();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.confirmDeleteProfile = function(itemId, itemType) {
  showModal('Delete Profile Fact', '<p>Are you sure you want to delete this profile fact?</p>', [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Delete', action: 'delete', primary: true, onClick: async () => {
      try {
        await apiPost('/api/v2/memory/edit', {
          user_id: bankState.user,
          operations: [{
            action: 'delete',
            type: itemType || 'explicit_info',
            item_id: itemId
          }]
        });
        hideModal();
        renderBank();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

/* ---------- episode editing ---------- */
window.showEditEpisode = function(episodeId) {
  showModal('Edit Episode', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Summary</label>
      <textarea id="epSummary" class="input" style="width:100%;height:60px" placeholder="Episode summary"></textarea>
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Subject</label>
      <input type="text" id="epSubject" class="input" style="width:100%" placeholder="Episode subject">
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Save', action: 'save', primary: true, onClick: async () => {
      const summary = $('#epSummary').value.trim();
      const subject = $('#epSubject').value.trim();
      try {
        await apiPost('/api/v2/memory/update', {
          memory_type: 'episode',
          memory_id: episodeId,
          patch: {
            summary: summary || undefined,
            subject: subject || undefined
          }
        });
        hideModal();
        renderBank();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.showFeedback = async function(memoryId, rating) {
  try {
    await apiPost('/api/v2/memory/feedback', {
      memory_type: 'episode',
      memory_id: memoryId,
      rating: rating > 0 ? 'positive' : 'negative'
    });
    alert(rating > 0 ? 'Marked as helpful!' : 'Marked as not helpful');
  } catch (e) { alert('Failed: ' + e.message); }
};

/* ---------- playground ---------- */
const pgState = { q: '', user: '', mode: 'search', res: null };

async function renderPlayground() {
  const d = unwrap(await api('/api/v2/admin/overview'));
  const users = d.users || [];
  if (!pgState.user && users.length) pgState.user = users[0].id;

  view().innerHTML = `
    <div class="head"><div><h1 style="font-size:20px">Playground</h1>
      <div class="sub">Test memory search, context retrieval, and memory addition.</div></div></div>

    <div class="card" style="margin-bottom:16px">
      <div class="toolbar" style="margin-bottom:12px">
        <div class="seg" id="pgMode">
          <button class="${pgState.mode === 'search' ? 'on' : ''}" data-m="search">Search</button>
          <button class="${pgState.mode === 'context' ? 'on' : ''}" data-m="context">Context</button>
          <button class="${pgState.mode === 'add' ? 'on' : ''}" data-m="add">Add Memory</button>
        </div>
      </div>

      <div class="toolbar">
        <select class="select" id="pgUser">${users.map(u => `<option value="${esc(u.id)}" ${u.id === pgState.user ? 'selected' : ''}>${esc(u.id)}</option>`).join('')}</select>
        <input class="input" id="pgQ" placeholder="${pgState.mode === 'add' ? 'Enter memory content...' : 'Enter search query...'}" style="flex:1;min-width:260px" value="${esc(pgState.q)}">
        <select class="select" id="pgK"><option value="6">top 6</option><option value="10">top 10</option><option value="3">top 3</option></select>
        <button class="select" id="pgGo" style="background:#f5c542;border-color:#e7b93a;font-weight:600">${pgState.mode === 'add' ? 'Add' : 'Search'}</button>
      </div>
    </div>

    <div id="pgOut">${pgState.res ? pgRender(pgState.res) : '<div class="card"><div class="empty">Enter a query to search</div></div>'}</div>`;

  $('#pgMode').onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    pgState.mode = b.dataset.m;
    pgState.res = null;
    renderPlayground();
  };

  const go = async () => {
    const q = $('#pgQ').value.trim();
    if (!q) return;
    pgState.q = q; pgState.user = $('#pgUser').value;
    $('#pgOut').innerHTML = '<div class="spin">Processing...</div>';
    try {
      let res;
      if (pgState.mode === 'add') {
        res = await apiPost('/api/v2/memory/add', {
          user_id: pgState.user,
          messages: [{ role: 'user', content: q }]
        });
        res._mode = 'add';
      } else if (pgState.mode === 'context') {
        res = await apiPost('/api/v2/memory/context', {
          user_id: pgState.user,
          query: q,
          top_k: parseInt($('#pgK').value)
        });
        res._mode = 'context';
      } else {
        res = await apiPost('/api/v2/memory/search', {
          user_id: pgState.user,
          query: q,
          top_k: parseInt($('#pgK').value)
        });
        res._mode = 'search';
      }
      pgState.res = res;
      $('#pgOut').innerHTML = pgRender(res);
    } catch (e) {
      $('#pgOut').innerHTML = '<div class="card"><div class="empty">' + esc(e.message) + '</div></div>';
    }
  };
  $('#pgGo').onclick = go;
  $('#pgQ').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
}

function pgRender(res) {
  if (res.error) return `<div class="card"><div class="empty">Error: ${esc(res.error)}</div></div>`;

  if (res._mode === 'add') {
    return `<div class="card">
      <div class="stat-top">Memory Added</div>
      <p class="desc">The memory has been queued for extraction. Request ID: <span class="mono">${esc(res.request_id || '-')}</span></p>
    </div>`;
  }

  if (res._mode === 'context') {
    const data = res.data || res;
    return `<div class="card">
      <div class="stat-top">Context (${data.tokens_estimate || 0} tokens)</div>
      <pre class="mono" style="background:#f7f6f3;padding:12px;border-radius:10px;overflow:auto;font-size:11.5px;line-height:1.6;white-space:pre-wrap">${esc(data.context || '')}</pre>
    </div>`;
  }

  const data = res.data || {};
  const episodes = data.episodes || [];
  const profiles = data.profiles || [];

  const block = (title, items) => items.length ? `
    <div style="margin-bottom:14px">
      <div style="font-weight:700;font-size:15px;margin:6px 0 10px">${title} <span class="tagline">${items.length}</span></div>
      ${items.map(i => `<div class="ep">
        <div class="top">
          <span>${esc(shortTime(i.timestamp))}</span>
          <span class="tagline">score ${(i.score || 0).toFixed(3)}</span>
          ${i.category ? `<span class="tagline">${esc(i.category)}</span>` : ''}
          <span class="tog">+</span>
        </div>
        <div class="txt">${esc((i.summary || i.subject || i.description || i.text || '').slice(0, 400))}</div>
        <div class="more">${esc(i.episode || i.text || i.content || '')}</div>
      </div>`).join('')}
    </div>` : '';

  const html = block('Episodes', episodes) + block('Profiles', profiles);
  const out = html || '<div class="empty">No results</div>';

  return `<div class="card tight" style="padding:16px 18px">
    <div class="foot-note" style="margin:0 0 10px">request_id <span class="mono">${esc(res.request_id || '-')}</span></div>
    ${out}</div>`;
}

/* ---------- request logs ---------- */
const logState = { method: '', status: '', q: '' };

async function renderLogs() {
  const qs = new URLSearchParams({ limit: '200' });
  if (logState.method) qs.set('method', logState.method);
  if (logState.status) qs.set('status', logState.status);
  if (logState.q) qs.set('path', logState.q);

  const d = unwrap(await api('/api/v2/admin/request-logs?' + qs.toString()));
  const logs = d.items || [];

  view().innerHTML = `
    <div class="head"><div><h1 style="font-size:20px">Request Logs</h1>
      <div class="sub">API request history with status and latency information.</div></div></div>
    <div class="toolbar" style="margin-bottom:16px">
      <select class="select" id="fMethod"><option value="">All methods</option>
        ${['GET', 'POST', 'PUT', 'DELETE'].map(m => `<option value="${m}" ${logState.method === m ? 'selected' : ''}>${m}</option>`).join('')}</select>
      <select class="select" id="fStatus"><option value="">All status</option>
        ${['2', '3', '4', '5'].map(s => `<option value="${s}" ${logState.status === s ? 'selected' : ''}>${s}xx</option>`).join('')}</select>
      <input class="input" id="fQ" placeholder="Filter by endpoint..." value="${esc(logState.q)}" style="width:240px">
      <div class="grow"></div>
      <span class="tagline">${fmt(d.total)} total</span>
    </div>
    <div class="card tight">
      <table><thead><tr><th>Timestamp</th><th>Method</th><th>Endpoint</th><th>Status</th><th>Latency</th><th>Client</th></tr></thead>
      <tbody>${logs.map(l => `<tr>
        <td>${esc(shortTime(l.ts))}</td>
        <td><span class="m ${(l.method || '').toLowerCase()}"><i></i>${esc(l.method)}</span></td>
        <td class="mono">${esc(l.path)}</td>
        <td>${statusPill(l.status)}</td>
        <td class="mono">${l.latency_ms || '-'}ms</td>
        <td class="mono">${esc(l.client)}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">No matching requests</td></tr>'}
      </tbody></table>
    </div>`;

  const upd = () => {
    logState.method = $('#fMethod').value;
    logState.status = $('#fStatus').value;
    logState.q = $('#fQ').value.trim();
    renderLogs();
  };
  $('#fMethod').onchange = upd;
  $('#fStatus').onchange = upd;
  $('#fQ').addEventListener('keydown', e => { if (e.key === 'Enter') upd(); });
}

/* ---------- API keys ---------- */
async function renderKeys() {
  const d = await api('/api/v2/auth/keys');
  const keys = d.keys || [];

  view().innerHTML = `
    <div class="head">
      <div><h1 style="font-size:20px">API Keys</h1>
        <div class="sub">Manage API keys for authentication.</div></div>
      <div class="right"><button class="select" onclick="showCreateKey()" style="background:#f5c542;border-color:#e7b93a;font-weight:600">+ Create Key</button></div>
    </div>
    <div class="card tight">
      <table><thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Created</th><th>Last Used</th><th>Actions</th></tr></thead>
      <tbody>${keys.map(k => `<tr>
        <td>${esc(k.name)}</td>
        <td class="mono">${esc(k.prefix)}...</td>
        <td>${(k.scopes || []).map(s => `<span class="tagline">${esc(s)}</span>`).join(' ')}</td>
        <td>${esc(ago(k.created_at))}</td>
        <td>${esc(k.last_used_at ? ago(k.last_used_at) : 'Never')}</td>
        <td>
          ${!k.revoked_at ? `<button class="pi-btn" onclick="rotateKey('${esc(k.key_id)}')">Rotate</button>
          <button class="pi-btn del" onclick="revokeKey('${esc(k.key_id)}')">Revoke</button>` : '<span class="pill err">Revoked</span>'}
        </td></tr>`).join('') || '<tr><td colspan="6" class="empty">No API keys</td></tr>'}
      </tbody></table>
    </div>`;
}

window.showCreateKey = function() {
  showModal('Create API Key', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Name</label>
      <input type="text" id="keyName" class="input" style="width:100%" placeholder="e.g. my-app">
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Scopes</label>
      <div style="margin-top:8px">
        <label><input type="checkbox" id="scopeRead" checked> memory:read</label><br>
        <label><input type="checkbox" id="scopeWrite" checked> memory:write</label><br>
        <label><input type="checkbox" id="scopeAdmin"> admin</label>
      </div>
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Create', action: 'create', primary: true, onClick: async () => {
      const name = $('#keyName').value.trim();
      if (!name) return;
      const scopes = [];
      if ($('#scopeRead').checked) scopes.push('memory:read');
      if ($('#scopeWrite').checked) scopes.push('memory:write');
      if ($('#scopeAdmin').checked) scopes.push('admin');
      try {
        const res = await apiPost('/api/v2/auth/keys', { name, scopes });
        hideModal();
        showModal('API Key Created', `
          <p>Your new API key has been created. <strong>Copy it now - you won't see it again!</strong></p>
          <div style="background:#f7f6f3;padding:12px;border-radius:10px;margin-top:12px">
            <code style="word-break:break-all">${esc(res.key)}</code>
          </div>
        `, [{ label: 'Done', action: 'cancel' }]);
        renderKeys();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.rotateKey = async function(keyId) {
  showModal('Rotate API Key', '<p>This will generate a new secret for this key. The old secret will stop working immediately.</p>', [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Rotate', action: 'rotate', primary: true, onClick: async () => {
      try {
        const res = await apiPost('/api/v2/auth/keys/' + keyId + '/rotate', {});
        hideModal();
        showModal('Key Rotated', `
          <p>Your new API key secret. <strong>Copy it now - you won't see it again!</strong></p>
          <div style="background:#f7f6f3;padding:12px;border-radius:10px;margin-top:12px">
            <code style="word-break:break-all">${esc(res.key)}</code>
          </div>
        `, [{ label: 'Done', action: 'cancel' }]);
        renderKeys();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.revokeKey = function(keyId) {
  showModal('Revoke API Key', '<p>Are you sure you want to revoke this key? It will stop working immediately.</p>', [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Revoke', action: 'revoke', primary: true, onClick: async () => {
      try {
        await apiDelete('/api/v2/auth/keys/' + keyId);
        hideModal();
        renderKeys();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

/* ---------- knowledge ---------- */
async function renderKnowledge() {
  let kbs = [];
  try {
    const res = await api('/api/v2/knowledge_bases');
    kbs = res.knowledge_bases || [];
  } catch (e) { }

  view().innerHTML = `
    <div class="head">
      <div><h1 style="font-size:20px">Knowledge Bases</h1>
        <div class="sub">Manage knowledge bases and documents.</div></div>
      <div class="right"><button class="select" onclick="showCreateKB()" style="background:#f5c542;border-color:#e7b93a;font-weight:600">+ Create KB</button></div>
    </div>
    <div class="card tight">
      <table><thead><tr><th>Name</th><th>Description</th><th>Documents</th><th>Actions</th></tr></thead>
      <tbody>${kbs.map(kb => `<tr>
        <td>${esc(kb.name)}</td>
        <td>${esc(kb.description || '-')}</td>
        <td>${kb.doc_count || 0}</td>
        <td>
          <button class="pi-btn" onclick="showUploadDoc('${esc(kb.kb_id)}')">Upload</button>
          <button class="pi-btn del" onclick="deleteKB('${esc(kb.kb_id)}')">Delete</button>
        </td></tr>`).join('') || '<tr><td colspan="4" class="empty">No knowledge bases</td></tr>'}
      </tbody></table>
    </div>`;
}

window.showCreateKB = function() {
  showModal('Create Knowledge Base', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Name</label>
      <input type="text" id="kbName" class="input" style="width:100%" placeholder="e.g. docs">
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Description</label>
      <input type="text" id="kbDesc" class="input" style="width:100%" placeholder="Optional description">
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Create', action: 'create', primary: true, onClick: async () => {
      const name = $('#kbName').value.trim();
      if (!name) return;
      try {
        await apiPost('/api/v2/knowledge_bases', { name, description: $('#kbDesc').value.trim() });
        hideModal();
        renderKnowledge();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.showUploadDoc = function(kbId) {
  showModal('Upload Document', `
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:#6b6660">Title</label>
      <input type="text" id="docTitle" class="input" style="width:100%" placeholder="Document title">
    </div>
    <div>
      <label style="font-size:12px;color:#6b6660">Content (Markdown)</label>
      <textarea id="docContent" class="input" style="width:100%;height:150px" placeholder="# Document content..."></textarea>
    </div>
  `, [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Upload', action: 'upload', primary: true, onClick: async () => {
      const title = $('#docTitle').value.trim();
      const content = $('#docContent').value.trim();
      if (!title || !content) return;
      try {
        await apiPost('/api/v2/knowledge_bases/' + kbId + '/documents', { title, content });
        hideModal();
        renderKnowledge();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

window.deleteKB = function(kbId) {
  showModal('Delete Knowledge Base', '<p>Are you sure you want to delete this knowledge base and all its documents?</p>', [
    { label: 'Cancel', action: 'cancel' },
    { label: 'Delete', action: 'delete', primary: true, onClick: async () => {
      try {
        await apiDelete('/api/v2/knowledge_bases/' + kbId);
        hideModal();
        renderKnowledge();
      } catch (e) { alert('Failed: ' + e.message); }
    }}
  ]);
};

/* ---------- settings ---------- */
async function renderSettings() {
  const d = unwrap(await api('/api/v2/admin/overview'));
  const c = d.counts || {};
  const sys = {
    data_mb: ((d.storage_bytes || 0) / 1048576).toFixed(1),
    store_files: (c.profile_items || 0) + (c.episodes || 0) + (c.cases || 0) + (c.knowledge_docs || 0)
  };

  view().innerHTML = `
    <div class="head"><div><h1 style="font-size:20px">Settings</h1>
      <div class="sub">View and configure your EverOS instance.</div></div></div>

    <div class="h2">Instance Information</div>
    <div class="card">
      <div class="kv">
        <div class="k">Version</div><div>EverOS ${esc(sys.version || d.version || '-')}</div>
        <div class="k">Model</div><div class="mono">${esc(sys.model || d.model || '-')}</div>
        <div class="k">Auth Mode</div><div>${d.auth_mode || 'enabled'}</div>
        <div class="k">Storage</div><div>${sys.data_mb || 0} MB</div>
        <div class="k">Space</div><div class="mono">${esc(d.space || 'default_space')}</div>
      </div>
    </div>

    <div class="h2">Configuration</div>
    <div class="card">
      <p class="desc">Configuration is managed via <code>everos.toml</code> in your memory root directory. Use the CLI to modify settings:</p>
      <pre class="mono" style="background:#f7f6f3;padding:12px;border-radius:10px;overflow:auto;font-size:11.5px;line-height:1.6">everos config show        # View current config
everos config edit        # Edit configuration</pre>
    </div>

    <div class="h2">API Documentation</div>
    <div class="card">
      <p class="desc">Interactive API documentation is available at:</p>
      <div style="margin-top:12px">
        <a href="/docs" target="_blank" class="select" style="text-decoration:none">OpenAPI (Swagger)</a>
        <a href="/redoc" target="_blank" class="select" style="text-decoration:none;margin-left:8px">ReDoc</a>
      </div>
    </div>`;
}

/* ---------- router ---------- */
const routes = {
  '/dashboard': renderDashboard,
  '/memory-bank': renderBank,
  '/logs': renderLogs,
  '/keys': renderKeys,
  '/knowledge': renderKnowledge,
  '/settings': renderSettings,
  '/playground': renderPlayground,
};

async function route() {
  const path = (location.hash.replace('#', '') || '/dashboard');
  document.querySelectorAll('.nav a').forEach(a => {
    a.classList.toggle('on', a.getAttribute('href') === '#' + path);
  });
  const fn = routes[path] || renderDashboard;
  try {
    view().innerHTML = '<div class="spin">Loading...</div>';
    await fn();
    wireEpisodes();
  } catch (err) {
    if (err.message === 'Unauthorized') return;
    view().innerHTML = `<div class="card"><div class="stat-top">Error</div><div class="desc">${esc(err.message || err)}</div>
      <button class="select" onclick="location.reload()">Retry</button></div>`;
  }
}

async function init() {
  paintIcons();
  try {
    const d = unwrap(await api('/api/v2/admin/overview'));
    isAuthenticated = true;
    $('#logoutBtn').style.display = 'block';
    $('#userVersion').textContent = 'v' + (d.version || d.system?.version || '1.7.0');
    if (d.space) $('#spaceName').textContent = d.space;
  } catch (e) {
    if (e.message === 'Unauthorized') {
      showLogin();
      return;
    }
  }
  window.addEventListener('hashchange', route);
  route();
}

init();
