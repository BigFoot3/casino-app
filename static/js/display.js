'use strict';

// display.js runs AFTER app.js (milsaware), so wheel & ballTrack globals exist.
// We only drive spinWheel(); the betting board is hidden via CSS.

// ── Move wheel container into wheel panel (app.js appends #container to body) ─
(function moveWheel() {
  const panel = document.getElementById('wheel-panel');
  const cont  = document.getElementById('container');
  if (panel && cont) panel.insertBefore(cont, panel.firstChild);
})();

const statusBadge    = document.getElementById('status-badge');
const winDisplay     = document.getElementById('winning-display');
const qrImg          = document.getElementById('qr-img');
const lastWinDisplay = document.getElementById('last-win-display');
const lastWinNumber  = document.getElementById('last-win-number');
const historyCircles = document.getElementById('history-circles');

const RED_NUMBERS = new Set([1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]);

let lastSessionId     = null;
let lastWinningNumber = null;   // remembered across sessions
// Guard keyed on session_id — prevents double-spin within one session.
let spunForSessionId  = null;
// Timestamp (ms) when the current wheel animation fully ends (t+10000ms).
// pollHistory defers rendering until this time to avoid spoiling the result.
let spinEndTime = 0;

// ── UI 1 palette ─────────────────────────────────────────────────────────────
const STATUS_LABELS = {
  waiting:  '⏳ En attente…',
  open:     '🟢 Mises ouvertes',
  spinning: '🎡 Bonne chance !',
};
const STATUS_BG = {
  waiting:  '#0d0d1a',   // near-black, white text ✓
  open:     '#2e7d32',   // dark green, white text ✓
  spinning: '#c9a84c',   // gold accent, dark text ✓
};
const STATUS_FG = {
  waiting:  '#f0f0f0',
  open:     '#f0f0f0',
  spinning: '#0d0d1a',
};

// ── UI 2: scale wheel to 70% of viewport height ───────────────────────────────
function scaleWheel() {
  const cont = document.getElementById('container');
  if (!cont) return;
  // Native wheel size is 312×312 px (milsaware default)
  const NATIVE = 312;
  const panel  = document.getElementById('wheel-panel');
  const avail  = panel ? Math.min(panel.clientHeight, panel.clientWidth) : window.innerHeight;
  const target = Math.min(avail * 0.88, window.innerHeight * 0.68);
  const scale  = target / NATIVE;
  cont.style.transform       = `scale(${scale.toFixed(4)})`;
  cont.style.transformOrigin = 'center center';
}
window.addEventListener('resize', scaleWheel);
// Run after DOM settles so panel dimensions are known
setTimeout(scaleWheel, 50);

// ── FEATURE 2: chip color per username ───────────────────────────────────────
function usernameHue(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0x7fffffff;
  return h % 360;
}
function chipColor(name) {
  return `hsl(${usernameHue(name)}, 70%, 52%)`;
}

// ── FEATURE 2: render chips on the live betting grid ─────────────────────────
let lastBetSignature = '';   // JSON string of bets — skip re-render if unchanged

function clearChips() {
  document.querySelectorAll('#bet-panel .bet-chip').forEach(el => el.remove());
  document.getElementById('legend-entries').innerHTML = '';
  lastBetSignature = '';
}

function renderChips(bets) {
  const sig = JSON.stringify(bets);
  if (sig === lastBetSignature) return;   // nothing changed
  lastBetSignature = sig;

  // Remove old chips
  document.querySelectorAll('#bet-panel .bet-chip').forEach(el => el.remove());

  // Track how many chips already placed per cell (for stacking offset)
  const cellCount = new Map();
  const legend    = new Map();  // username → color

  bets.forEach(bet => {
    const color = chipColor(bet.username);
    legend.set(bet.username, color);

    // Find the target cell
    const cell = document.querySelector(
      `#bet-panel [data-type="${bet.bet_type}"][data-val="${bet.bet_value}"]`
    );
    if (!cell) return;

    const key   = `${bet.bet_type}:${bet.bet_value}`;
    const idx   = cellCount.get(key) || 0;
    cellCount.set(key, idx + 1);

    const chip = document.createElement('div');
    chip.className = 'bet-chip';
    chip.style.background = color;
    // Stack chips with 4px offset each
    chip.style.top  = `calc(50% + ${idx * 4}px)`;
    chip.style.left = `calc(50% + ${idx * 4}px)`;
    chip.style.transform = 'none';
    chip.title = `${bet.username}: ${bet.amount} token${bet.amount > 1 ? 's' : ''}`;
    // Show first initial as label
    chip.textContent = bet.username.charAt(0).toUpperCase();
    cell.appendChild(chip);
  });

  // Update legend
  const legendEl = document.getElementById('legend-entries');
  legendEl.innerHTML = '';
  legend.forEach((color, username) => {
    const row = document.createElement('div');
    row.className = 'legend-row';
    row.innerHTML = `<div class="legend-chip" style="background:${color}"></div>
                     <span>${username}</span>`;
    legendEl.appendChild(row);
  });
}

async function pollBets() {
  try {
    const r = await fetch('/api/session/bets');
    if (r.ok) {
      const bets = await r.json();
      if (bets.length > 0) {
        renderChips(bets);
      }
    }
  } catch(e) { /* network hiccup */ }
  setTimeout(pollBets, 2000);
}

// ── App mode helpers ─────────────────────────────────────────────────────────
const voteOverlay     = document.getElementById('vote-overlay');
const palmaresOverlay = document.getElementById('palmares-overlay');
const mainWrap        = document.getElementById('main-wrap');
let lastAppMode       = null;
let palmaresLoaded    = false;

function setDisplayMode(mode) {
  if (mode === lastAppMode) return;
  lastAppMode = mode;

  if (mode === 'vote') {
    mainWrap.style.display        = 'none';
    palmaresOverlay.style.display = 'none';
    voteOverlay.style.display     = 'flex';
  } else if (mode === 'palmares') {
    mainWrap.style.display        = 'none';
    voteOverlay.style.display     = 'none';
    palmaresOverlay.style.display = 'flex';
    if (!palmaresLoaded) {
      palmaresLoaded = true;
      loadPalmares();
    }
  } else {
    // roulette
    voteOverlay.style.display     = 'none';
    palmaresOverlay.style.display = 'none';
    mainWrap.style.display        = '';
  }
}

async function loadPalmares() {
  try {
    const r = await fetch('/api/vote/summary');
    if (!r.ok) return;
    const films = await r.json();
    const medals = ['🥇', '🥈', '🥉'];
    const listEl = document.getElementById('palmares-list');
    listEl.innerHTML = '';
    films.forEach((f, i) => {
      const medal = medals[i] || `${i + 1}.`;
      const div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;gap:16px;padding:14px 20px;' +
        'background:#1a1a2e;border-radius:12px;margin-bottom:12px;' +
        (i === 0 ? 'border:2px solid #ffd700;box-shadow:0 0 18px rgba(255,215,0,.4)' :
         i === 1 ? 'border:2px solid #c0c0c0' :
         i === 2 ? 'border:2px solid #cd7f32' : 'border:1px solid #2a2a4a');
      div.innerHTML = `<span style="font-size:2rem">${medal}</span>
        <span style="flex:1;font-size:1.25rem;font-weight:bold;color:#f0f0f0">${f.film_title}</span>
        <span style="font-size:1.8rem;font-weight:900;color:#c9a84c">${f.avg_weighted_score}</span>
        <span style="color:#9e9e9e;font-size:.85rem">/10 · ${f.voter_count} vote${f.voter_count !== 1 ? 's' : ''}</span>`;
      listEl.appendChild(div);
    });
  } catch(e) { /* network hiccup */ }
}

// ── Main display poll ─────────────────────────────────────────────────────────
async function pollDisplay() {
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

    const appMode = d.app_mode || 'roulette';
    setDisplayMode(appMode);

    if (appMode === 'vote') {
      if (d.vote_session) {
        document.getElementById('display-film-title').textContent  = d.vote_session.film_title;
        document.getElementById('display-voter-count').textContent = d.vote_session.voter_count;
      }
      setTimeout(pollDisplay, 3000);
      return;
    }

    if (appMode === 'palmares') {
      setTimeout(pollDisplay, 5000);
      return;
    }

    // ── Roulette mode ─────────────────────────────────────────────────────────

    // UI 1: apply palette
    const label = STATUS_LABELS[d.status] || d.status;
    statusBadge.style.background = STATUS_BG[d.status] || '#0d0d1a';
    statusBadge.style.color      = STATUS_FG[d.status] || '#f0f0f0';

    // UI 3: timer text goes in status-bar badge (never overlapping wheel)
    if (d.status === 'open' && d.time_remaining_seconds > 0) {
      statusBadge.textContent = `🟢 Misez ! ${d.time_remaining_seconds}s`;
    } else {
      statusBadge.textContent = label;
    }

    // Fire spinWheel exactly once per unique session_id
    if (d.status === 'spinning' && d.winning_number !== null && d.session_id !== spunForSessionId) {
      spunForSessionId  = d.session_id;
      spinEndTime       = Date.now() + 10000;  // animation ends at t+10000ms (app.js wheel stop)
      lastWinningNumber = d.winning_number;    // remember for next open phase
      // Hide "last win" banner while wheel is spinning
      lastWinDisplay.style.display = 'none';
      // At t+9000ms: reveal winning number + fetch round leaderboard
      // (ball visually frozen at t=9000ms — app.js line 629)
      const _winNum        = d.winning_number;
      const _rlSessionId   = d.session_id;
      setTimeout(async () => {
        winDisplay.style.display = 'block';
        winDisplay.textContent   = _winNum === 0 ? 67 : _winNum;
        // Fetch and display the per-round leaderboard
        try {
          const rr = await fetch('/api/session/round_result');
          if (rr.ok) {
            const data = await rr.json();
            if (data.session_id === _rlSessionId) showRoundLeaderboard(data);
          }
        } catch(e) { /* network hiccup */ }
      }, 9000);
      console.log(`spinWheel called session=${d.session_id} winning=${d.winning_number}`);
      try { spinWheel(d.winning_number); } catch(e) { console.error('spinWheel error', e); }
    }

    // Clear overlay when not spinning; show last win banner during open phase
    if (d.status === 'waiting' || d.status === 'open') {
      winDisplay.style.display = 'none';
      winDisplay.textContent   = '';
    }
    if (d.status === 'open' && lastWinningNumber !== null) {
      lastWinNumber.textContent  = lastWinningNumber === 0 ? 67 : lastWinningNumber;
      lastWinDisplay.style.display = '';
    } else if (d.status !== 'open') {
      lastWinDisplay.style.display = 'none';
    }

    // Clear bet chips only when session is fully over — not during spinning
    if (d.status === 'closed' || d.status === 'waiting') {
      clearChips();
    }

    // Refresh QR when session changes
    if (d.session_id && d.session_id !== lastSessionId) {
      lastSessionId = d.session_id;
      qrImg.src = '/api/session/qr?' + Date.now();
    }

  } catch(e) { /* network hiccup */ }

  setTimeout(pollDisplay, 1000);
}

// ── Draw history polling ──────────────────────────────────────────────────────
function renderHistory(draws) {
  historyCircles.innerHTML = '';
  draws.forEach(draw => {
    const n = draw.winning_number;
    const el = document.createElement('div');
    el.className = 'history-circle ' + (n === 0 ? 'zero-num' : RED_NUMBERS.has(n) ? 'red-num' : 'black-num');
    el.textContent = n === 0 ? 67 : n;
    el.title = `Session #${draw.session_id}`;
    historyCircles.appendChild(el);
  });
}

async function pollHistory() {
  try {
    const r = await fetch('/api/history');
    if (r.ok) {
      const draws = await r.json();
      const now   = Date.now();
      if (now >= spinEndTime) {
        renderHistory(draws);
      } else {
        // Wheel animation still running — defer render until it ends (+200ms margin)
        const delay = spinEndTime - now + 200;
        setTimeout(async () => {
          try {
            const r2 = await fetch('/api/history');
            if (r2.ok) renderHistory(await r2.json());
          } catch(e) { /* network hiccup */ }
        }, delay);
      }
    }
  } catch(e) { /* network hiccup */ }
  setTimeout(pollHistory, 5000);
}

// ── Round leaderboard overlay (shown once after each spin) ───────────────────
function showRoundLeaderboard(data) {
  const overlay  = document.getElementById('round-leaderboard');
  const winSect  = document.getElementById('rl-winners-section');
  const loseSect = document.getElementById('rl-losers-section');
  const winEl    = document.getElementById('rl-winners');
  const loseEl   = document.getElementById('rl-losers');
  if (!overlay || !winEl || !loseEl) return;

  const medals = ['🥇', '🥈', '🥉'];
  winEl.innerHTML  = '';
  loseEl.innerHTML = '';
  const rows = [];

  data.winners.forEach((p, i) => {
    const div = document.createElement('div');
    div.className = 'rl-row';
    div.innerHTML = `<span>${medals[i]} ${p.username}</span><span class="rl-pos">+${p.net}</span>`;
    winEl.appendChild(div);
    rows.push(div);
  });
  data.losers.forEach((p, i) => {
    const div = document.createElement('div');
    div.className = 'rl-row';
    div.innerHTML = `<span>${medals[i]} ${p.username}</span><span class="rl-neg">${p.net}</span>`;
    loseEl.appendChild(div);
    rows.push(div);
  });

  if (rows.length === 0) return;  // no bets this round — nothing to show

  winSect.style.display  = data.winners.length ? '' : 'none';
  loseSect.style.display = data.losers.length  ? '' : 'none';

  // Reset animation classes then show overlay
  rows.forEach(r => { r.classList.remove('rl-popin'); r.style.animationDelay = ''; });
  overlay.classList.remove('rl-fadeout');
  overlay.style.display = 'flex';

  // Trigger pop-in sequentially (force reflow so removing/adding class works)
  void overlay.offsetWidth;
  rows.forEach((row, i) => {
    row.style.animationDelay = `${i * 200}ms`;
    row.classList.add('rl-popin');
  });

  // Auto-hide: fade out after 5s, then hide after fade (600ms)
  setTimeout(() => {
    overlay.classList.add('rl-fadeout');
    setTimeout(() => {
      overlay.style.display = 'none';
    }, 600);
  }, 5000);
}

// ── Top holders panel ─────────────────────────────────────────────────────────
function renderTopHolders(holders) {
  const el = document.getElementById('top-holders-list');
  if (!el) return;
  if (!holders || holders.length === 0) {
    el.innerHTML = '<div class="lb-row lb-empty">Aucun joueur…</div>';
    return;
  }
  el.innerHTML = holders.map(h =>
    `<div class="holder-row">
       <span class="holder-rank rank-${h.rank}">${h.rank}</span>
       <span class="holder-name">${h.username}</span>
       <span class="holder-tokens">${h.tokens}</span>
     </div>`
  ).join('');
}

// ── Leaderboard polling (every 15s) ──────────────────────────────────────────
function renderLeaderboard(data) {
  const medals  = ['🥇', '🥈', '🥉'];
  const winEl   = document.getElementById('lb-winners-list');
  const loseEl  = document.getElementById('lb-losers-list');
  if (!winEl || !loseEl) return;

  winEl.innerHTML = data.top_winners.length === 0
    ? '<div class="lb-row lb-empty">Personne encore…</div>'
    : data.top_winners.map((p, i) =>
        `<div class="lb-row"><span>${medals[i]} ${p.username}</span><span class="lb-net-pos">+${p.net}</span></div>`
      ).join('');

  loseEl.innerHTML = data.top_losers.length === 0
    ? '<div class="lb-row lb-empty">Personne encore…</div>'
    : data.top_losers.map((p, i) =>
        `<div class="lb-row"><span>${medals[i]} ${p.username}</span><span class="lb-net-neg">${p.net}</span></div>`
      ).join('');
}

async function pollLeaderboard() {
  try {
    const r = await fetch('/api/leaderboard');
    if (r.ok) {
      const data = await r.json();
      renderLeaderboard(data);
      renderTopHolders(data.top_holders || []);
    }
  } catch(e) { /* network hiccup */ }
  setTimeout(pollLeaderboard, 15000);
}

// Start all pollers
pollDisplay();
pollBets();
pollHistory();
pollLeaderboard();
