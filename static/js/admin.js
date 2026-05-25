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

// ── Quick-add tokens (+150 / +350) ───────────────────────────────────────────
document.querySelectorAll('.add-tokens-quick-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const uid    = parseInt(btn.dataset.id);
    const amount = parseInt(btn.dataset.amount);
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

// ── Add reward ────────────────────────────────────────────────────────────────
$('btn-add-reward').addEventListener('click', async () => {
  const name  = $('rw-name').value.trim();
  const desc  = $('rw-desc').value.trim();
  const cost  = parseInt($('rw-cost').value);
  const stock = parseInt($('rw-stock').value) || 0;
  if (!name || !cost || cost <= 0) return alert('Nom et coût requis.');
  const [s, d] = await post('/api/admin/rewards', {name, description: desc, token_cost: cost, stock});
  if (s === 200) {
    location.reload();
  } else {
    alert(d.error || 'Erreur');
  }
});

// ── Save reward stock ─────────────────────────────────────────────────────────
document.querySelectorAll('.rw-save-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const rid   = parseInt(btn.dataset.id);
    const stock = parseInt(document.querySelector(`.rw-stock-input[data-id="${rid}"]`).value);
    const [s]   = await post(`/api/admin/rewards/${rid}`, {stock});
    if (s !== 200) alert('Erreur lors de la sauvegarde.');
  });
});

// ── Delete reward ─────────────────────────────────────────────────────────────
document.querySelectorAll('.rw-delete-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const rid  = parseInt(btn.dataset.id);
    const name = btn.dataset.name;
    showConfirm(
      'Supprimer la récompense',
      `Supprimer « ${name} » ? L'historique des attributions sera également supprimé.`,
      async () => {
        const [s, d] = await post(`/api/admin/rewards/${rid}/delete`);
        if (s === 200) {
          const row = document.getElementById(`reward-row-${rid}`);
          if (row) row.remove();
        } else {
          alert(d.error || 'Erreur');
        }
      }
    );
  });
});

// ── Toggle reward ─────────────────────────────────────────────────────────────
document.querySelectorAll('.rw-toggle-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const rid    = parseInt(btn.dataset.id);
    const active = btn.dataset.active === '1' ? 0 : 1;
    const [s]    = await post(`/api/admin/rewards/${rid}`, {active});
    if (s === 200) location.reload();
    else alert('Erreur');
  });
});

// ── Give reward ───────────────────────────────────────────────────────────────
const btnGiveReward = $('btn-give-reward');
if (btnGiveReward) {
  btnGiveReward.addEventListener('click', () => {
    const username  = $('give-reward-username').value.trim();
    const reward_id = parseInt($('give-reward-select').value);
    if (!username) return alert('Saisissez le nom du joueur.');
    if (!reward_id) return;
    showConfirm(
      'Attribuer une récompense',
      `Attribuer la récompense à « ${username} » ?`,
      async () => {
        const [s, d] = await post('/api/admin/reward/give', {username, reward_id});
        const statusEl = $('give-reward-status');
        if (s === 200) {
          statusEl.textContent = `✅ Attribuée à ${username}`;
          statusEl.className = 'small text-success';
          $('give-reward-username').value = '';
          // Refresh page so stock counts update
          setTimeout(() => location.reload(), 1200);
        } else {
          statusEl.textContent = d.error || 'Erreur';
          statusEl.className = 'small text-danger';
        }
      }
    );
  });
}

// ── Vote ──────────────────────────────────────────────────────────────────────
const btnVoteOpen     = $('btn-vote-open');
const btnVoteClose    = $('btn-vote-close');
const btnVotePalmares = $('btn-vote-palmares');
const btnVoteRoulette = $('btn-vote-roulette');

if (btnVoteOpen) {
  btnVoteOpen.addEventListener('click', async () => {
    const film_title = $('vote-film-title').value.trim();
    if (!film_title) return alert('Saisissez le titre du film.');
    const [s, d] = await post('/api/vote/open', {film_title});
    if (s === 200) location.reload();
    else alert(d.error || 'Erreur');
  });
}

if (btnVoteClose) {
  btnVoteClose.addEventListener('click', async () => {
    if (!confirm('Fermer le vote en cours ?')) return;
    const [s, d] = await post('/api/vote/close');
    if (s === 200) location.reload();
    else alert(d.error || 'Erreur');
  });
}

if (btnVotePalmares) {
  btnVotePalmares.addEventListener('click', async () => {
    const [s, d] = await post('/api/vote/palmares');
    if (s === 200) location.reload();
    else alert(d.error || 'Erreur');
  });
}

if (btnVoteRoulette) {
  btnVoteRoulette.addEventListener('click', async () => {
    const [s, d] = await post('/api/vote/reset-mode');
    if (s === 200) location.reload();
    else alert(d.error || 'Erreur');
  });
}

// ── Films list collapse — chevron toggle ─────────────────────────────────────
const filmsListEl  = document.getElementById('films-list');
const filmsChevron = $('films-list-chevron');
if (filmsListEl && filmsChevron) {
  filmsListEl.addEventListener('show.bs.collapse', () => { filmsChevron.textContent = '▲'; });
  filmsListEl.addEventListener('hide.bs.collapse', () => { filmsChevron.textContent = '▼'; });
}

// ── Film search filter ────────────────────────────────────────────────────────
const filmSearch = $('film-search');
if (filmSearch) {
  filmSearch.addEventListener('input', () => {
    const q = filmSearch.value.toLowerCase().trim();
    const rows = document.querySelectorAll('#films-table tbody tr');
    let visible = 0;
    rows.forEach(row => {
      const title = row.querySelector('td')?.textContent.toLowerCase() || '';
      const show  = !q || title.includes(q);
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    const noResult = $('films-no-result');
    if (noResult) noResult.style.display = (visible === 0 && rows.length > 0) ? '' : 'none';
  });
}

// ── Film delete ───────────────────────────────────────────────────────────────
document.querySelectorAll('.film-delete-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const vid   = parseInt(btn.dataset.id);
    const title = btn.dataset.title;
    showConfirm(
      'Supprimer le film',
      `Supprimer « ${title} » et tous ses votes ? Cette action est irréversible.`,
      async () => {
        const [s, d] = await post(`/api/admin/vote/${vid}/delete`);
        if (s === 200) {
          const row = document.getElementById(`film-row-${vid}`);
          if (row) row.remove();
        } else {
          alert(d.error || 'Erreur');
        }
      }
    );
  });
});

// ── Film rename ───────────────────────────────────────────────────────────────
document.querySelectorAll('.film-rename-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const vid      = parseInt(btn.dataset.id);
    const current  = btn.dataset.title;
    const newTitle = prompt(`Nouveau titre pour « ${current} » :`, current);
    if (!newTitle || newTitle.trim() === current) return;
    const [s, d] = await post(`/api/admin/vote/${vid}/rename`, {film_title: newTitle.trim()});
    if (s === 200) {
      const cell = document.getElementById(`film-title-${vid}`);
      if (cell) cell.textContent = d.film_title;
      btn.dataset.title = d.film_title;
      const delBtn = document.querySelector(`.film-delete-btn[data-id="${vid}"]`);
      if (delBtn) delBtn.dataset.title = d.film_title;
    } else {
      alert(d.error || 'Erreur');
    }
  });
});

// ── Vote results polling (when vote is open) ──────────────────────────────────
const vrPanel = $('vote-results-panel');
if (vrPanel) {
  let vrSessionId = null;
  // Read session id from the close button's data or from page context
  const closeBtn = $('btn-vote-close');
  // The session_id comes from the current_vote embedded in the page — read via API
  async function loadVoteResults() {
    try {
      const r = await fetch('/api/session/status');
      const d = await r.json();
      if (d.vote_session && d.vote_session.id) {
        vrSessionId = d.vote_session.id;
        const r2 = await fetch(`/api/vote/results?session_id=${vrSessionId}`);
        if (r2.ok) {
          const data = await r2.json();
          $('vr-avg').textContent   = data.avg_weighted_score || '—';
          $('vr-count').textContent = data.voter_count;
          $('vr-b0').textContent    = data.bonus_breakdown[0];
          $('vr-b25').textContent   = data.bonus_breakdown[25];
          $('vr-b50').textContent   = data.bonus_breakdown[50];
          const tbody = $('vr-table-body');
          tbody.innerHTML = '';
          (data.votes || []).forEach(v => {
            const tr = document.createElement('tr');
            const td1 = document.createElement('td');
            td1.textContent = v.username;
            const td2 = document.createElement('td');
            td2.textContent = `${v.score}/10`;
            const td3 = document.createElement('td');
            td3.textContent = `${v.bonus_amount} tok`;
            const td4 = document.createElement('td');
            td4.textContent = String(v.weighted_score);
            tr.appendChild(td1);
            tr.appendChild(td2);
            tr.appendChild(td3);
            tr.appendChild(td4);
            tbody.appendChild(tr);
          });
          $('vote-results-loading').style.display = 'none';
          $('vote-results-content').style.display = '';
        }
      }
    } catch(e) {}
    setTimeout(loadVoteResults, 5000);
  }
  loadVoteResults();
}
