'use strict';

// display.js runs AFTER app.js (milsaware), so wheel & ballTrack globals exist.
// We only drive spinWheel(); the betting board is hidden via CSS.

const statusBadge  = document.getElementById('status-badge');
const winDisplay   = document.getElementById('winning-display');
const qrImg        = document.getElementById('qr-img');
const sessionInfo  = document.getElementById('session-id-display');

let lastSessionId  = null;
let spinTriggered  = false;

const STATUS_LABELS = {
  waiting:  '⏳ En attente…',
  open:     '🟢 Mises ouvertes',
  spinning: '🎡 Bonne chance !',
};

const STATUS_COLORS = {
  waiting:  '#6c757d',
  open:     '#28a745',
  spinning: '#ffc107',
};

async function pollDisplay() {
  try {
    const r = await fetch('/api/session/status');
    const d = await r.json();

    statusBadge.textContent       = STATUS_LABELS[d.status] || d.status;
    statusBadge.style.background  = STATUS_COLORS[d.status] || '#333';
    statusBadge.style.color       = d.status === 'spinning' ? '#000' : '#fff';

    if (d.status === 'spinning' && d.winning_number !== null && !spinTriggered) {
      spinTriggered = true;
      winDisplay.style.display = '';
      winDisplay.textContent   = d.winning_number;
      try { spinWheel(d.winning_number); } catch(e) {}   // milsaware function
    }

    if (d.status === 'waiting') {
      winDisplay.style.display = 'none';
      winDisplay.textContent   = '';
      spinTriggered = false;
    }

    // Refresh QR when session changes
    if (d.session_id && d.session_id !== lastSessionId) {
      lastSessionId = d.session_id;
      qrImg.src = '/api/session/qr?' + Date.now();   // cache-bust per session
    }
    if (d.session_id) {
      sessionInfo.textContent = `Session #${d.session_id}`;
    }

    // Open state: show time remaining
    if (d.status === 'open' && d.time_remaining_seconds > 0) {
      statusBadge.textContent = `🟢 Misez ! ${d.time_remaining_seconds}s restantes`;
    }

  } catch (e) { /* network hiccup, retry next tick */ }

  setTimeout(pollDisplay, 1000);
}

// Start immediately
pollDisplay();
