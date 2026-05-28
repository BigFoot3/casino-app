'use strict';

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const $ = id => document.getElementById(id);

// ── Password modal ────────────────────────────────────────────────────────────
let pwTimer = null;
const pwModal    = new bootstrap.Modal($('pwModal'), {backdrop: true, keyboard: false});
const pwDisplay  = $('pw-display');
const pwCountdown= $('pw-countdown');
const pwCopyBtn  = $('pw-copy-btn');
const pwCopyOk   = $('pw-copy-ok');
const pwTitle    = $('pw-modal-title');

function showPassword(title, password) {
  clearInterval(pwTimer);
  pwTitle.textContent   = title;
  pwDisplay.textContent = password;
  pwCopyOk.style.display = 'none';
  pwModal.show();

  let secs = 30;
  pwCountdown.textContent = secs;
  pwTimer = setInterval(() => {
    secs--;
    pwCountdown.textContent = secs;
    if (secs <= 0) {
      clearInterval(pwTimer);
      pwDisplay.textContent = '';
      pwModal.hide();
    }
  }, 1000);
}

pwCopyBtn.addEventListener('click', () => {
  navigator.clipboard.writeText(pwDisplay.textContent).then(() => {
    pwCopyOk.style.display = '';
    setTimeout(() => { pwCopyOk.style.display = 'none'; }, 2000);
  });
});

// Clear password from DOM when modal hides
$('pwModal').addEventListener('hidden.bs.modal', () => {
  clearInterval(pwTimer);
  pwDisplay.textContent = '';
});

// ── API helper ────────────────────────────────────────────────────────────────
async function post(url, body = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
    body: JSON.stringify(body)
  });
  return [r.status, await r.json()];
}

// ── Session controls ──────────────────────────────────────────────────────────
let currentStatus = null;

$('btn-action').addEventListener('click', async () => {
  if (currentStatus === 'waiting') {
    const [s] = await post('/api/admin/session/open');
    if (s === 200) location.reload();
    else alert('Erreur : impossible d\'ouvrir la session.');
  } else if (currentStatus === 'open') {
    const [s] = await post('/api/admin/session/spin');
    if (s === 200) location.reload();
    else alert('Erreur : impossible de lancer la roue.');
  }
});

$('btn-close').addEventListener('click', () => {
  showConfirm(
    'Fermer la session',
    'Forcer la fermeture ? Les mises en cours seront perdues.',
    async () => {
      const [s, d] = await post('/api/admin/session/close');
      if (s === 200) location.reload();
      else alert(d.error || 'Erreur : impossible de fermer la session.');
    }
  );
});

$('btn-stats-reset').addEventListener('click', () => {
  showConfirm(
    'Vider les tops',
    'Réinitialiser le classement ? Les tops gagnants / perdants seront remis à zéro.',
    async () => {
      const [s, d] = await post('/api/admin/stats/reset');
      if (s === 200) location.reload();
      else alert(d.error || 'Erreur : impossible de réinitialiser les stats.');
    }
  );
});

// ── Mode toggle ───────────────────────────────────────────────────────────────
async function applyMode(mode) {
  const interval = parseInt($('interval-input').value) || 120;
  const [s, d]   = await post('/api/admin/mode', {mode, interval});
  const statusEl = $('mode-status');
  if (s === 200) {
    const isAuto = (mode === 'auto');
    $('btn-mode-manual').classList.toggle('active', !isAuto);
    $('btn-mode-auto').classList.toggle('active', isAuto);
    $('interval-wrap').style.display = 'flex';
    statusEl.textContent = isAuto ? '⚡ Activé' : '✅ Manuel';
    statusEl.className   = 'small text-success';
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
  } else {
    statusEl.textContent = d.error || 'Erreur';
    statusEl.className   = 'small text-danger';
  }
}

$('btn-mode-manual').addEventListener('click', () => applyMode('manual'));
$('btn-mode-auto').addEventListener('click',   () => applyMode('auto'));

$('interval-input').addEventListener('change', () => {
  const mode = $('btn-mode-auto').classList.contains('active') ? 'auto' : 'manual';
  applyMode(mode);
});

$('btn-interval-apply').addEventListener('click', () => {
  const mode = $('btn-mode-auto').classList.contains('active') ? 'auto' : 'manual';
  applyMode(mode);
});

// ── Session status polling — drives button states from server state ───────────
const SESSION_STATUS_BADGE_CLASSES = {
  waiting:  'badge fs-6 bg-secondary',
  open:     'badge fs-6 bg-success',
  spinning: 'badge fs-6 bg-warning text-dark',
  closed:   'badge fs-6 bg-danger',
};

function updateControlsState(status, mode) {
  currentStatus = status;

  const btnAction = $('btn-action');
  const btnClose  = $('btn-close');

  switch (status) {
    case 'waiting':
      btnAction.disabled    = false;
      btnAction.className   = 'btn btn-success';
      btnAction.textContent = '▶ Ouvrir session';
      btnClose.style.display = 'none';
      break;
    case 'open':
      btnAction.disabled    = false;
      btnAction.className   = 'btn btn-warning text-dark';
      btnAction.textContent = '🎯 Lancer la roue';
      btnClose.style.display = '';
      break;
    case 'spinning':
      btnAction.disabled    = true;
      btnAction.className   = 'btn btn-secondary';
      btnAction.textContent = '⏳ En cours…';
      btnClose.style.display = 'none';
      break;
    default:
      btnAction.disabled    = false;
      btnAction.className   = 'btn btn-success';
      btnAction.textContent = '▶ Ouvrir session';
      btnClose.style.display = 'none';
  }

  // Status badge
  const badge = $('session-status-badge');
  if (badge) {
    badge.className   = SESSION_STATUS_BADGE_CLASSES[status] || 'badge fs-6 bg-secondary';
    badge.textContent = status.toUpperCase();
  }

  // Mode toggle buttons
  const isAuto       = (mode === 'auto');
  const btnManual    = $('btn-mode-manual');
  const btnAutoEl    = $('btn-mode-auto');
  const intervalWrap = $('interval-wrap');
  if (btnManual)    btnManual.classList.toggle('active', !isAuto);
  if (btnAutoEl)    btnAutoEl.classList.toggle('active', isAuto);
  if (intervalWrap) intervalWrap.style.display = 'flex';
}

async function pollAdmin() {
  try {
    const r = await fetch('/api/session/status');
    if (r.ok) {
      const d = await r.json();
      updateControlsState(d.status, d.mode);
    }
  } catch(e) { /* network hiccup — keep current button states */ }
  setTimeout(pollAdmin, 3000);
}

pollAdmin();

// ── Users list collapse — chevron toggle ─────────────────────────────────────
const usersListEl = document.getElementById('users-list');
const usersChevron = $('users-list-chevron');
if (usersListEl && usersChevron) {
  usersListEl.addEventListener('show.bs.collapse', () => { usersChevron.textContent = '▲'; });
  usersListEl.addEventListener('hide.bs.collapse', () => { usersChevron.textContent = '▼'; });
}

// ── User search filter ────────────────────────────────────────────────────────
const userSearch = $('user-search');
if (userSearch) {
  userSearch.addEventListener('input', () => {
    const q = userSearch.value.toLowerCase().trim();
    const rows = document.querySelectorAll('#users-table tbody tr');
    let visible = 0;
    rows.forEach(row => {
      const name = row.querySelector('td')?.textContent.toLowerCase() || '';
      const show = !q || name.includes(q);
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    const noResult = $('users-no-result');
    if (noResult) noResult.style.display = (visible === 0 && rows.length > 0) ? '' : 'none';
  });
}

// ── Decrement tokens (−1, no modal needed) ───────────────────────────────────
document.querySelectorAll('.decrement-tokens-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid  = parseInt(btn.dataset.id);
    const [s, d] = await post(`/api/admin/users/${uid}/decrement-tokens`);
    if (s === 200) {
      document.querySelector(`.user-tokens[data-id="${uid}"]`).textContent = d.new_balance;
    } else {
      alert(d.error || 'Erreur');
    }
  });
});

// ── Quick-add tokens (+150 / +350) — double-tap confirmation on touch ────────
const quickBtnPendingMap = new Map(); // key: "uid-amount" → timer

document.querySelectorAll('.add-tokens-quick-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid    = parseInt(btn.dataset.id);
    const amount = parseInt(btn.dataset.amount);
    const isTouchDevice = window.matchMedia('(hover: none)').matches;

    if (isTouchDevice) {
      const key = `${uid}-${amount}`;
      if (quickBtnPendingMap.has(key)) {
        // Second tap within 3s → execute
        clearTimeout(quickBtnPendingMap.get(key));
        quickBtnPendingMap.delete(key);
        btn.textContent = `+${amount}`;
      } else {
        // First tap → request confirmation
        btn.textContent = '✓ Confirmer';
        const timer = setTimeout(() => {
          quickBtnPendingMap.delete(key);
          btn.textContent = `+${amount}`;
        }, 3000);
        quickBtnPendingMap.set(key, timer);
        return;
      }
    }

    // Send request (desktop: immediate; touch: after second tap)
    const [s, d] = await post(`/api/admin/users/${uid}/add-tokens`, {amount});
    if (s === 200) {
      document.querySelector(`.user-tokens[data-id="${uid}"]`).textContent = d.new_balance;
    } else {
      console.error('add-tokens-quick-btn error:', d.error);
    }
  });
});

// ── Tokens ────────────────────────────────────────────────────────────────────
document.querySelectorAll('.add-tokens-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid    = parseInt(btn.dataset.id);
    const input  = document.querySelector(`.add-tokens-input[data-id="${uid}"]`);
    const amount = parseInt(input.value);
    if (!amount || amount <= 0) return;
    const [s, d] = await post(`/api/admin/users/${uid}/add-tokens`, {amount});
    if (s === 200) {
      document.querySelector(`.user-tokens[data-id="${uid}"]`).textContent = d.new_balance;
      input.value = '';
    } else {
      alert(d.error || 'Erreur');
    }
  });
});

// ── Create user ───────────────────────────────────────────────────────────────
$('btn-create-user').addEventListener('click', async () => {
  const username = $('new-username').value.trim();
  const role     = $('new-role').value;
  if (!username) return;
  const [s, d] = await post('/api/admin/users/create', {username, role});
  if (s === 200) {
    showPassword(`Utilisateur créé : ${d.username}`, d.password);
    $('new-username').value = '';
    // Append row to table
    const tbody = document.querySelector('#users-table tbody');
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td>${d.username}</td>
        <td><span class="badge ${role==='admin'?'bg-warning text-dark':'bg-secondary'}">${role}</span></td>
        <td>0</td>
        <td><em class="text-muted small">rechargez la page</em></td>
        <td><em class="text-muted small">rechargez la page</em></td>
      </tr>`);
  } else {
    alert(d.error || 'Erreur');
  }
});

// ── Confirm modal helper ──────────────────────────────────────────────────────
const confirmModal = new bootstrap.Modal($('confirmModal'), {backdrop: 'static', keyboard: false});
let _confirmCallback = null;
$('confirm-ok').addEventListener('click', () => {
  confirmModal.hide();
  if (_confirmCallback) { _confirmCallback(); _confirmCallback = null; }
});

function showConfirm(title, body, onConfirm) {
  $('confirm-title').textContent = title;
  $('confirm-body').textContent  = body;
  _confirmCallback = onConfirm;
  confirmModal.show();
}

// ── Zero tokens ───────────────────────────────────────────────────────────────
document.querySelectorAll('.zero-tokens-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const uid  = parseInt(btn.dataset.id);
    const name = btn.dataset.name;
    showConfirm(
      'Réinitialiser les tokens',
      `Mettre le solde de ${name} à 0 ? Cette action est irréversible.`,
      async () => {
        const [s, d] = await post(`/api/admin/users/${uid}/zero-tokens`);
        if (s === 200) {
          document.querySelector(`.user-tokens[data-id="${uid}"]`).textContent = 0;
        } else {
          alert(d.error || 'Erreur');
        }
      }
    );
  });
});

// ── Delete user ───────────────────────────────────────────────────────────────
document.querySelectorAll('.delete-user-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const uid  = parseInt(btn.dataset.id);
    const name = btn.dataset.name;
    showConfirm(
      'Supprimer l\'utilisateur',
      `Supprimer définitivement ${name} et toutes ses données ?`,
      async () => {
        const [s, d] = await post(`/api/admin/users/${uid}/delete`);
        if (s === 200) {
          const row = document.getElementById(`user-row-${uid}`);
          if (row) row.remove();
        } else {
          alert(d.error || 'Erreur');
        }
      }
    );
  });
});

// ── Set role ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.set-role-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid     = parseInt(btn.dataset.id);
    const newRole = btn.dataset.role === 'player' ? 'admin' : 'player';
    const [s, d]  = await post(`/api/admin/users/${uid}/set-role`, {role: newRole});
    if (s === 200) {
      btn.dataset.role  = newRole;
      btn.textContent   = newRole === 'admin' ? '→ Joueur' : '→ Admin';
      const badge = btn.closest('tr').querySelector('.badge');
      badge.className   = `badge ${newRole === 'admin' ? 'bg-warning text-dark' : 'bg-secondary'}`;
      badge.textContent = newRole;
    } else {
      alert(d.error || 'Erreur');
    }
  });
});

// ── Reset password ────────────────────────────────────────────────────────────
document.querySelectorAll('.reset-pw-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid  = parseInt(btn.dataset.id);
    const name = btn.dataset.name;
    if (!confirm(`Réinitialiser le mot de passe de ${name} ?`)) return;
    const [s, d] = await post(`/api/admin/users/${uid}/reset-password`);
    if (s === 200) {
      showPassword(`Nouveau MDP pour ${name}`, d.password);
    } else {
      alert(d.error || 'Erreur');
    }
  });
});

// ── Vote catalogue ────────────────────────────────────────────────────────────
let catalogueData = [];   // [{id, name, display_order, films:[{id,title}]}]

async function loadCatalogue() {
  try {
    const r = await fetch('/api/admin/vote/catalogue');
    if (!r.ok) return;
    const d = await r.json();
    catalogueData = d.categories || [];
    renderCatalogue();
  } catch(e) {}
}

function renderCatalogue() {
  const container = $('catalogue-container');
  if (!container) return;
  container.innerHTML = '';
  if (!catalogueData.length) {
    container.innerHTML = '<p class="text-muted small">Aucune catégorie.</p>';
    return;
  }
  catalogueData.forEach(cat => {
    const catDiv = document.createElement('div');
    catDiv.className = 'mb-3 p-3 border rounded';
    catDiv.style.borderColor = 'var(--mg-claret)';

    const header = document.createElement('div');
    header.className = 'd-flex align-items-center gap-2 mb-2';
    header.innerHTML = `
      <strong class="flex-grow-1">${cat.name}</strong>
      <button class="btn btn-sm btn-outline-danger del-cat-btn" data-id="${cat.id}">🗑</button>
    `;
    catDiv.appendChild(header);

    // Films list
    const filmList = document.createElement('ul');
    filmList.className = 'list-group mb-2';
    (cat.films || []).forEach(f => {
      const li = document.createElement('li');
      li.className = 'list-group-item d-flex justify-content-between align-items-center';
      li.style.background = 'var(--mg-velvet)';
      li.style.color = 'var(--mg-ivory)';
      li.style.borderColor = 'var(--mg-oxblood)';
      li.innerHTML = `
        <span>${f.title}</span>
        <button class="btn btn-sm btn-outline-danger del-film-btn" data-id="${f.id}">🗑</button>
      `;
      filmList.appendChild(li);
    });
    catDiv.appendChild(filmList);

    // Add film form
    const addFilmRow = document.createElement('div');
    addFilmRow.className = 'd-flex gap-2';
    addFilmRow.innerHTML = `
      <input type="text" class="form-control form-control-sm film-title-input" placeholder="Titre du film">
      <button class="btn btn-sm btn-success add-film-btn text-nowrap" data-cat-id="${cat.id}">+ Film</button>
    `;
    catDiv.appendChild(addFilmRow);
    container.appendChild(catDiv);
  });

  // Bind delete category
  container.querySelectorAll('.del-cat-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const cid = parseInt(btn.dataset.id);
      if (!confirm('Supprimer cette catégorie et tous ses films ?')) return;
      const [s, d] = await post(`/api/admin/vote/categories/${cid}/delete`);
      if (s === 200) { await loadCatalogue(); }
      else alert(d.error || 'Erreur');
    });
  });

  // Bind add film
  container.querySelectorAll('.add-film-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const catId = parseInt(btn.dataset.catId);
      const input = btn.previousElementSibling;
      const title = input.value.trim();
      if (!title) return;
      const [s, d] = await post('/api/admin/vote/films', {title, category_id: catId});
      if (s === 200) { input.value = ''; await loadCatalogue(); }
      else alert(d.error || 'Erreur');
    });
  });

  // Bind delete film
  container.querySelectorAll('.del-film-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const fid = parseInt(btn.dataset.id);
      if (!confirm('Supprimer ce film ?')) return;
      const [s, d] = await post(`/api/admin/vote/films/${fid}/delete`);
      if (s === 200) { await loadCatalogue(); }
      else alert(d.error || 'Erreur');
    });
  });
}

const addCatBtn = $('btn-add-cat');
if (addCatBtn) {
  addCatBtn.addEventListener('click', async () => {
    const input = $('new-cat-name');
    const name  = input.value.trim();
    if (!name) return;
    const [s, d] = await post('/api/admin/vote/categories', {name});
    const fb = $('catalogue-feedback');
    if (s === 200) {
      input.value = '';
      fb.style.display = 'none';
      await loadCatalogue();
    } else {
      fb.textContent = d.error || 'Erreur';
      fb.style.display = '';
      fb.style.color = 'var(--mg-ember)';
    }
  });
}

// ── Vote festival controls ────────────────────────────────────────────────────

const btnVoteOpen              = document.getElementById('btn-vote-open');
const btnVoteClose             = document.getElementById('btn-vote-close');
const btnVotePalmares          = document.getElementById('btn-vote-palmares');
const btnVoteResetFromClosed   = document.getElementById('btn-vote-reset-from-closed');
const btnVoteResetFromPalmares = document.getElementById('btn-vote-reset-from-palmares');
const btnVoteDisplayHide       = document.getElementById('btn-vote-display-hide');
const voteRevealCounter        = document.getElementById('vote-reveal-counter');
const voteBadge                = document.getElementById('vote-status-badge');
const voteErrorMsg             = document.getElementById('vote-error-msg');
const catBtnsWrap              = document.getElementById('vote-display-category-btns');
const palmaresGatsBtnsWrap     = document.getElementById('vote-palmares-category-btns');

let trackingInterval = null;

const VOTE_GROUPS = ['roulette', 'vote', 'closed', 'palmares'];

function showVoteGroup(mode) {
  VOTE_GROUPS.forEach(m => {
    const el = document.getElementById('vote-group-' + m);
    if (el) el.style.display = (m === mode) ? '' : 'none';
  });
}

function hideVoteError() {
  if (voteErrorMsg) voteErrorMsg.style.display = 'none';
}

function showVoteError(msg) {
  if (!voteErrorMsg) return;
  voteErrorMsg.textContent = msg;
  voteErrorMsg.style.display = '';
}

async function loadVoteTracking(sessionId = null) {
  try {
    const url = sessionId
      ? `/api/admin/vote/tracking?session_id=${sessionId}`
      : '/api/admin/vote/tracking';
    const r = await fetch(url);
    if (!r.ok) return;
    const data = await r.json();
    renderVoteTracking(data);
  } catch(e) {}
}

async function loadVoteSessions() {
  try {
    const r = await fetch('/api/admin/vote/sessions');
    if (!r.ok) return;
    const data = await r.json();
    const sel = document.getElementById('vote-tracking-session-select');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">Session en cours</option>' +
      data.sessions.map(s =>
        `<option value="${s.id}">#${s.id} — ${s.status}${s.closed_at ? ' (' + s.closed_at.slice(0, 10) + ')' : ''}</option>`
      ).join('');
    sel.value = cur;
  } catch(e) {}
}

document.getElementById('vote-tracking-session-select')
  ?.addEventListener('change', function() {
    const sid = this.value ? parseInt(this.value) : null;
    clearInterval(trackingInterval);
    trackingInterval = null;
    if (!sid) trackingInterval = setInterval(loadVoteTracking, 3000);
    loadVoteTracking(sid);
  });

function renderVoteTracking(data) {
  const cont = document.getElementById('vote-tracking-content');
  if (!cont) return;
  if (!data.session_id || data.categories.length === 0) {
    cont.innerHTML = '<span style="color:var(--mg-rosewood)">Aucune catégorie.</span>';
    return;
  }
  cont.innerHTML = data.categories.map(cat => {
    const filmMap = {};
    cat.films.forEach(f => { filmMap[f.id] = f.title; });

    const header = `<th>Joueur</th>` +
      cat.films.map(f =>
        `<th style="font-size:0.75rem">${f.title}</th>`
      ).join('') +
      `<th>Boost</th>`;

    const rows = cat.voters.length === 0
      ? `<tr><td colspan="${cat.films.length + 2}"
             style="color:var(--mg-rosewood)">Aucun vote</td></tr>`
      : cat.voters.map(v => {
          const rankMap = {};
          v.rankings.forEach(r => { rankMap[r.film_id] = r.rank; });
          return `<tr>
            <td>${v.username}</td>
            ${cat.films.map(f =>
              `<td>${rankMap[f.id] ?? '—'}</td>`
            ).join('')}
            <td>${v.boost > 0 ? v.boost + '🔥' : '—'}</td>
          </tr>`;
        }).join('');

    return `
      <div class="mb-4">
        <div class="small fw-bold mb-1" style="color:var(--mg-blush)">
          ${cat.name} — ${cat.voter_count} votant(s) — ${cat.total_boost} jetons misés
        </div>
        <div style="overflow-x:auto">
          <table class="table table-sm mb-0" style="font-size:0.8rem">
            <thead><tr>${header}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }).join('');
}

async function voteAction(btn, url, body = {}) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.style.opacity = '0.5';
  btn.textContent = '…';
  hideVoteError();
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Erreur serveur');
    await updateVoteStatus();
  } catch(e) {
    showVoteError(e.message);
  } finally {
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.textContent = orig;
  }
}

async function updateVoteStatus() {
  try {
    const r = await fetch('/api/session/status');
    if (!r.ok) return;
    const d = await r.json();

    const mode     = d.app_mode || 'roulette';
    const vs       = d.vote_session;
    const revealed = Array.isArray(d.revealed_categories) ? d.revealed_categories : [];
    const total    = d.total_vote_categories || 0;
    const dispCat  = d.vote_display_category_id || null;
    const cats     = Array.isArray(d.vote_categories) ? d.vote_categories : [];

    if (voteBadge) {
      voteBadge.style.display = '';
      const labels = {
        roulette: 'Roulette en cours',
        vote:     `Vote ouvert — session #${vs ? vs.id : '?'}`,
        closed:   vs ? `Vote fermé — session #${vs.id}` : 'Vote fermé',
        palmares: 'Palmarès',
      };
      voteBadge.textContent = labels[mode] || mode;
      const styles = {
        roulette: { bg: 'var(--mg-velvet)', color: 'var(--mg-rosewood)' },
        vote:     { bg: 'var(--mg-velvet)', color: 'var(--mg-blush)' },
        closed:   { bg: 'var(--mg-oxblood)', color: 'var(--mg-ember)' },
        palmares: { bg: 'var(--mg-velvet)', color: 'var(--mg-flame)' },
      };
      const s = styles[mode] || styles.roulette;
      voteBadge.style.background = s.bg;
      voteBadge.style.color = s.color;
    }

    showVoteGroup(mode);

    if (mode === 'vote' && catBtnsWrap) {
      catBtnsWrap.innerHTML = cats.map(c =>
        `<button class="btn btn-sm ${dispCat === c.id ? 'btn-primary' : 'btn-outline-light'}"
                 data-cat-id="${c.id}">▶ ${c.name}</button>`
      ).join('');
    }

    // A3 — pending notice when vote is open but no category is projected yet
    const pendingNotice = document.getElementById('vote-pending-notice');
    if (pendingNotice) {
      pendingNotice.style.display = (mode === 'vote' && !dispCat) ? '' : 'none';
    }

    // A5 — palmares category buttons (all cats, revealed highlighted)
    if (mode === 'palmares' && palmaresGatsBtnsWrap) {
      const allRevealed = revealed.length >= cats.length && cats.length > 0;
      palmaresGatsBtnsWrap.innerHTML = cats.map(c => {
        const isRevealed = revealed.includes(c.id);
        return `<button class="btn btn-sm ${isRevealed ? 'btn-primary' : 'btn-outline-secondary'}"
                        data-palmares-cat-id="${c.id}">▶ ${c.name}${isRevealed ? ' ✓' : ''}</button>`;
      }).join('');
      if (voteRevealCounter) {
        voteRevealCounter.textContent = allRevealed
          ? `${revealed.length}/${cats.length} révélées — cliquez pour reprojeter`
          : `${revealed.length}/${cats.length} révélées`;
      }
    }

    // A4 — tracking visible en mode vote, closed et palmares
    const trackingSection = document.getElementById('vote-tracking-section');
    if (mode === 'vote' || mode === 'closed' || mode === 'palmares') {
      if (trackingSection) trackingSection.style.display = '';
      loadVoteSessions();
      const sel = document.getElementById('vote-tracking-session-select');
      if (!sel?.value) {
        if (!trackingInterval) trackingInterval = setInterval(loadVoteTracking, 3000);
        loadVoteTracking();
      }
    } else {
      if (trackingSection) trackingSection.style.display = 'none';
      clearInterval(trackingInterval);
      trackingInterval = null;
    }
  } catch(e) {}
}

btnVoteOpen?.addEventListener('click', () =>
  voteAction(btnVoteOpen, '/api/admin/vote/open'));

btnVoteClose?.addEventListener('click', () =>
  voteAction(btnVoteClose, '/api/admin/vote/close'));

btnVotePalmares?.addEventListener('click', () =>
  voteAction(btnVotePalmares, '/api/admin/vote/palmares'));

[btnVoteResetFromClosed, btnVoteResetFromPalmares]
  .forEach(btn => btn?.addEventListener('click', () =>
    voteAction(btn, '/api/admin/vote/reset-mode')));

btnVoteDisplayHide?.addEventListener('click', () =>
  voteAction(btnVoteDisplayHide, '/api/admin/vote/display-category', {category_id: null}));

catBtnsWrap?.addEventListener('click', e => {
  const btn = e.target.closest('[data-cat-id]');
  if (!btn) return;
  voteAction(btn, '/api/admin/vote/display-category',
             {category_id: parseInt(btn.dataset.catId)});
});

palmaresGatsBtnsWrap?.addEventListener('click', e => {
  const btn = e.target.closest('[data-palmares-cat-id]');
  if (!btn) return;
  voteAction(btn, '/api/admin/vote/reveal-next',
             {category_id: parseInt(btn.dataset.palmaresCatId)});
});


// Boot: load catalogue + poll vote status every 2s
loadCatalogue();
loadVoteSessions();
updateVoteStatus();
setInterval(updateVoteStatus, 2000);

