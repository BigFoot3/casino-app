# Casino

Online roulette app for live events — up to 100 concurrent players.

---

## Infrastructure & Stack

| Component | Details |
|-----------|---------|
| VPS | Hetzner CX23, Nuremberg — 178.104.41.200 |
| OS | Ubuntu 24.04, Python 3.12 |
| URL | `https://casino.kryptide.fr` (nginx → Gunicorn:5000) |
| Service | `systemctl {status,restart,stop} casino` / `journalctl -u casino -f` |

**Stack:** Flask 3.1.3 (factory) · Gunicorn 23.0.0 (1 worker, 12 gthread, preload_app=True) · APScheduler 3.10.4 (game_tick every 5s) · SQLite WAL (busy_timeout=10s) · Flask-WTF (CSRF) · Flask-Limiter (10 req/min/IP on `/login`) · bcrypt · qrcode/Pillow

**Env vars:**
- `CASINO_SECRET_KEY` — **required** (RuntimeError if missing)
- `FLASK_ENV` — `development` or `production` (prod requires cookie Secure)
- `CASINO_BASE_URL` — for QR codes (defaults to request.host_url if absent)
- `RATELIMIT_ENABLED` — set to `false` for Locust load tests (read by extensions.py)

---

## Design System — Midnight Gala

**Palette (static/css/midnight-gala.css):** All app CSS centralized here. Never add inline styles to templates or modify colors outside the token list below.

| Token | Color | Usage |
|-------|-------|-------|
| `--mg-flame` | `#cc2819` | Primary accent — borders, strips, buttons |
| `--mg-ember` | `#ec2415` | Loss/danger signal |
| `--mg-velvet` | `#4d0f12` | Deep secondary surface |
| `--mg-oxblood` | `#6d1613` | Intermediate accent |
| `--mg-claret` | `#901e16` | Strong borders |
| `--mg-rosewood` | `#a35e57` | Secondary text / muted labels |
| `--mg-blush` | `#f0afa7` | Gain/positive highlights |
| `--mg-ivory` | `#f8f6f6` | Primary text |
| `--mg-noir` | `#1a0507` | Body background |
| `--mg-noir-2` | `#0e0405` | Card/sidebar surfaces |

**Components (CSS classes):**
`.mg-page-head` (eyebrow + h1) · `.mg-page-title` / `.mg-page-title--login` · `.mg-kpi` / `.mg-kpi__label` / `.mg-kpi__value` · `.mg-brand-logo` · `.login-wrap` / `.login-card` · `.token-badge` / `.token-count` · `.mg-chip` (variants: `--black`, `--zero`, `--latest`) · `.mg-strip` / `.mg-strip__label` · `.mg-display-logo` (position: absolute in #main-wrap)

**Key template changes (sessions 4–7):**
- `base.html`: Navbar with logo1.png (32px) + admin btn as `btn-primary`
- `login.html`: `.login-wrap`, `.login-card`, eyebrow, logo (64px)
- `dashboard.html`: `.mg-page-head`, `.mg-kpi` for tokens
- `admin/index.html`: `.mg-page-head`, no `table-dark` (conflicts with midnight-gala overrides)
- `roulette/display.html`: Inline `<style>` block (standalone template, not extending base.html), felt-green radial background, Neue Machina OTF
- `static/roulette/assets/css/style.css`: Neue Machina Ultrabold (font-weight: 800), `.double` rotated 3deg with left: 147px

**Font:** Neue Machina Ultrabold (self-hosted OTF) — declared in `midnight-gala.css` AND standalone in display.html `<style>` block.

---

## Project Structure

```
app.py                 # Factory: create_app()
db.py                  # SQLite schema, resolve_spin(), startup_check()
scheduler.py           # APScheduler: game_tick() every 5s (Gunicorn master only)
cli.py                 # CLI: flask create-user <nom> <role>
extensions.py          # Flask-Limiter (shared)
gunicorn.conf.py       # 1 worker, 12 gthread, preload_app=True, logs/
casino.service         # Systemd unit
routes/
  auth.py              # /login, /logout (redirects to /play)
  player.py            # /dashboard, /play, /rewards, /roulette/display
                       # /rewards returns claimed_ids (already-claimed reward IDs)
  admin.py             # /admin
  api.py               # /api/session/*, /api/bet, /api/admin/*, /api/vote/*
templates/             # Jinja2: base.html, login.html, dashboard.html, play.html, admin/index.html
static/
  css/midnight-gala.css           # All app styles
  fonts/NeueMachina-Ultrabold.otf # Self-hosted OTF
  img/logo1.png (206×205), logo2.png (209×205), feuille.png (697×354)
  js/play.js                      # Polling → bet form → result display (gridLocked, resultFetching flag)
     admin.js                     # Session controls, user/film/reward CRUD, vote tracking
     display.js                   # spinWheel() polling, leaderboard cache, public vote display
  roulette/                       # milsaware/javascript-roulette (nested, no .gitmodules, local CSS mods committed)
logs/, casino.db, tests/, .github/workflows/tests.yml
```

---

## Routes (42 endpoints)

**Player routes (auth.py, player.py):**
`GET /login` · `POST /login` · `GET /logout` · `GET /dashboard` · `GET /play` · `GET /rewards` · `GET /roulette/display`

**Session API (api.py):**
`GET /api/leaderboard` (top 5 winners/losers by net P&L) · `GET /api/session/status` (current state + app_mode + vote_session + tokens) · `GET /api/session/round_result` (winners/losers aggregated per player, GROUP BY u.id, u.username, SUM(payout-amount)) · `GET /api/session/result` · `GET /api/session/bets` · `GET /api/session/qr` · `GET /api/history` · `POST /api/bet`

**Vote API (api.py):**
`GET /api/vote/state` (projected category only, or [] if none) · `POST /api/vote/rankings` · `POST /api/vote/boost` (MAX_BOOST=300 per category per user per session; 400 response if exceeded) · `GET /api/vote/display-state` (**public, no auth**; used by display.html) · `GET /api/admin/vote/tracking` (matrix: voters × films × ranks × boosts per category)

**Admin session API (api.py):**
`POST /api/admin/session/open` · `POST /api/admin/session/spin` · `POST /api/admin/session/close` · `POST /api/admin/stats/reset`

**Admin mode API (api.py):**
`POST /api/admin/mode` (toggle manual/auto)

**Admin user API (api.py):**
`POST /api/admin/users/create` · `POST /api/admin/users/<id>/delete` · `POST /api/admin/users/<id>/set-role` (super-admin only, protected from username='admin') · `POST /api/admin/users/<uid>/zero-tokens` · `POST /api/admin/users/<uid>/decrement-tokens` · `POST /api/admin/users/<uid>/reset-password`

**Admin vote API (api.py):**
`GET /api/admin/vote/catalogue` · `POST /api/admin/vote/categories` · `POST /api/admin/vote/categories/<cid>/delete` · `POST /api/admin/vote/films` · `POST /api/admin/vote/films/<fid>/delete` (protected if status='open') · `POST /api/admin/vote/open` · `POST /api/admin/vote/close` · `POST /api/admin/vote/display-category` · `POST /api/admin/vote/palmares` · `POST /api/admin/vote/reset-mode` · `GET /api/admin/vote/sessions` · `GET /api/vote/results`

---

## Database Schema (SQLite)

```sql
users          (id, username, password_hash, role, tokens, created_at)
               role: 'admin' | 'player' — super-admin = username=='admin' (no DB column)
game_sessions  (id, status, mode, auto_interval_seconds, winning_number, opened_at, closed_at, created_at)
               status: waiting → open (30s) → spinning → closed → waiting
bets           (id, session_id, user_id, bet_type, bet_value, amount, payout)
app_config     (key, value)
               Keys: auto_mode_ui, auto_mode_enabled, auto_interval_seconds, app_mode,
                     current_vote_session_id, vote_revealed_categories (JSON []),
                     vote_display_category_id, stats_reset_at (isoformat for lexicographic comparison)
vote_sessions  (id, status, opened_at, closed_at, created_at)
               status: waiting → open → closed → palmares
vote_boosts    (id, session_id, user_id, category_id, amount, updated_at) UNIQUE(session_id, user_id, category_id)
vote_categories (id, name, display_order, created_at)
vote_films     (id, category_id, title, created_at) UNIQUE(category_id, title)
vote_rankings  (id, session_id, user_id, film_id, rank, points, updated_at) UNIQUE(session_id, user_id, film_id)
               points = max(1, round(base × (0.55 ^ (rank-1)) × (1 + boost%)))
               base = max(10, n × 2.5) where n = num films in category
```

---

## Session Lifecycle

**Roulette:** waiting → open (30s bet window) → spinning → closed → waiting

**Vote:** waiting → open → closed → palmares → back to roulette via reset-mode

**Manual mode:** Admin clicks ▶ Open, then 🎯 Spin.

**Auto mode:** Toggle ⚡ Auto sets `auto_mode_ui='1'`. Scheduler activates `auto_mode_enabled='1'` **only on the first click** of ▶ Open. Subsequent sessions chain automatically (5s after close). Btn Fermer ↯ disables `auto_mode_enabled` but preserves `auto_mode_ui` (toggle stays ⚡ Auto; next ▶ Open restarts chaining).

**Payouts:** color/parity/half ×2 · number ×36 · column/dozen ×3. Chip denominations: 1, 5, 10, 50, 67, 100. Columns: `column=1` → n%3==0 (3,6,…,36); `column=2` → n%3==2 (2,5,…,35); `column=3` → n%3==1 (1,4,…,34).

**Roulette grid layout:**
```
[ 1-18 ]  [ 19-36 ]
[ 0 ][ 3 ][ 6 ]…[ 36 ][ 2→1 ]
     [ 2 ][ 5 ]…[ 35 ][ 2→1 ]
     [ 1 ][ 4 ]…[ 34 ][ 2→1 ]
[ 1–12 ][ 13–24 ][ 25–36 ]
[ PAIR ][ RED ][ BLACK ][ ODD ]
```

**app_mode:** `'roulette'` (default) · `'vote'` (voting phase) · `'closed'` (transient, before palmares or reset) · `'palmares'` (display results).

---

## Commands & Operations

**Create user:**
```bash
flask --app "app:create_app()" create-user <name> <role>  # role: player | admin
```

**Service management:**
```bash
systemctl {status,restart,stop} casino
journalctl -u casino -f              # tail logs
journalctl -u casino -n 50 --no-pager
```

**Local dev:**
```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set CASINO_SECRET_KEY
flask --app "app:create_app()" run
```

**Tests:**
```bash
cd /root/casino && source venv/bin/activate
pytest tests/ -v --tb=short                  # 65 tests (64 passed + 1 xfail)
pytest tests/ --cov=. --cov-report=term-missing
```
Suite: conftest.py (fixtures: app, client, admin_client, player_client, player2_client, open_session) · test_casino.py (12 test classes)
xfail: test_no_double_bet_same_session (multi-bet allowed by frontend).

**Load testing (Locust):**
```bash
export RATELIMIT_ENABLED=false  # disable rate limiter for load tests
python tests/setup_load_test.py  # create 100 test accounts (once)

# Run scenarios:
locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
  --users=100 --spawn-rate=10 --run-time=3m --headless --class-picker CasinoPlayer
# Other classes: BetStorm (spike bets), PollingOnly (baseline), VotePolling (public /api/vote/display-state), VotePlayer (rankings + boost)

# Web UI (localhost:8089):
locust -f tests/locustfile.py --host=http://127.0.0.1:5000
```

**CI:** GitHub Actions (tests.yml) — pytest + Codecov on push/PR to main. Coverage: 77% (excludes scheduler.py, locustfile.py, setup_load_test.py). Report: https://app.codecov.io/gh/BigFoot3/casino-app

---

## Critical Invariants

- All token operations use `BEGIN IMMEDIATE` with balance verification — no double-spend.
- CSRF enabled on all POST routes. JSON API calls send `X-CSRFToken` header.
- `resolve_spin()` is idempotent — safe to retry after restart.
- Password modal auto-deletes DOM after 30s — never stored client-side.
- Sessions in `open` or `spinning` at startup auto-recovered by `startup_check()`.
- `CASINO_SECRET_KEY` required — RuntimeError if missing. conftest.py injects 'test-secret-for-pytest'.
- `ProxyFix(x_for=1, x_proto=1, x_host=1)` on app.wsgi_app — Flask-Limiter needs real IP behind nginx.
- JS API data rendered via `textContent` only — never `innerHTML` with untrusted data.
- `/api/vote/display-state` is **public (no auth)** — used by display.html. DO NOT replace with /api/vote/state (protected).
- `stats_reset_at` stored as isoformat (e.g., '2026-05-13T14:15:14+00:00') for lexicographic comparison with closed_at — never strftime().

---

## Traps & Anti-Patterns

**CSS / Design:**
- ⚠️ **midnight-gala.css** — all app CSS centralized. Never add inline styles to templates.
- ⚠️ **display.html CSS** — lives in inline `<style>` block (standalone template). Do NOT move to midnight-gala.css.
- ⚠️ **DA colors** — never use colors outside the token palette (#7DE0A8, #FFB4AB, #72727F, etc.). Use only --mg-* tokens.
- ⚠️ **table-dark** — Bootstrap conflicts with midnight-gala overrides. Never use in admin templates.
- ⚠️ **mg-display-logo** — position: absolute in #main-wrap (which has display:contents). Logo positioned relative to viewport, not parent. Auto-hides when JS sets mainWrap.style.display='none' (vote/palmares).
- ⚠️ **#right-panel display.html** — flex column only. Never use grid multi-column (grid-template-columns: 1fr 1fr puts leaderboards side-by-side instead of stacked).
- ⚠️ **live-half/dozens/outside width** — calc(100% - 86px) = 46px (zero) + 40px (colBtn 38px + gap 2px). Do NOT use calc(100% - 46px) → overflow.
- ⚠️ **roulette lib CSS** — nested git repo (milsaware/javascript-roulette), no .gitmodules. Local CSS mods committed locally, cannot push upstream.
- ⚠️ **roulette centering** — after session 7: ballTrack 212×212 (left/top 49), pocketsRim (left/top 37.5), cone (left/top 65, gradient circle at 90px 90px), turret (left/top 132), turretHandle (left 111). Do NOT revert.
- ⚠️ **roulette turret** — feuille.png (RGBA 697×354) replaces gold gradient. Turret 100×100px (top:106px, left:106px, centered at 156px). Filter: drop-shadow #cc2819/#ec2415 + brightness(1.15) + animation leaf-pulse 3s infinite. turretHandle + thendOne/thendTwo hidden (display:none). Do NOT restore gold gradient or handle.
- ⚠️ **roulette fonts** — Neue Machina Ultrabold (font-weight: 800). .double: transform: rotate(3deg) scaleX(0.75) + left: 147px (compensation for Neue Machina width vs Arial). .single: left: 152px, font-size: 14px (shared).
- ⚠️ **Neue Machina @font-face** — declared in midnight-gala.css (app pages) AND inline `<style>` in display.html (standalone). font-weight: 100–900 mapped to single Ultrabold file.
- ⚠️ **favicon** — base.html and display.html use logo1.png (replaces old SVG data URI).

**Database & Transactions:**
- ⚠️ **isolation_level=None** — manual transactions required. BEGIN IMMEDIATE must be used for token ops.
- ⚠️ **SQLite WAL** — busy_timeout=10s. Connections should not stay open long.
- ⚠️ **resolve_spin() idempotent** — status != 'spinning' → silent ROLLBACK.
- ⚠️ **preload_app=True** — forking: objects initialized before fork are shared. Avoid global DB connections.
- ⚠️ **stats_reset_at** — isoformat for lexicographic correctness vs closed_at.
- ⚠️ **round_result GROUP BY** — api.py session_round_result() MUST group by u.id, u.username with SUM(payout-amount). Without GROUP BY, multi-bet players appear N times (one per bet) → popup displays duplicate lines.

**Frontend JS / Play & Display:**
- ⚠️ **resultFetching flag** — play.js guard against pollResult() race condition. Lock at entry; clear on 404, session mismatch, catch, session reset (open/waiting). Remains true on success path (popup shown). Do NOT remove or move resultShown=true before awaits without reconsidering.
- ⚠️ **balanceDelta vs netDelta** — pollResult(): balanceDelta = sum(payout) for balance update (amount already deducted at bet); netDelta = sum(payout-amount) for result display (net profit/loss). Do NOT confuse.
- ⚠️ **gridLocked vs betPlaced** — gridLocked: UI lock (grid unclickable between submit and spin). betPlaced: routes to pollResult(). Independent. gridLocked=false when spinning starts; betPlaced remains true until result.
- ⚠️ **auto-reload mode** — play.js lastKnownMode detects app_mode transitions (roulette↔vote↔palmares) → window.location.reload() on next pollStatus() (max 2s). First call silent (lastKnownMode=null → init). Do NOT remove showOnly() fallback.
- ⚠️ **leaderboard cache** — display.js caches lastLeaderboardCache {top_winners, top_losers}. During isSpinning, empty payload does NOT clear tops (intended). top_holders removed (session 8, 2026-05-25).
- ⚠️ **claimed_ids** — /rewards returns IDs of already-claimed rewards (used client-side to disable buttons).
- ⚠️ **column/dozen/half display** — colBtn/ldoz/lhalf must have data-type and data-val attributes. Without them, renderChips() silently ignores these bets.
- ⚠️ **play.js tokens polling** — pollStatus() updates balanceEl from data.tokens (every 2s). /api/session/status includes 'tokens' for authenticated Flask sessions (None for unauthenticated callers — display page unaffected).

**Admin JS:**
- ⚠️ **admin.js className** — pollAdmin() (every 3s) completely rewrites button className. Jinja initial CSS class is cosmetic only. Only ID selectors (btn-action, btn-close, etc.) are stable.
- ⚠️ **btn-action** — single contextual button: waiting→▶ Open, open→🎯 Spin, spinning→⏳ Spinning… Routes per currentStatus. Do NOT restore 3 separate buttons.
- ⚠️ **btn-close** — visible only when status='open' (Fermer ↯). Emergency destructive action.
- ⚠️ **admin buttons state** — piloted exclusively by pollAdmin() (3s) via updateControlsState(). Never add local JS variables for button state. currentStatus updated only by updateControlsState().
- ⚠️ **auto_mode_ui vs auto_mode_enabled** — two separate app_config keys: auto_mode_ui = toggle intent (returned by session_status() as `mode`); auto_mode_enabled = scheduler read flag. auto_mode_enabled='1' only on admin_open_session() when auto_mode_ui='1'. admin_close_session() resets auto_mode_enabled='0', preserves auto_mode_ui. admin_set_mode(manual) resets both to '0'.
- ⚠️ **mode toggle** — btn-mode-manual/auto write auto_mode_ui via applyMode(). interval-wrap always visible (auto_interval_seconds controls bet window in both modes). btn-interval-apply (OK) validates without blur; Enter/blur also work.
- ⚠️ **vote-group d-flex** — vote-group-roulette and vote-group-closed must NOT have .d-flex (Bootstrap .d-flex { display: flex !important } overrides inline style="display:none"). d-flex goes on inner div. showVoteGroup() uses el.style.display = 'none' / '' on container → works without d-flex.
- ⚠️ **admin.js vote section** — exclusive HTML groups: vote-group-roulette / vote-group-vote / vote-group-closed / vote-group-palmares. showVoteGroup(mode) shows one, hides others. Never pilot display individually. Listeners attached once at boot (not in updateVoteStatus). Event delegation on catBtnsWrap for category buttons (innerHTML OK here: server-verified content).
- ⚠️ **voteAction finally** — voteAction(btn, url, body) restores btn.disabled=false and opacity=1 in finally block. Always use for vote actions. Never bare fetch() without restoration on error.
- ⚠️ **trackingInterval** — admin.js setInterval 3s for loadVoteTracking(). Managed in updateVoteStatus(): active if mode=vote, cleared otherwise. Do NOT create another interval for vote tracking.
- ⚠️ **#vote-tracking-section** — display:none by default. Shown only when app_mode='vote' (controlled by updateVoteStatus). Do NOT toggle visibility elsewhere.
- ⚠️ **admin mobile** — @media (max-width:768px): #users-table as flex cards, column 4 (Add Tokens) hidden, column 3 (Tokens) visible (padding:4px 0), thead hidden (labels via ::before), data-label="Player/Role/Tokens/Actions" on td 1/2/3/5. Buttons +150/+350 in column 5, NOT in column 4 (hidden). Double-tap confirmation on touch (window.matchMedia('hover:none')), timer 3s via quickBtnPendingMap. Desktop: immediate action, never alert().
- ⚠️ **admin films** — deletion protected if status='open'. Rename protected similarly.
- ⚠️ **admin users** — collapsible list (collapse show by default) + live filter + scroll 5 rows.
- ⚠️ **admin rewards** — deletion cascades reward_claims via /api/admin/rewards/<id>/delete.
- ⚠️ **vote tracking closed** — #vote-tracking-section visible when app_mode='closed' (admin can review results before palmares/reset-mode decision).

**Roulette & Session State:**
- ⚠️ **APScheduler** — game_tick() runs only in Gunicorn master (preload_app=True). Do NOT run in `flask run`.
- ⚠️ **grace period spinning** — /api/session/status simulates 'spinning' for 12s after normal close (for display page). Bypassed if winning_number IS NULL (admin force-close). Do NOT remove condition `prev['winning_number'] is not None`.
- ⚠️ **redirect /play** — routes/auth.py redirects to /play after login (not /dashboard).
- ⚠️ **SESSION_COOKIE_SECURE** — True in prod, False in dev HTTP (else cookie never sent).
- ⚠️ **winning_number** — 0 = House win (no payout for red/black, even/odd, column, dozen, half).
- ⚠️ **btn-interval-apply** — OK button to apply interval change without blur. Works alongside Enter/blur.

**Vote System:**
- ⚠️ **vote delta_tokens** — bonus_delta = old_bonus - new_bonus (positive = refund, negative = deduction).
- ⚠️ **vote points formula** — base = max(10, n × 2.5); raw = base × (0.55 ^ (rank-1)); points = max(1, round(raw × (1 + boost%))).
- ⚠️ **vote boost MAX_BOOST** — 300 per category per user per session (400 response if exceeded). Total can exceed 300 across categories.
- ⚠️ **vote UPSERT** — UNIQUE(vote_session_id, user_id) — one ranking per user per session, modifiable.
- ⚠️ **app_mode 'closed'** — transient state before palmares or reset-mode. Full cycle: roulette → vote → closed → palmares → roulette. Never treat closed as terminal.
- ⚠️ **app_mode storage** — in app_config. Roulette default. Reset via /api/vote/reset-mode.
- ⚠️ **vote_boosts stub** — minimal table in db.py init_db(). Net P&L leaderboard = SUM(payout-amount) closed sessions − SUM(vote_boosts.amount).
- ⚠️ **/api/vote/state** — returns only projected category (vote_display_category_id) or [] if none. Players see "Waiting…" until admin projects a category.
- ⚠️ **/api/vote/display-state** — **public, no auth**. Used by display.html. Returns {session, display_category{id,name,social_boost,voter_count}}. DO NOT replace with /api/vote/state (protected → 401 on display). Original bug: pollVoteDisplay() called /api/vote/state → 401 → category never displayed (fixed 2026-05-26).
- ⚠️ **/api/admin/vote/tracking** — protected _require_admin. Matrix: voters × films × ranks × boosts per category for current vote session (current_vote_session_id). Only active session, not history.
- ⚠️ **vote_boosts net P&L** — calculation: sum(payout-amount) from closed game_sessions minus sum(amount) from vote_boosts for the user. Allows negative bonus adjustments (refunds).

**Security & Infrastructure:**
- ⚠️ **ProxyFix** — configured in app.py (x_for=1, x_proto=1, x_host=1). Do NOT remove. Flask-Limiter reads 127.0.0.1 for all IPs without it.
- ⚠️ **CSP nginx** — 'unsafe-inline' kept for scripts (inline script in play.html). Harden with nonce if templates refactored.
- ⚠️ **CASINO_BASE_URL** — if missing, QR code uses request.host_url (Host header — injectable). Always define in .env for prod.
- ⚠️ **super-admin** — username == 'admin' exactly (no DB column, no distinct role). Exclusive rights: create admins, delete admins, change roles. Protected from self-deletion/modification (anti-lockout). session['username'] accessible natively in Jinja.
- ⚠️ **set-role** — /api/admin/users/<id>/set-role super-admin only (403 otherwise). Forbidden on username='admin'. Updates badge + button in-place without reload.
- ⚠️ **admin delete user** — super-admin can delete admins (except username='admin'). Regular admin → 403 on any admin account.
- ⚠️ **/api/claim** — endpoint removed (2026-05-08). Rewards distributed exclusively via /api/admin/reward/give (admin).
- ⚠️ **Roles** — 'admin' | 'player' — SQLite CHECK constraint.

**Load Tests:**
- ⚠️ **RATELIMIT_ENABLED=false** — required for Locust. Set before pytest or locust commands.
- ⚠️ **load_test_users.json** — 100 precreated accounts. Do NOT commit.

---

## Changelog

| Session | Fichier | Modification |
|---------|---------|-------------|
| 9 (2026-05-30) | `play.html` | Ajout bouton `?` (modale `#rulesModal`) dans le header card mise — règles du jeu statiques, zéro JS, zéro route |
| 9 (2026-05-30) | `templates/play.html` | Ajout modale #rulesModal (règles roulette + mises + jetons) + bouton "COMMENT JOUER" permanent dans header card |
| 9 (2026-05-30) | `templates/play.html` | Ajout modale #voteHelpModal + bouton "COMMENT VOTER" contextuel dans #vote-panel |
| 9 (2026-05-30) | `static/js/play.js` | Masquage btn-rules-modal en mode vote (show/hide via display) |
| 9 (2026-05-30) | `templates/roulette/display.html` | QR code sorti du flux fixed → 3ème enfant de #right-panel, 260×260px, margin-top:auto |
| 9 (2026-05-30) | `static/css/midnight-gala.css` | Neutralisation flex:1 1 calc(50%) sur .btn dans .input-group mobile (#users-table) |
| 9 (2026-05-30) | `templates/admin/index.html` | Bouton − libre dans colonne "Ajouter tokens" (desktop) + bouton +600 + suppression −1 hardcodé |
| 9 (2026-05-30) | `templates/admin/index.html` | Bloc d-md-none : input+boutons +/− mobile dans colonne 5 + masquage 🪙0 sur mobile |
| 9 (2026-05-30) | `templates/admin/index.html` | Select jetons initiaux (0/150/350/600, défaut 0) dans formulaire création utilisateur |
| 9 (2026-05-30) | `routes/api.py` | add-tokens accepte les négatifs + plancher MAX(0, tokens+?) ; create user accepte initial_tokens |
| 9 (2026-05-30) | `static/js/admin.js` | Handler − libre (sub-tokens-btn) + suppression decrement-tokens-btn + flag pendingReloadAfterPw |
| 9 (2026-05-30) | `templates/dashboard.html` | Lien "Voir le résultat" ajouté pendant status=spinning (même pattern que "Placer une mise" en open) |
| 9 (2026-05-30) | `templates/play.html` | #result-panel enveloppé en card p-4 mt-3, titre "Dernier résultat", hr + bouton "Nouvelle partie" supprimés |
| 9 (2026-05-30) | `static/js/play.js` | resultPanel retiré de showOnly() — s'affiche en dessous du contenu actif, masqué uniquement au spin suivant |
| 9 (2026-05-30) | `static/js/display.js` | renderChips() : chips ancrés bottom/right (grille 2×n) — numéro de cellule toujours visible ; CHIP_SPREAD devient code mort |

