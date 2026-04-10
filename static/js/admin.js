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
