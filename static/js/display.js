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
const sessionInfo    = document.getElementById('session-id-display');
const lastWinDisplay = document.getElementById('last-win-display');
const lastWinNumber  = document.getElementById('last-win-number');
const historyCircles = document.getElementById('history-circles');

const RED_NUMBERS = new Set([1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]);

let lastSessionId    = null;
let lastWinningNumber = null;  // remembered across sessions
// BUG 1 fix: guard keyed on session_id, not a boolean flag.
// Prevents double-spin within one session; resets automatically when session_id changes.
// Does NOT rely on status==='waiting' which may be skipped in fast auto-mode cycles.
let spunForSessionId = null;

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
  const target = avail * 0.88;   // 88% of the panel dimension (≈70% viewport when panel ~80% tall)
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

// ── Main display poll ─────────────────────────────────────────────────────────
async function pollDisplay() {
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

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

    // BUG 1 fix: fire spinWheel exactly once per unique session_id
    if (d.status === 'spinning' && d.winning_number !== null && d.session_id !== spunForSessionId) {
      spunForSessionId = d.session_id;
      lastWinningNumber = d.winning_number;  // remember for next open phase
      // Hide "last win" banner while wheel is spinning
      lastWinDisplay.style.display = 'none';
      // Delay winning number reveal by 9000ms — ball is visually frozen at t=9000ms
      // (app.js:629: ballTrack.style.cssText sets static transform at t=9000ms)
      const _winNum = d.winning_number;
      setTimeout(() => {
        winDisplay.style.display = 'block';
        winDisplay.textContent   = _winNum;
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
      lastWinNumber.textContent  = lastWinningNumber;
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
    if (d.session_id) {
      sessionInfo.textContent = `Session #${d.session_id}`;
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
    el.textContent = n;
    el.title = `Session #${draw.session_id}`;
    historyCircles.appendChild(el);
  });
}

async function pollHistory() {
  try {
    const r = await fetch('/api/history');
    if (r.ok) renderHistory(await r.json());
  } catch(e) { /* network hiccup */ }
  setTimeout(pollHistory, 5000);
}

// Start all pollers
pollDisplay();
pollBets();
pollHistory();
