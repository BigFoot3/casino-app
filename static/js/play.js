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
const msgPalmares  = $('msg-palmares');
const btnRulesModal = $('btn-rules-modal');

let betPlaced       = false;
let betSessionId    = null;
let resultShown     = false;
let resultFetching  = false;
let pollTimer       = null;
let cdInterval      = null;
let lastOpenSession = null;   // tracks session_id of last 'open' state seen
let gridLocked      = false;  // true only between submission and spin start
let lastKnownMode   = null;   // tracks app_mode transitions

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
    if (gridLocked) return;
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
  [msgWaiting, msgSpinning, msgCountdown, betForm, resultPanel, votePanel, msgPalmares]
    .forEach(e => e.style.display = 'none');
  els.forEach(e => { if (e) e.style.display = ''; });
  if (btnRulesModal) btnRulesModal.style.display = els.includes(votePanel) ? 'none' : '';
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

    // Sync token balance from server (reflects admin additions within 2s)
    if (d.tokens !== null && d.tokens !== undefined) {
      balanceEl.textContent = d.tokens;
    }

    // App mode transition handling
    const newMode = d.app_mode || 'roulette';
    if (lastKnownMode !== null && newMode !== lastKnownMode) {
      lastKnownMode = newMode;
      pollTimer = setTimeout(pollStatus, 2000);
      return;
    }
    if (lastKnownMode === null) lastKnownMode = newMode;

    if (newMode === 'vote') {
      showOnly(votePanel);
      loadVoteState();
      // Strategy 3: slow down polling while user types in a boost input
      const hasActiveInput = document.activeElement?.type === 'number' &&
                             document.activeElement?.closest('#vote-panel');
      pollTimer = setTimeout(pollStatus, hasActiveInput ? 5000 : 3000);
      return;
    }
    if (newMode === 'palmares') {
      showOnly(msgPalmares);
      pollTimer = setTimeout(pollStatus, 3000);
      return;
    }

    if (d.status === 'open') {
      // New session detected → clear any leftover pending bets from previous round
      if (d.session_id && d.session_id !== lastOpenSession) {
        lastOpenSession = d.session_id;
        // Keep pendingBets — user may have pre-filled during spinning
        updateTotals();
        betError.style.display = 'none';
        betPlaced      = false;
        resultShown    = false;
        resultFetching = false;
        gridLocked     = false;
        betSubmit.disabled = false;
        betClear.disabled  = false;
        allBtns.forEach(btn => btn.style.pointerEvents = '');
        $('bet-recap').style.display = 'none';
      }
      showOnly(msgCountdown, betForm);
      startCountdown(d.time_remaining_seconds);
    } else if (d.status === 'spinning') {
      clearInterval(cdInterval);
      // First tick in spinning: unlock grid, clear submitted bets so user pre-fills for next round
      if (gridLocked) {
        gridLocked = false;
        pendingBets.clear();
        allBtns.forEach(btn => { updateBadge(btn); btn.style.pointerEvents = ''; });
        updateTotals();
      }
      betSubmit.disabled = true;
      betClear.disabled  = false;
      if (!resultShown) { showOnly(msgSpinning, betForm); }
      else              { betForm.style.display = ''; }  // keep result panel, add form below
    } else {
      // BUG 2: reset all bet state when session returns to waiting
      if (betPlaced || resultShown) {
        betPlaced      = false;
        betSessionId   = null;
        resultShown    = false;
        resultFetching = false;
        gridLocked     = false;
        // Keep pendingBets for the upcoming session
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
  } catch(e) {
    pollTimer = setTimeout(pollStatus, 3000);
    return;
  }
  pollTimer = setTimeout(pollStatus, 2000);
}

// ── Result polling ────────────────────────────────────────────────────────────
async function pollResult() {
  if (resultShown || resultFetching) return;
  resultFetching = true;
  try {
    const r = await fetch('/api/session/result');
    if (r.status === 404) { resultFetching = false; pollTimer = setTimeout(pollResult, 2000); return; }
    const d = await r.json();
    if (betSessionId !== null && d.session_id !== betSessionId) {
      resultFetching = false; pollTimer = setTimeout(pollResult, 2000); return;
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
    resultFetching = false;
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
  gridLocked = true;
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

// ── Vote state ────────────────────────────────────────────────────────────────
let voteStateData   = null;
let voteBoostState  = {};   // category_id → amount (local pending)
let voteRankState   = {};   // category_id → [{film_id, rank}]
let sortableInstances = []; // track to destroy on reload

function estimatePoints(n, rank, boostAmount) {
  const base = Math.max(10, n * 2.5);
  const rawFloat = base * Math.pow(0.55, rank - 1);
  const pts = Math.max(1, Math.round(rawFloat * (1 + boostAmount / 100)));
  return pts;
}

async function loadVoteState() {
  try {
    const r = await fetch('/api/vote/state');
    if (!r.ok) return;
    voteStateData = await r.json();
    // Strategy 1: skip re-render while user types in a boost input
    if (document.activeElement?.type === 'number' &&
        document.activeElement?.closest('#vote-panel')) return;
    renderVotePanel(voteStateData);
  } catch(e) {}
}

function renderVotePanel(data) {
  const submitBtn = $('btn-vote-submit');
  if (!data || !data.session) {
    $('vote-categories-container').innerHTML = '<p class="text-muted">Aucune session de vote active.</p>';
    if (submitBtn) submitBtn.style.display = 'none';
    return;
  }
  if (!data.categories || data.categories.length === 0) {
    $('vote-categories-container').innerHTML =
      '<p class="text-muted">⏳ En attente de la prochaine catégorie…</p>';
    if (submitBtn) submitBtn.style.display = 'none';
    return;
  }
  if (submitBtn) submitBtn.style.display = '';
  // Destroy old sortables
  sortableInstances.forEach(s => s.destroy());
  sortableInstances = [];

  const container = $('vote-categories-container');

  // Strategy 2: save boost input values + focused input before wiping DOM
  const savedBoosts = {};
  container.querySelectorAll('input[type="number"][id^="boost-input-"]').forEach(inp => {
    const v = parseInt(inp.value);
    if (!isNaN(v)) savedBoosts[inp.id] = v;
  });
  const focusedId = document.activeElement?.id || null;

  container.innerHTML = '';

  data.categories.forEach(cat => {
    // Init rank state from server if not locally set
    if (!voteRankState[cat.id]) {
      if (cat.user_rankings && cat.user_rankings.length) {
        voteRankState[cat.id] = cat.user_rankings.slice().sort((a,b) => a.rank - b.rank);
      } else {
        voteRankState[cat.id] = cat.films.map((f, i) => ({film_id: f.id, rank: i+1}));
      }
    }
    if (voteBoostState[cat.id] === undefined) {
      voteBoostState[cat.id] = cat.user_boost || 0;
    }

    const block = document.createElement('div');
    block.className = 'mb-4 p-3 border rounded';
    block.style.borderColor = 'var(--mg-claret)';

    const title = document.createElement('h6');
    title.className = 'mb-2 fw-bold';
    title.textContent = cat.name;
    block.appendChild(title);

    // Social boost display
    const maxBoost = cat.films.length * 50;
    const boostPct = maxBoost > 0 ? Math.min(100, Math.round(cat.social_boost / maxBoost * 100)) : 0;
    const socialDiv = document.createElement('div');
    socialDiv.className = 'small mb-2';
    socialDiv.style.color = 'var(--mg-rosewood)';
    socialDiv.innerHTML = `💰 ${cat.social_boost} jetons misés par la salle`;
    block.appendChild(socialDiv);

    // Drag-and-drop list
    const list = document.createElement('ul');
    list.className = 'list-group mb-2';
    list.dataset.catId = cat.id;

    const filmMap = Object.fromEntries(cat.films.map(f => [f.id, f.title]));
    const ranked  = voteRankState[cat.id];
    ranked.forEach((rk, idx) => {
      const li = document.createElement('li');
      li.className = 'list-group-item d-flex align-items-center gap-2';
      li.style.background = 'var(--mg-velvet)';
      li.style.color = 'var(--mg-ivory)';
      li.style.borderColor = 'var(--mg-claret)';
      li.style.cursor = 'grab';
      li.dataset.filmId = rk.film_id;

      const rankBadge = document.createElement('span');
      rankBadge.className = 'badge me-1';
      rankBadge.style.background = 'var(--mg-flame)';
      rankBadge.textContent = idx + 1;
      li.appendChild(rankBadge);

      const filmTitle = document.createElement('span');
      filmTitle.className = 'flex-grow-1';
      filmTitle.textContent = filmMap[rk.film_id] || `Film ${rk.film_id}`;
      li.appendChild(filmTitle);

      const ptsBadge = document.createElement('span');
      ptsBadge.className = 'badge';
      ptsBadge.style.background = 'var(--mg-noir-2)';
      ptsBadge.style.color = 'var(--mg-blush)';
      ptsBadge.textContent = estimatePoints(cat.films.length, idx + 1, voteBoostState[cat.id]) + ' pts';
      li.appendChild(ptsBadge);

      list.appendChild(li);
    });
    block.appendChild(list);

    const sortable = Sortable.create(list, {
      animation: 150,
      onEnd: () => {
        const items = list.querySelectorAll('li');
        const newRanks = [];
        items.forEach((li, idx) => {
          const fid = parseInt(li.dataset.filmId);
          newRanks.push({film_id: fid, rank: idx + 1});
          li.querySelector('.badge:first-child').textContent = idx + 1;
          const ptsEl = li.querySelector('.badge:last-child');
          ptsEl.textContent = estimatePoints(cat.films.length, idx + 1, voteBoostState[cat.id]) + ' pts';
        });
        voteRankState[cat.id] = newRanks;
      }
    });
    sortableInstances.push(sortable);

    // Boost selector — quick buttons + free input
    const boostWrap = document.createElement('div');
    boostWrap.className = 'mt-2';

    const boostRow1 = document.createElement('div');
    boostRow1.className = 'd-flex align-items-center gap-2 flex-wrap';
    const boostLabel = document.createElement('span');
    boostLabel.className = 'small';
    boostLabel.style.color = 'var(--mg-rosewood)';
    boostLabel.textContent = 'Boost :';
    boostRow1.appendChild(boostLabel);

    const customInput = document.createElement('input');
    customInput.type = 'number';
    customInput.min = '0';
    customInput.max = '300';
    customInput.step = '1';
    customInput.id = `boost-input-${cat.id}`;
    customInput.className = 'form-control form-control-sm';
    customInput.style.cssText = 'width:80px;background:var(--mg-velvet);color:var(--mg-ivory);border-color:var(--mg-claret)';
    customInput.value = voteBoostState[cat.id] || 0;

    const updateBoost = (val) => {
      voteBoostState[cat.id] = val;
      customInput.value = val;
      customInput.style.borderColor = 'var(--mg-claret)';
      list.querySelectorAll('li').forEach((li, idx) => {
        const ptsEl = li.querySelector('.badge:last-child');
        ptsEl.textContent = estimatePoints(cat.films.length, idx + 1, val) + ' pts';
      });
      boostRow1.querySelectorAll('button[data-boost-val]').forEach(b => {
        const bv = parseInt(b.dataset.boostVal);
        if (bv === val) {
          b.style.background = 'var(--mg-flame)';
          b.style.color = 'var(--mg-ivory)';
        } else {
          b.style.background = 'var(--mg-velvet)';
          b.style.color = 'var(--mg-rosewood)';
        }
      });
    };

    [0, 25, 50, 100].forEach(val => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-sm';
      btn.dataset.boostVal = val;
      btn.style.borderColor = 'var(--mg-claret)';
      btn.textContent = val === 0 ? 'Aucun' : `+${val}`;
      const cur = voteBoostState[cat.id] || 0;
      btn.style.background = (cur === val) ? 'var(--mg-flame)' : 'var(--mg-velvet)';
      btn.style.color      = (cur === val) ? 'var(--mg-ivory)' : 'var(--mg-rosewood)';
      btn.addEventListener('click', () => updateBoost(val));
      boostRow1.appendChild(btn);
    });

    customInput.addEventListener('input', () => {
      const raw = parseInt(customInput.value);
      if (isNaN(raw) || raw < 0 || raw > 300) {
        customInput.style.borderColor = 'var(--mg-ember)';
        return;
      }
      updateBoost(raw);
    });

    boostRow1.appendChild(customInput);
    boostWrap.appendChild(boostRow1);
    block.appendChild(boostWrap);
    container.appendChild(block);
  });

  // Strategy 2: restore saved boost values and re-focus if needed
  Object.entries(savedBoosts).forEach(([id, val]) => {
    const inp = document.getElementById(id);
    if (inp) { inp.value = val; voteBoostState[parseInt(id.replace('boost-input-', ''))] = val; }
  });
  if (focusedId) document.getElementById(focusedId)?.focus();
}

// ── Vote submit ───────────────────────────────────────────────────────────────
$('btn-vote-submit').addEventListener('click', async () => {
  if (!voteStateData || !voteStateData.session) return;
  const feedback = $('vote-feedback');
  feedback.style.display = 'none';
  feedback.className = 'mt-2';

  const cats = voteStateData.categories;
  let errors = [];

  for (const cat of cats) {
    const rankings = voteRankState[cat.id] || [];
    if (rankings.length > 0) {
      const resp = await fetch('/api/vote/rankings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
        body: JSON.stringify({category_id: cat.id, rankings})
      });
      if (!resp.ok) {
        const d = await resp.json();
        errors.push(`${cat.name}: ${d.error || 'Erreur rankings'}`);
      }
    }
    const serverBoost = cat.user_boost || 0;
    const newBoost    = voteBoostState[cat.id] !== undefined ? voteBoostState[cat.id] : 0;
    if (newBoost !== serverBoost) {
      const resp2 = await fetch('/api/vote/boost', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
        body: JSON.stringify({category_id: cat.id, amount: newBoost})
      });
      if (!resp2.ok) {
        const d2 = await resp2.json();
        errors.push(`${cat.name} boost: ${d2.error || 'Erreur boost'}`);
      } else {
        const d2 = await resp2.json();
        balanceEl.textContent = d2.tokens_remaining;
      }
    }
  }

  feedback.style.display = '';
  if (errors.length) {
    feedback.className = 'mt-2 alert alert-danger';
    feedback.textContent = errors.join(' | ');
  } else {
    feedback.className = 'mt-2 alert alert-success';
    feedback.textContent = 'Classement enregistré !';
    await loadVoteState();
  }
});

showOnly(msgWaiting);
if (window.INITIAL_APP_MODE === 'vote') {
  lastKnownMode = 'vote';
  showOnly(votePanel);
  loadVoteState();
} else if (window.INITIAL_APP_MODE === 'palmares') {
  lastKnownMode = 'palmares';
  showOnly(msgPalmares);
}
pollStatus();
pollPlayBets();
