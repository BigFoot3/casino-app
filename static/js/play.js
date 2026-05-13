'use strict';

// Spin animation duration — ball is visually frozen at t=9000ms (app.js:629:
// ballTrack.style.cssText = 'transform: rotate(-'+degree+'deg);')
const SPIN_DURATION_MS = 9000;

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const ME   = (document.querySelector('meta[name="username"]') || {}).content || '';

const $ = id => document.getElementById(id);

const msgWaiting   = $('msg-waiting');
const msgSpinning  = $('msg-spinning');
const msgCountdown = $('msg-countdown');
const msgPalmares  = $('msg-palmares');
const betForm      = $('bet-form');
const betSubmit    = $('bet-submit');
const betClear     = $('bet-clear');
const betError     = $('bet-error');
const countdownEl  = $('countdown');
const resultPanel  = $('result-panel');
const balanceEl    = $('balance');
const totalMisedEl = $('total-mised');
const soldeResteEl = $('solde-reste');
const votePanel    = $('vote-panel');

let betPlaced       = false;
let betSessionId    = null;
let resultShown     = false;
let pollTimer       = null;
let cdInterval      = null;
let lastOpenSession = null;   // tracks session_id of last 'open' state seen

// ── Multi-bet state ───────────────────────────────────────────────────────────
// pendingBets: Map<"type:value", {bet_type, bet_value, amount}>
const pendingBets = new Map();
let chipValue = 1;  // currently selected chip denomination

const BET_LABELS = {
  number: v => `Numéro ${v}`,
  color:  v => v === 'red' ? 'Rouge' : 'Noir',
  parity: v => v === 'even' ? 'Pair' : 'Impair',
  column: v => `Colonne ${v} (2→1)`,
  dozen:  v => v === '1' ? '1ère douzaine (1-12)' : v === '2' ? '2ème douzaine (13-24)' : '3ème douzaine (25-36)',
  half:   v => v === 'low' ? '1-18' : '19-36',
};

const numBtns  = document.querySelectorAll('#roulette-grid .num-btn[data-type]');
const halfBtns = document.querySelectorAll('#half-bets .half-btn[data-type]');
const dozBtns  = document.querySelectorAll('#dozen-bets .doz-btn[data-type]');
const outBtns  = document.querySelectorAll('#outside-bets .out-btn[data-type]');
const allBtns  = [...numBtns, ...halfBtns, ...dozBtns, ...outBtns];

function betKey(type, value) { return `${type}:${value}`; }

function updateBadge(btn) {
  const key   = betKey(btn.dataset.type, btn.dataset.val);
  const entry = pendingBets.get(key);
  let badge   = btn.querySelector('.bet-badge');
  if (entry && entry.amount > 0) {
    if (!badge) { badge = document.createElement('div'); badge.className = 'bet-badge'; btn.appendChild(badge); }
    badge.textContent = entry.amount;
  } else if (badge) {
    badge.remove();
  }
}

function updateTotals() {
  let total = 0;
  pendingBets.forEach(b => total += b.amount);
  totalMisedEl.textContent = total;
  soldeResteEl.textContent = (parseInt(balanceEl.textContent) || 0) - total;
}

// ── Grid interaction ──────────────────────────────────────────────────────────
allBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    if (betPlaced) return;
    const type  = btn.dataset.type;
    const value = btn.dataset.val;
    const key   = betKey(type, value);
    const balance = parseInt(balanceEl.textContent) || 0;
    let total = 0; pendingBets.forEach(b => total += b.amount);
    if (total + chipValue > balance) { showError('Solde insuffisant pour ajouter cette mise.'); return; }
    betError.style.display = 'none';
    if (pendingBets.has(key)) { pendingBets.get(key).amount += chipValue; }
    else { pendingBets.set(key, {bet_type: type, bet_value: value, amount: chipValue}); }
    updateBadge(btn);
    updateTotals();
  });
});

// ── Chip denomination ─────────────────────────────────────────────────────────
document.querySelectorAll('.chip-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    chipValue = parseInt(btn.dataset.chip);
    document.querySelectorAll('.chip-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// ── Clear button ──────────────────────────────────────────────────────────────
betClear.addEventListener('click', () => {
  pendingBets.clear();
  allBtns.forEach(btn => updateBadge(btn));
  updateTotals();
  betError.style.display = 'none';
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function showOnly(...els) {
  [msgWaiting, msgSpinning, msgCountdown, betForm, resultPanel, msgPalmares, votePanel]
    .forEach(e => e.style.display = 'none');
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

// ── Status polling ────────────────────────────────────────────────────────────
async function pollStatus() {
  if (betPlaced && !resultShown) { pollResult(); return; }
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

    const appMode = d.app_mode || 'roulette';

    if (appMode === 'vote') {
      showOnly(votePanel);
      clearInterval(cdInterval);
      if (d.vote_session) {
        $('vote-film-name').textContent = d.vote_session.film_title;
      }
      updateVoteBalancePreview();
    } else if (appMode === 'palmares') {
      showOnly(msgPalmares);
      clearInterval(cdInterval);
    } else {
      // Normal roulette mode
      if (d.status === 'open') {
        // New session detected → clear any leftover pending bets from previous round
        if (d.session_id && d.session_id !== lastOpenSession) {
          lastOpenSession = d.session_id;
          pendingBets.clear();
          allBtns.forEach(btn => updateBadge(btn));
          updateTotals();
          betError.style.display = 'none';
          betPlaced    = false;
          resultShown  = false;
          betSubmit.disabled = false;
          betClear.disabled  = false;
          allBtns.forEach(btn => btn.style.pointerEvents = '');
          $('bet-recap').style.display = 'none';
        }
        showOnly(msgCountdown, betForm);
        startCountdown(d.time_remaining_seconds);
      } else if (d.status === 'spinning') {
        if (!resultShown) { showOnly(msgSpinning); }  // BUG 1: preserve result panel during grace period
        clearInterval(cdInterval);
      } else {
        // BUG 2: reset all bet state when session returns to waiting
        if (betPlaced || resultShown) {
          betPlaced    = false;
          betSessionId = null;
          resultShown  = false;
          pendingBets.clear();
          allBtns.forEach(btn => updateBadge(btn));
          allBtns.forEach(btn => btn.style.pointerEvents = '');
          updateTotals();
          betSubmit.disabled = false;
          betClear.disabled  = false;
          betError.style.display = 'none';
          $('bet-recap').style.display = 'none';
          lastOpenSession = null;
        }
        showOnly(msgWaiting);
        clearInterval(cdInterval);
      }
    }
  } catch(e) {
    pollTimer = setTimeout(pollStatus, 3000);
    return;
  }
  pollTimer = setTimeout(pollStatus, 2000);
}

// ── Result polling ────────────────────────────────────────────────────────────
async function pollResult() {
  if (resultShown) return;
  try {
    const r = await fetch('/api/session/result');
    if (r.status === 404) { pollTimer = setTimeout(pollResult, 2000); return; }
    const d = await r.json();
    if (betSessionId !== null && d.session_id !== betSessionId) {
      pollTimer = setTimeout(pollResult, 2000); return;
    }
    resultShown = true;
    clearTimeout(pollTimer);

    // Delay result by SPIN_DURATION_MS — ball frozen at t=9000ms on display page
    setTimeout(() => {
      $('result-number').textContent = d.winning_number === 0 ? 67 : d.winning_number;
      showOnly(resultPanel);

      const listEl = $('result-bets-list');
      listEl.innerHTML = '';
      let netDelta     = 0;  // net profit/loss — for display only
      let balanceDelta = 0;  // sum of payouts — amount was already deducted at bet time

      if (d.user_bets && d.user_bets.length > 0) {
        d.user_bets.forEach(bet => {
          const won   = bet.payout > 0;
          const delta = won ? bet.payout - bet.amount : -bet.amount;
          netDelta     += delta;
          balanceDelta += bet.payout;  // 0 on loss → no-op; payout on win restores stake + profit
          const row   = document.createElement('div');
          row.className = 'result-bet-row ' + (won ? 'result-win-row' : 'result-loss-row');
          row.innerHTML =
            `<span>${BET_LABELS[bet.bet_type]?.(String(bet.bet_value)) ?? bet.bet_value} (${bet.amount} t.)</span>` +
            `<span>${won ? '+' + bet.payout : '−' + bet.amount} tokens ${won ? '✅' : '❌'}</span>`;
          listEl.appendChild(row);
        });
        const totalRow = document.createElement('div');
        totalRow.className = 'result-total-row';
        totalRow.innerHTML = `<strong>Total : ${netDelta >= 0 ? '+' : ''}${netDelta} tokens</strong>`;
        listEl.appendChild(totalRow);
        balanceEl.textContent = (parseInt(balanceEl.textContent) || 0) + balanceDelta;
      } else {
        listEl.innerHTML = '<div class="alert alert-secondary">Tu n\'avais pas misé sur cette partie.</div>';
      }
      // BUG 1+3: resume status polling after animation so new sessions are detected
      pollTimer = setTimeout(pollStatus, 2000);
    }, SPIN_DURATION_MS);
  } catch(e) {
    pollTimer = setTimeout(pollResult, 2000);
  }
}

// ── Bet submission ────────────────────────────────────────────────────────────
betForm.addEventListener('submit', async e => {
  e.preventDefault();
  betError.style.display = 'none';

  if (pendingBets.size === 0) return showError('Placez au moins une mise sur la table.');

  betSubmit.disabled = true;
  betClear.disabled  = true;
  // Prevent further clicks on grid
  allBtns.forEach(btn => btn.style.pointerEvents = 'none');

  let lastSid    = null;
  let placedCount = 0;
  let totalPlaced = 0;
  let currentBalance = parseInt(balanceEl.textContent) || 0;

  // Sequential POSTs — each deducts from balance atomically; stop on first error
  for (const [, bet] of pendingBets) {
    const resp = await fetch('/api/bet', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
      body: JSON.stringify({bet_type: bet.bet_type, bet_value: bet.bet_value, amount: bet.amount})
    });
    const data = await resp.json();
    if (!resp.ok) {
      const suffix = placedCount > 0 ? ` (${placedCount} mise(s) déjà enregistrée(s))` : '';
      showError((data.error || 'Erreur lors de la mise.') + suffix);
      betSubmit.disabled = false;
      betClear.disabled  = false;
      allBtns.forEach(btn => btn.style.pointerEvents = '');
      return;
    }
    lastSid        = data.session_id;
    currentBalance = data.new_balance;
    placedCount++;
    totalPlaced   += bet.amount;
  }

  betPlaced    = true;
  betSessionId = lastSid;
  balanceEl.textContent = currentBalance;

  const recap = $('bet-recap');
  recap.textContent = `${placedCount} mise(s) placée(s) — total misé : ${totalPlaced} tokens`;
  recap.style.display = '';

  showOnly(msgSpinning);
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollResult, 2000);
});

function showError(msg) {
  betError.textContent = msg;
  betError.style.display = '';
}

// ── FEATURE 2: live bet chips on play page ────────────────────────────────────
function usernameHue(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0x7fffffff;
  return h % 360;
}
function chipColor(name) { return `hsl(${usernameHue(name)}, 70%, 52%)`; }

let lastPlayBetSig = '';

function clearPlayChips() {
  document.querySelectorAll('.play-chip').forEach(el => el.remove());
  lastPlayBetSig = '';
}

function renderPlayChips(bets) {
  const sig = JSON.stringify(bets);
  if (sig === lastPlayBetSig) return;
  lastPlayBetSig = sig;
  document.querySelectorAll('.play-chip').forEach(el => el.remove());

  const cellCount = new Map();
  bets.forEach(bet => {
    const cell = document.querySelector(
      `#roulette-grid [data-type="${bet.bet_type}"][data-val="${bet.bet_value}"],` +
      `#outside-bets [data-type="${bet.bet_type}"][data-val="${bet.bet_value}"]`
    );
    if (!cell) return;
    const key = `${bet.bet_type}:${bet.bet_value}`;
    const idx = cellCount.get(key) || 0;
    cellCount.set(key, idx + 1);

    const chip = document.createElement('div');
    chip.className = 'play-chip' + (bet.username === ME ? ' own-bet' : '');
    chip.style.background = chipColor(bet.username);
    chip.style.top  = `calc(50% + ${idx * 4}px)`;
    chip.style.left = `calc(50% + ${idx * 4}px)`;
    chip.style.transform = 'none';
    chip.title = `${bet.username}: ${bet.amount} token${bet.amount > 1 ? 's' : ''}`;
    chip.textContent = bet.username.charAt(0).toUpperCase();
    cell.appendChild(chip);
  });
}

async function pollPlayBets() {
  try {
    const r = await fetch('/api/session/bets');
    if (r.ok) {
      const bets = await r.json();
      renderPlayChips(bets);
      if (bets.length === 0) clearPlayChips();
    }
  } catch(e) {}
  setTimeout(pollPlayBets, 2000);
}

// ── Vote UI ───────────────────────────────────────────────────────────────────
let selectedBonus = 0;

const scoreSlider = $('vote-score-slider');
const scoreDisplay = $('vote-score-display');

if (scoreSlider) {
  scoreSlider.addEventListener('input', () => {
    scoreDisplay.textContent = scoreSlider.value;
    updateVoteBalancePreview();
  });
}

document.querySelectorAll('.vote-bonus-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const bonus = parseInt(btn.dataset.bonus);
    const balance = parseInt(balanceEl.textContent) || 0;
    // Check affordability for new bonus vs current selection
    const cost = bonus - selectedBonus;  // net cost (negative = refund)
    if (balance + selectedBonus - bonus < 0) {
      const fb = $('vote-feedback');
      fb.textContent = 'Solde insuffisant pour ce bonus.';
      fb.className = 'alert alert-danger mt-2';
      fb.style.display = '';
      return;
    }
    $('vote-feedback').style.display = 'none';
    selectedBonus = bonus;
    document.querySelectorAll('.vote-bonus-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    updateVoteBalancePreview();
  });
});

function updateVoteBalancePreview() {
  const balance = parseInt(balanceEl.textContent) || 0;
  // Preview assumes 0 was previously spent (first vote); will be adjusted server-side for re-votes
  $('vote-balance-preview').textContent = balance - selectedBonus;
}

const btnVoteSubmit = $('btn-vote-submit');
if (btnVoteSubmit) {
  btnVoteSubmit.addEventListener('click', async () => {
    const score = parseInt(scoreSlider.value);
    const fb = $('vote-feedback');
    fb.style.display = 'none';
    $('vote-confirmed').style.display = 'none';
    btnVoteSubmit.disabled = true;

    const resp = await fetch('/api/vote/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
      body: JSON.stringify({score, bonus_amount: selectedBonus})
    });
    const data = await resp.json();
    btnVoteSubmit.disabled = false;

    if (!resp.ok) {
      fb.textContent = data.error || 'Erreur lors du vote.';
      fb.className = 'alert alert-danger mt-2';
      fb.style.display = '';
    } else {
      balanceEl.textContent = data.tokens_remaining;
      updateVoteBalancePreview();
      $('vote-confirmed').style.display = '';
    }
  });
}

// ── Start — affichage immédiat depuis l'état serveur injecté par Jinja2 ──────
(function applyInitialMode() {
  const mode = window.INITIAL_APP_MODE || 'roulette';
  const vs   = window.INITIAL_VOTE_SESSION;
  if (mode === 'vote') {
    showOnly(votePanel);
    if (vs && vs.film_title) $('vote-film-name').textContent = vs.film_title;
    updateVoteBalancePreview();
  } else if (mode === 'palmares') {
    showOnly(msgPalmares);
  } else {
    showOnly(msgWaiting);
  }
})();

pollStatus();
pollPlayBets();
