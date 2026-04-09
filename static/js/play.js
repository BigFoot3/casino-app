'use strict';

const CSRF = document.querySelector('meta[name="csrf-token"]').content;

const $ = id => document.getElementById(id);

const msgWaiting  = $('msg-waiting');
const msgSpinning = $('msg-spinning');
const msgCountdown= $('msg-countdown');
const betForm     = $('bet-form');
const betType     = $('bet-type');
const betSubmit   = $('bet-submit');
const betError    = $('bet-error');
const countdownEl = $('countdown');
const resultPanel = $('result-panel');
const balanceEl   = $('balance');
const maxTokensEl = $('max-tokens');

let betPlaced   = false;
let resultShown = false;
let pollTimer   = null;
let cdInterval  = null;

// ── Bet type selector ────────────────────────────────────────────────────────
betType.addEventListener('change', () => {
  $('color-options').style.display  = betType.value === 'color'  ? '' : 'none';
  $('parity-options').style.display = betType.value === 'parity' ? '' : 'none';
  $('number-options').style.display = betType.value === 'number' ? '' : 'none';
});

// Value buttons (color / parity)
document.querySelectorAll('.bet-val-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const group = btn.closest('[id$="-options"]');
    group.querySelectorAll('.bet-val-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const hiddenId = group.id.replace('-options', '-value');
    document.getElementById(hiddenId).value = btn.dataset.val;
  });
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function showOnly(...els) {
  [msgWaiting, msgSpinning, msgCountdown, betForm, resultPanel].forEach(e => e.style.display = 'none');
  els.forEach(e => { if (e) e.style.display = ''; });
}

function startCountdown(seconds) {
  clearInterval(cdInterval);
  countdownEl.textContent = seconds;
  cdInterval = setInterval(() => {
    seconds = Math.max(0, seconds - 1);
    countdownEl.textContent = seconds;
    if (seconds <= 0) clearInterval(cdInterval);
  }, 1000);
}

function getBetValue() {
  const t = betType.value;
  if (t === 'color')  return $('color-value').value;
  if (t === 'parity') return $('parity-value').value;
  if (t === 'number') return $('number-value').value;
  return '';
}

// ── Status polling ────────────────────────────────────────────────────────────
async function pollStatus() {
  if (betPlaced) {
    pollResult();
    return;
  }
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

    if (d.status === 'open') {
      showOnly(msgCountdown, betForm);
      startCountdown(d.time_remaining_seconds);
      maxTokensEl.textContent = balanceEl.textContent;
    } else if (d.status === 'spinning') {
      showOnly(msgSpinning);
      clearInterval(cdInterval);
    } else {
      showOnly(msgWaiting);
      clearInterval(cdInterval);
    }
  } catch (e) { /* ignore network hiccup */ }

  pollTimer = setTimeout(pollStatus, 2000);
}

// ── Result polling ────────────────────────────────────────────────────────────
async function pollResult() {
  if (resultShown) return;
  try {
    const r = await fetch('/api/session/result');
    if (r.status === 404) {
      pollTimer = setTimeout(pollResult, 2000);
      return;
    }
    const d = await r.json();
    resultShown = true;
    clearTimeout(pollTimer);

    $('result-number').textContent = d.winning_number;
    showOnly(resultPanel);

    if (d.user_bet) {
      if (d.user_bet.result === 'win') {
        $('result-win').style.display = '';
        $('result-payout').textContent = d.user_bet.payout;
        balanceEl.textContent = parseInt(balanceEl.textContent) + d.user_bet.payout;
      } else {
        $('result-loss').style.display = '';
      }
    } else {
      $('result-no-bet').style.display = '';
    }
  } catch (e) {
    pollTimer = setTimeout(pollResult, 2000);
  }
}

// ── Bet submission ────────────────────────────────────────────────────────────
betForm.addEventListener('submit', async e => {
  e.preventDefault();
  betError.style.display = 'none';

  const btype  = betType.value;
  const bvalue = getBetValue();
  const amount = parseInt($('bet-amount').value);

  if (!btype)  return showError('Choisissez un type de mise.');
  if (!bvalue) return showError('Choisissez une valeur.');
  if (!amount || amount <= 0) return showError('Montant invalide.');

  betSubmit.disabled = true;

  const resp = await fetch('/api/bet', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
    body: JSON.stringify({bet_type: btype, bet_value: bvalue, amount})
  });
  const data = await resp.json();

  if (resp.status === 409) {
    showError('Mise déjà enregistrée.');
    betSubmit.disabled = false;
    return;
  }
  if (!resp.ok) {
    showError(data.error || 'Erreur lors de la mise.');
    betSubmit.disabled = false;
    return;
  }

  betPlaced = true;
  balanceEl.textContent = data.new_balance;
  showOnly(msgSpinning);
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollResult, 2000);
});

function showError(msg) {
  betError.textContent = msg;
  betError.style.display = '';
}

// ── Start ────────────────────────────────────────────────────────────────────
pollStatus();
