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
// Shared flag: true while status === 'spinning'. Used by leaderboard renderers
// to keep the last known tops visible instead of overwriting with empty data.
let isSpinning           = false;
let lastLeaderboardCache = null;   // {top_winners, top_losers}

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
  document.querySelectorAll('#bet-panel .bet-chip-more').forEach(el => el.remove());
  lastBetSignature = '';
}

const MAX_CHIPS_PER_CELL = 6;  // max chips displayed before "+N" badge

// Spread positions [x, y] in px from cell center — 1 to 6 chips
// Designed for 30px-tall cells: two rows of 10px chips at ±8px vertical
const CHIP_SPREAD = [
  [[0, 0]],                                                              // 1
  [[-9, 0], [9, 0]],                                                     // 2
  [[-14, 0], [0, 0], [14, 0]],                                          // 3
  [[-9, -8], [9, -8], [-9, 8], [9, 8]],                                 // 4
  [[-14, -8], [0, -8], [14, -8], [-9, 8], [9, 8]],                     // 5
  [[-14, -8], [0, -8], [14, -8], [-14, 8], [0, 8], [14, 8]],           // 6
];

function renderChips(bets) {
  const sig = JSON.stringify(bets);
  if (sig === lastBetSignature) return;   // nothing changed
  lastBetSignature = sig;

  // Remove old chips and overflow badges
  document.querySelectorAll('#bet-panel .bet-chip').forEach(el => el.remove());
  document.querySelectorAll('#bet-panel .bet-chip-more').forEach(el => el.remove());

  // Group bets by cell key
  const cellBets = new Map();  // "type:val" → {cell, entries[]}

  bets.forEach(bet => {
    const cell = document.querySelector(
      `#bet-panel [data-type="${bet.bet_type}"][data-val="${bet.bet_value}"]`
    );
    if (!cell) return;
    const key = `${bet.bet_type}:${bet.bet_value}`;
    if (!cellBets.has(key)) cellBets.set(key, { cell, entries: [] });
    cellBets.get(key).entries.push({
      username: bet.username,
      amount:   bet.amount,
      color:    chipColor(bet.username),
    });
  });

  cellBets.forEach(({ cell, entries }) => {
    const total    = entries.length;
    const visible  = entries.slice(0, MAX_CHIPS_PER_CELL);
    const overflow = total - visible.length;

    // Single chip: 16px; multiple: 10px — anchored to bottom-right corner so number stays visible
    const chipSize = visible.length === 1 ? 16 : 10;
    const cols = 2;

    visible.forEach((e, idx) => {
      const row = Math.floor(idx / cols);
      const col = idx % cols;
      const chip = document.createElement('div');
      chip.className = 'bet-chip';
      chip.style.cssText =
        `width:${chipSize}px;height:${chipSize}px;` +
        `background:${e.color};` +
        `bottom:${2 + row * (chipSize + 3)}px;right:${2 + col * (chipSize + 3)}px;top:auto;left:auto;` +
        `transform:none;z-index:${10 + idx};` +
        `font-size:${chipSize <= 10 ? '5px' : '6px'};`;
      chip.title = `${e.username}: ${e.amount} token${e.amount > 1 ? 's' : ''}`;
      chip.textContent = e.username.charAt(0).toUpperCase();
      cell.appendChild(chip);
    });

    if (overflow > 0) {
      // "+N" badge anchored to top-right corner of the cell
      const badge = document.createElement('div');
      badge.className = 'bet-chip-more';
      badge.style.cssText =
        'position:absolute;top:1px;right:1px;' +
        'width:11px;height:11px;border-radius:50%;' +
        'background:rgba(20,20,40,0.92);border:1.5px solid rgba(255,255,255,.6);' +
        'display:flex;align-items:center;justify-content:center;' +
        'font-size:5px;font-weight:900;color:#fff;' +
        `z-index:30;cursor:default;`;
      badge.textContent = `+${overflow}`;
      badge.title = `+${overflow} autre${overflow > 1 ? 's' : ''} joueur${overflow > 1 ? 's' : ''}`;
      cell.appendChild(badge);
    }
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

let lastPollStatus    = null;
let lastDisplayMode   = null;

const voteOverlay     = document.getElementById('vote-overlay');
const palmaresOverlay = document.getElementById('palmares-overlay');

function setDisplayMode(mode) {
  const showWheel = (mode === 'roulette' || mode === 'closed' || !mode);
  if (voteOverlay)     voteOverlay.classList.toggle('active',     mode === 'vote');
  if (palmaresOverlay) palmaresOverlay.classList.toggle('active', mode === 'palmares');
  const mainWrap = document.getElementById('main-wrap');
  if (mainWrap) mainWrap.style.display = showWheel ? '' : 'none';
  const statusBar = document.getElementById('status-bar');
  if (statusBar) statusBar.style.display = showWheel ? '' : 'none';
  const drawsStrip = document.getElementById('draws-strip');
  if (drawsStrip) drawsStrip.style.display = showWheel ? '' : 'none';
  const qrCorner = document.getElementById('qr-corner');
  if (qrCorner) qrCorner.style.display = (mode === 'palmares') ? 'none' : '';
  const qrLabel = document.getElementById('qr-corner-label');
  if (qrLabel) qrLabel.textContent = (mode === 'vote') ? 'Scannez pour voter' : 'Scannez pour miser';
  if (mode === 'palmares') {
    lastPalmaresSig     = '';
    currentDisplayCatId = null;
  }
  if (mode === 'vote') {
    currentDisplayCatId = null;
    lastVoteStateSig = '';  // force fresh fetch on mode entry
    pollVoteDisplay();
  }
  if (mode !== 'vote') {
    currentDisplayCatId = null;
  }
}

let lastVoteStateSig   = '';
let lastPalmaresSig    = '';
let lastVoteStateData  = null;
let currentDisplayCatId = null;

async function pollVoteDisplay() {
  try {
    const r = await fetch('/api/vote/display-state');
    if (!r.ok) return;
    const data = await r.json();
    const sig  = JSON.stringify(data);
    if (sig === lastVoteStateSig) return;
    lastVoteStateSig  = sig;
    lastVoteStateData = data;
    if (lastDisplayMode === 'vote') {
      renderSingleVoteCategory(lastVoteStateData);
    }
  } catch(e) {}
}

function renderSingleVoteCategory(data) {
  const cont = document.getElementById('vote-categories-display');
  if (!cont) return;
  cont.innerHTML = '';

  const cat = data && data.display_category;
  if (!cat) {
    const msg = document.createElement('div');
    msg.className = 'vote-waiting-msg';
    msg.textContent = 'En attente…';
    cont.appendChild(msg);
    return;
  }

  const block = document.createElement('div');
  block.className = 'vote-category-block vote-category-fadein';

  const title = document.createElement('h3');
  title.textContent = cat.name;
  block.appendChild(title);

  const social = cat.social_boost || 0;
  const pct    = Math.min(100, Math.round(social / 5));

  const boostLabel = document.createElement('div');
  boostLabel.className = 'social-boost-label';
  boostLabel.textContent = `💰 ${social} jetons misés`;
  block.appendChild(boostLabel);

  const barWrap = document.createElement('div');
  barWrap.className = 'social-boost-bar-wrap';
  const bar = document.createElement('div');
  bar.className = 'social-boost-bar';
  bar.style.width = pct + '%';
  barWrap.appendChild(bar);
  block.appendChild(barWrap);

  const voterCount = document.createElement('div');
  voterCount.className = 'vote-voter-count';
  voterCount.textContent = `${cat.voter_count || 0} votant(s)`;
  block.appendChild(voterCount);

  cont.appendChild(block);
}

async function pollPalmaresDisplay() {
  try {
    const r = await fetch('/api/vote/results');
    if (!r.ok) return;
    const data = await r.json();
    const sig  = JSON.stringify(data) + '|' + currentDisplayCatId;
    if (sig === lastPalmaresSig) return;
    lastPalmaresSig = sig;
    renderPalmaresDisplay(data, currentDisplayCatId);
  } catch(e) {}
}

function renderPalmaresDisplay(data, forcedCatId) {
  const cont = document.getElementById('palmares-category-display');
  if (!cont) return;
  const allCats      = data.categories || [];
  const revealedCats = allCats.filter(c => c.revealed);
  if (!revealedCats.length) { cont.innerHTML = ''; return; }

  let cat = forcedCatId ? revealedCats.find(c => c.id === forcedCatId) : null;
  if (!cat) cat = revealedCats[revealedCats.length - 1];

  const progressEl = document.getElementById('palmares-progress');
  if (progressEl) progressEl.textContent = `Catégorie ${revealedCats.length} / ${allCats.length}`;

  cont.innerHTML = '';
  const block = document.createElement('div');
  block.className = 'palmares-category palmares-reveal';

  const title = document.createElement('div');
  title.className = 'palmares-category-title';
  title.textContent = cat.name;
  block.appendChild(title);

  const sep = document.createElement('div');
  sep.className = 'palmares-separator';
  block.appendChild(sep);

  const medals = ['🥇', '🥈', '🥉'];
  (cat.films || []).forEach((f, i) => {
    const row = document.createElement('div');
    row.className = 'palmares-film' + (i === 0 ? ' palmares-film--first' : '');
    const medal = document.createElement('span');
    medal.className = 'palmares-film-medal';
    medal.textContent = medals[i] || `#${f.rank || i + 1}`;
    const filmTitle = document.createElement('span');
    filmTitle.className = 'palmares-film-title';
    filmTitle.textContent = f.title;
    const score = document.createElement('span');
    score.className = 'palmares-film-score';
    score.textContent = (f.score !== null && f.score !== undefined) ? f.score + ' pts' : '';
    row.appendChild(medal);
    row.appendChild(filmTitle);
    row.appendChild(score);
    block.appendChild(row);
  });

  cont.appendChild(block);
}

// ── Main display poll ─────────────────────────────────────────────────────────
async function pollDisplay() {
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

    // Handle app_mode transitions
    const newMode = d.app_mode || 'roulette';
    if (newMode !== lastDisplayMode) {
      lastDisplayMode = newMode;
      setDisplayMode(newMode);
    }

    // Vote mode: update single-category display when admin selects a category
    if (newMode === 'vote') {
      const catId = d.vote_display_category_id || null;
      if (catId !== currentDisplayCatId) {
        currentDisplayCatId = catId;
        pollVoteDisplay();  // force fresh fetch for new category
      }
    }

    // Palmares mode: re-render when admin changes the projected category
    if (newMode === 'palmares') {
      const catId = d.vote_display_category_id || null;
      if (catId !== currentDisplayCatId) {
        currentDisplayCatId = catId;
        await pollPalmaresDisplay();
      }
    }

    // Non-roulette modes: skip roulette UI updates
    if (newMode !== 'roulette') {
      setTimeout(pollDisplay, 2000);
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

    // Clear bet chips when session ends, or once on transition to open (new round)
    if (d.status === 'closed' || d.status === 'waiting' ||
        (d.status === 'open' && lastPollStatus !== 'open')) {
      clearChips();
    }
    // Track spin state so leaderboard renderers can guard their cache
    isSpinning     = (d.status === 'spinning');
    lastPollStatus = d.status;

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
    const nameSpan = document.createElement('span');
    nameSpan.textContent = `${medals[i]} ${p.username}`;
    const netSpan = document.createElement('span');
    netSpan.className = 'rl-pos';
    netSpan.textContent = `+${p.net}`;
    div.appendChild(nameSpan);
    div.appendChild(netSpan);
    winEl.appendChild(div);
    rows.push(div);
  });
  data.losers.forEach((p, i) => {
    const div = document.createElement('div');
    div.className = 'rl-row';
    const nameSpan = document.createElement('span');
    nameSpan.textContent = `${medals[i]} ${p.username}`;
    const netSpan = document.createElement('span');
    netSpan.className = 'rl-neg';
    netSpan.textContent = String(p.net);
    div.appendChild(nameSpan);
    div.appendChild(netSpan);
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

// ── Leaderboard polling (every 15s) ──────────────────────────────────────────
function renderLeaderboard(data) {
  const medals  = ['🥇', '🥈', '🥉', '4.', '5.'];
  const winEl   = document.getElementById('lb-winners-list');
  const loseEl  = document.getElementById('lb-losers-list');
  if (!winEl || !loseEl) return;

  const hasData = data.top_winners.length > 0 || data.top_losers.length > 0;

  // During a spin, never overwrite with empty data — keep the last known tops visible
  if (!hasData && isSpinning && lastLeaderboardCache) return;

  if (hasData) {
    lastLeaderboardCache = Object.assign(lastLeaderboardCache || {}, {
      top_winners: data.top_winners,
      top_losers:  data.top_losers,
    });
  }

  winEl.innerHTML = '';
  if (data.top_winners.length === 0) {
    winEl.innerHTML = '<div class="lb-row lb-empty">Personne encore…</div>';
  } else {
    data.top_winners.forEach((p, i) => {
      const row = document.createElement('div');
      row.className = 'lb-row';
      const nameSpan = document.createElement('span');
      nameSpan.textContent = `${medals[i]} ${p.username}`;
      const netSpan = document.createElement('span');
      netSpan.className = 'lb-net-pos';
      netSpan.textContent = `+${p.net}`;
      row.appendChild(nameSpan);
      row.appendChild(netSpan);
      winEl.appendChild(row);
    });
  }

  loseEl.innerHTML = '';
  if (data.top_losers.length === 0) {
    loseEl.innerHTML = '<div class="lb-row lb-empty">Personne encore…</div>';
  } else {
    data.top_losers.forEach((p, i) => {
      const row = document.createElement('div');
      row.className = 'lb-row';
      const nameSpan = document.createElement('span');
      nameSpan.textContent = `${medals[i]} ${p.username}`;
      const netSpan = document.createElement('span');
      netSpan.className = 'lb-net-neg';
      netSpan.textContent = String(p.net);
      row.appendChild(nameSpan);
      row.appendChild(netSpan);
      loseEl.appendChild(row);
    });
  }
}

async function pollLeaderboard() {
  try {
    const r = await fetch('/api/leaderboard');
    if (r.ok) {
      const data = await r.json();
      renderLeaderboard(data);
    }
  } catch(e) { /* network hiccup */ }
  setTimeout(pollLeaderboard, 15000);
}

// ── Vote / palmares sub-pollers ───────────────────────────────────────────────
async function voteDisplayLoop() {
  if (lastDisplayMode === 'vote') await pollVoteDisplay();
  setTimeout(voteDisplayLoop, 5000);
}

async function palmaresDisplayLoop() {
  if (lastDisplayMode === 'palmares') await pollPalmaresDisplay();
  setTimeout(palmaresDisplayLoop, 5000);
}

// Start all pollers
pollDisplay();
pollBets();
pollHistory();
pollLeaderboard();
voteDisplayLoop();
palmaresDisplayLoop();

// QR code init
(function() {
  if (typeof QRCode === 'undefined') return;
  const opts = { text: 'https://casino.kryptide.fr/login', colorDark: '#1a0507', colorLight: '#f8f6f6' };
  const corner = document.getElementById('qr-corner-canvas');
  if (corner) new QRCode(corner, Object.assign({}, opts, { width: 120, height: 120 }));
  const vote = document.getElementById('qr-vote-canvas');
  if (vote) new QRCode(vote, Object.assign({}, opts, { width: 300, height: 300 }));
})();
