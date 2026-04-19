'use strict';

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const $ = id => document.getElementById(id);

// ── Password modal ────────────────────────────────────────────────────────────
let pwTimer = null;
const pwModal    = new bootstrap.Modal($('pwModal'), {backdrop: 'static', keyboard: false});
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
$('btn-open').addEventListener('click', async () => {
  const [s] = await post('/api/admin/session/open');
  if (s === 200) location.reload();
  else alert('Erreur : impossible d\'ouvrir la session.');
});

$('btn-spin').addEventListener('click', async () => {
  const [s] = await post('/api/admin/session/spin');
  if (s === 200) location.reload();
  else alert('Erreur : impossible de lancer la roue.');
});

$('btn-close').addEventListener('click', () => {
  showConfirm(
    'Fermer la session',
    'Forcer la fermeture de la session en cours ? Les mises en cours seront perdues.',
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

// ── Mode ──────────────────────────────────────────────────────────────────────
$('btn-mode').addEventListener('click', async () => {
  const mode     = $('mode-select').value;
  const interval = parseInt($('interval-input').value);
  const [s, d]   = await post('/api/admin/mode', {mode, interval});
  const statusEl = $('mode-status');
  if (s === 200) {
    statusEl.textContent = '✅ Appliqué';
    statusEl.className = 'text-success small';
  } else {
    statusEl.textContent = d.error || 'Erreur';
    statusEl.className = 'text-danger small';
  }
});

// ── Stop auto mode ────────────────────────────────────────────────────────────
const btnStopAuto = $('btn-stop-auto');
if (btnStopAuto) {
  btnStopAuto.addEventListener('click', async () => {
    const interval = parseInt($('interval-input').value) || 120;
    const [s, d]   = await post('/api/admin/mode', {mode: 'manual', interval});
    if (s === 200) {
      // Immediate UI update — do not wait for the next poll cycle
      btnStopAuto.style.display = 'none';
      const autoBadge = $('auto-mode-badge');
      if (autoBadge) autoBadge.style.display = 'none';
      const modeSelect = $('mode-select');
      if (modeSelect) modeSelect.value = 'manual';
      const statusEl = $('mode-status');
      statusEl.textContent = '✅ Mode manuel';
      statusEl.className   = 'text-success small';
    } else {
      alert(d.error || 'Erreur');
    }
  });
}

// ── Session status polling — drives button states from server state ───────────
const SESSION_STATUS_BADGE_CLASSES = {
  waiting:  'badge fs-6 bg-secondary',
  open:     'badge fs-6 bg-success',
  spinning: 'badge fs-6 bg-warning text-dark',
  closed:   'badge fs-6 bg-danger',
};

function updateControlsState(status, mode) {
  const btnOpen  = $('btn-open');
  const btnSpin  = $('btn-spin');
  const btnClose = $('btn-close');

  // Button enabled/disabled states are driven exclusively by server status
  switch (status) {
    case 'waiting':
    case 'closed':
      btnOpen.disabled  = false;
      btnSpin.disabled  = true;
      btnClose.disabled = true;
      break;
    case 'open':
      btnOpen.disabled  = true;
      btnSpin.disabled  = false;
      btnClose.disabled = false;
      break;
    case 'spinning':
      btnOpen.disabled  = true;
      btnSpin.disabled  = true;
      btnClose.disabled = true;
      break;
  }

  // Status badge
  const badge = $('session-status-badge');
  if (badge) {
    badge.className   = SESSION_STATUS_BADGE_CLASSES[status] || 'badge fs-6 bg-secondary';
    badge.textContent = status.toUpperCase();
  }

  // Auto mode indicator
  const isAuto    = (mode === 'auto');
  const autoBadge = $('auto-mode-badge');
  const stopBtn   = $('btn-stop-auto');
  if (autoBadge) autoBadge.style.display = isAuto ? '' : 'none';
  if (stopBtn)   stopBtn.style.display   = isAuto ? '' : 'none';
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
            tr.innerHTML = `<td>${v.username}</td><td>${v.score}/10</td><td>${v.bonus_amount} tok</td><td>${v.weighted_score}</td>`;
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
