import io
import os
import random
import string
import bcrypt
from datetime import datetime, timezone

import qrcode
from flask import (Blueprint, jsonify, request, session as flask_session,
                   abort, Response)

from db import db_conn, get_active_session, get_config, resolve_spin

api_bp = Blueprint('api', __name__)

# QR cache: session_id → PNG bytes
_qr_cache: dict = {}


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_dt(s):
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _require_login():
    if 'user_id' not in flask_session:
        # Return JSON so JS callers (play.js, rewards.html) can parse resp.json() cleanly
        from flask import make_response
        abort(make_response(jsonify({'error': 'Non authentifié'}), 401))


def _require_admin():
    if flask_session.get('role') != 'admin':
        # Same: JSON so admin.js can handle 403 gracefully
        from flask import make_response
        abort(make_response(jsonify({'error': 'Accès refusé'}), 403))


def _gen_password(n=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=n))


# ─── Leaderboard ─────────────────────────────────────────────────────────────

@api_bp.route('/api/leaderboard')
def leaderboard():
    """Top 3 winners / top 3 losers by net P&L over closed sessions.
    Net = SUM(payout - amount) from closed sessions minus vote boost spend.
    Bets on open/spinning sessions are excluded (JOIN + NULL guard).
    Filtered by stats_reset_at if set in app_config.
    """
    _Q = '''
        SELECT u.username,
               COALESCE(SUM(b.payout - b.amount), 0) AS net
        FROM users u
        LEFT JOIN bets b          ON b.user_id    = u.id
        LEFT JOIN game_sessions gs ON b.session_id = gs.id
                                   AND gs.status   = 'closed'
                                   AND gs.closed_at > ?
        WHERE u.role = 'player'
          AND (b.id IS NULL OR gs.id IS NOT NULL)
        GROUP BY u.id
        HAVING net {cmp} 0
        ORDER BY net {order}
        LIMIT 5
    '''
    with db_conn() as conn:
        cfg      = get_config(conn)
        reset_at = cfg.get('stats_reset_at', '1970-01-01 00:00:00')
        winners  = conn.execute(_Q.format(cmp='>', order='DESC'), (reset_at,)).fetchall()
        losers   = conn.execute(_Q.format(cmp='<', order='ASC'),  (reset_at,)).fetchall()
    return jsonify({
        'top_winners': [{'username': r['username'], 'net': r['net']} for r in winners],
        'top_losers':  [{'username': r['username'], 'net': r['net']} for r in losers],
    })


# ─── Round result (per-spin leaderboard) ─────────────────────────────────────

@api_bp.route('/api/session/round_result')
def session_round_result():
    """Winners/losers for the most recently closed session (single-round stats)."""
    with db_conn() as conn:
        closed = conn.execute(
            "SELECT id FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not closed:
            return jsonify({'session_id': None, 'winners': [], 'losers': []})
        sid = closed['id']
        rows = conn.execute(
            '''SELECT u.username, SUM(b.payout - b.amount) AS net
               FROM bets b JOIN users u ON b.user_id = u.id
               WHERE b.session_id = ?
               GROUP BY u.id, u.username
               ORDER BY net DESC''',
            (sid,)
        ).fetchall()
    data    = [{'username': r['username'], 'net': r['net']} for r in rows]
    winners = [r for r in data if r['net'] > 0][:3]
    losers  = sorted([r for r in data if r['net'] < 0], key=lambda x: x['net'])[:3]
    return jsonify({'session_id': sid, 'winners': winners, 'losers': losers})


# ─── Draw history ────────────────────────────────────────────────────────────

@api_bp.route('/api/history')
def draw_history():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, winning_number FROM game_sessions "
            "WHERE status='closed' AND winning_number IS NOT NULL "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return jsonify([{'session_id': r['id'], 'winning_number': r['winning_number']} for r in rows])


# ─── Session status ──────────────────────────────────────────────────────────

@api_bp.route('/api/session/status')
def session_status():
    with db_conn() as conn:
        active = get_active_session(conn)
        cfg    = get_config(conn)
        mode_val = 'auto' if cfg.get('auto_mode_ui', '0') == '1' else 'manual'

        # Token balance for the logged-in player (None for unauthenticated callers)
        user_tokens = None
        if 'user_id' in flask_session:
            row = conn.execute(
                'SELECT tokens FROM users WHERE id=?', (flask_session['user_id'],)
            ).fetchone()
            if row:
                user_tokens = row['tokens']

        app_mode  = cfg.get('app_mode', 'roulette')
        vs_id_str = cfg.get('current_vote_session_id', '')
        vote_session = None
        if vs_id_str:
            vs_row = conn.execute(
                'SELECT id, status FROM vote_sessions WHERE id=?', (int(vs_id_str),)
            ).fetchone()
            if vs_row:
                vote_session = {'id': vs_row['id'], 'status': vs_row['status']}

        revealed_cats   = _json.loads(cfg.get('vote_revealed_categories', '[]'))
        total_vote_cats = conn.execute('SELECT COUNT(*) FROM vote_categories').fetchone()[0]
        _disp_raw = cfg.get('vote_display_category_id', '')
        vote_display_cat_id = int(_disp_raw) if _disp_raw else None
        vote_cats_list = [
            {'id': r['id'], 'name': r['name']}
            for r in conn.execute(
                'SELECT id, name FROM vote_categories ORDER BY display_order'
            ).fetchall()
        ] if app_mode in ('vote', 'palmares') else []

        if not active:
            return jsonify({'status': 'waiting', 'time_remaining_seconds': 0,
                            'winning_number': None,
                            'mode': mode_val,
                            'auto_interval_seconds': int(cfg.get('auto_interval_seconds', 120)),
                            'tokens': user_tokens,
                            'app_mode': app_mode,
                            'vote_session': vote_session,
                            'revealed_categories': revealed_cats,
                            'total_vote_categories': total_vote_cats,
                            'vote_display_category_id': vote_display_cat_id,
                            'vote_categories': vote_cats_list,
                            'vote_revealed_count': len(revealed_cats)})

        # Grace period: if active is 'waiting' but previous session closed < 12s ago,
        # report 'spinning' so the display page can run the wheel animation.
        # Only applies when winning_number is set (normal spin), not admin force-close.
        if active['status'] == 'waiting':
            prev = conn.execute(
                "SELECT * FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if prev and prev['closed_at'] and prev['winning_number'] is not None:
                closed_ago = (_utcnow() - _parse_dt(prev['closed_at'])).total_seconds()
                if closed_ago < 12:
                    return jsonify({
                        'status': 'spinning',
                        'winning_number': prev['winning_number'],
                        'time_remaining_seconds': 0,
                        'session_id': prev['id'],
                        'mode': mode_val,
                        'auto_interval_seconds': active['auto_interval_seconds'],
                        'tokens': user_tokens,
                        'app_mode': app_mode,
                        'vote_session': vote_session,
                        'revealed_categories': revealed_cats,
                        'total_vote_categories': total_vote_cats,
                        'vote_display_category_id': vote_display_cat_id,
                        'vote_categories': vote_cats_list,
                        'vote_revealed_count': len(revealed_cats),
                    })

        time_remaining = 0
        if active['status'] == 'open' and active['opened_at']:
            elapsed = (_utcnow() - _parse_dt(active['opened_at'])).total_seconds()
            # betting window = auto_interval_seconds (not hardcoded 30s)
            time_remaining = max(0, int(active['auto_interval_seconds'] - elapsed))

        return jsonify({
            'status': active['status'],
            'winning_number': active['winning_number'],
            'time_remaining_seconds': time_remaining,
            'session_id': active['id'],
            'mode': mode_val,
            'auto_interval_seconds': active['auto_interval_seconds'],
            'tokens': user_tokens,
            'app_mode': app_mode,
            'vote_session': vote_session,
            'revealed_categories': revealed_cats,
            'total_vote_categories': total_vote_cats,
            'vote_display_category_id': vote_display_cat_id,
            'vote_categories': vote_cats_list,
            'vote_revealed_count': len(revealed_cats),
        })


@api_bp.route('/api/session/result')
def session_result():
    _require_login()
    with db_conn() as conn:
        closed = conn.execute(
            "SELECT * FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not closed:
            abort(404)
        # Return all bets the player placed in this session
        user_bets = conn.execute(
            'SELECT id, bet_type, bet_value, amount, payout FROM bets WHERE session_id=? AND user_id=?',
            (closed['id'], flask_session['user_id'])
        ).fetchall()
        return jsonify({
            'session_id': closed['id'],
            'winning_number': closed['winning_number'],
            # list of {id, bet_type, bet_value, amount, payout}
            # payout>0 means win; payout==0 means loss
            'user_bets': [dict(b) for b in user_bets],
        })


# ─── Session bets (public — used by /roulette/display and /play) ─────────────

@api_bp.route('/api/session/bets')
def session_bets():
    """Returns bets for the current open session. No auth required (display page is public)."""
    with db_conn() as conn:
        active = get_active_session(conn)
        if not active or active['status'] != 'open':
            return jsonify([])
        rows = conn.execute(
            '''SELECT u.username, b.bet_type, b.bet_value, b.amount
               FROM bets b JOIN users u ON b.user_id = u.id
               WHERE b.session_id = ?
               ORDER BY b.id''',
            (active['id'],)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── QR code ─────────────────────────────────────────────────────────────────

@api_bp.route('/api/session/qr')
def session_qr():
    with db_conn() as conn:
        active = get_active_session(conn)
        sid = active['id'] if active else 0

    if sid not in _qr_cache:
        # Generate QR pointing to /play
        base = os.environ.get('CASINO_BASE_URL', request.host_url).rstrip('/')
        url  = f'{base}/play'
        qr   = qrcode.QRCode(version=1, box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        _qr_cache[sid] = buf.getvalue()
        # Evict old entries
        for k in list(_qr_cache.keys()):
            if k != sid:
                del _qr_cache[k]

    return Response(_qr_cache[sid], mimetype='image/png',
                    headers={'Cache-Control': 'no-store'})


# ─── Bet ─────────────────────────────────────────────────────────────────────

@api_bp.route('/api/bet', methods=['POST'])
def place_bet():
    _require_login()
    data      = request.get_json(force=True)
    bet_type  = data.get('bet_type', '')
    bet_value = str(data.get('bet_value', ''))
    amount    = data.get('amount')

    if bet_type not in ('color', 'parity', 'number', 'column', 'dozen', 'half'):
        return jsonify({'error': 'bet_type invalide'}), 400
    if bet_type == 'color' and bet_value not in ('red', 'black'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'parity' and bet_value not in ('even', 'odd'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'column' and bet_value not in ('1', '2', '3'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'dozen' and bet_value not in ('1', '2', '3'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'half' and bet_value not in ('low', 'high'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'number':
        try:
            n = int(bet_value)
            if not (0 <= n <= 36):
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify({'error': 'Numéro invalide (0–36)'}), 400
    if not isinstance(amount, int) or amount <= 0:
        return jsonify({'error': 'Montant invalide'}), 400

    user_id = flask_session['user_id']

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active = get_active_session(conn)
        if not active or active['status'] != 'open':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Session non disponible pour les mises'}), 400

        user = conn.execute(
            'SELECT * FROM users WHERE id=?', (user_id,)
        ).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 401
        if user['tokens'] < amount:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Solde insuffisant'}), 400

        conn.execute(
            'UPDATE users SET tokens = tokens - ? WHERE id=? AND tokens >= ?',
            (amount, user_id, amount)
        )
        cur = conn.execute(
            'INSERT INTO bets(session_id, user_id, bet_type, bet_value, amount) VALUES (?,?,?,?,?)',
            (active['id'], user_id, bet_type, bet_value, amount)
        )
        bet_id = cur.lastrowid
        new_balance = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()['tokens']
        bet_session_id = active['id']
        conn.execute('COMMIT')

    return jsonify({'bet_id': bet_id, 'new_balance': new_balance, 'session_id': bet_session_id})


# ─── Admin — session control ──────────────────────────────────────────────────

@api_bp.route('/api/admin/session/open', methods=['POST'])
def admin_open_session():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active = get_active_session(conn)
        if not active or active['status'] != 'waiting':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session en attente'}), 400
        cfg = get_config(conn)
        conn.execute(
            "UPDATE game_sessions SET status='open', opened_at=? WHERE id=?",
            (_utcnow().isoformat(), active['id'])
        )
        if cfg.get('auto_mode_ui', '0') == '1':
            conn.execute(
                "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_enabled','1')"
            )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/session/spin', methods=['POST'])
def admin_spin_session():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active = get_active_session(conn)
        if not active or active['status'] not in ('open', 'spinning'):
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session ouverte'}), 400
        if active['status'] == 'open':
            winning = random.randint(0, 36)
            conn.execute(
                "UPDATE game_sessions SET status='spinning', winning_number=? WHERE id=? AND status='open'",
                (winning, active['id'])
            )
            conn.execute('COMMIT')
        else:
            conn.execute('ROLLBACK')
    resolve_spin(active['id'])
    return jsonify({'ok': True})


@api_bp.route('/api/admin/session/close', methods=['POST'])
def admin_close_session():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active = get_active_session(conn)
        if not active:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session active'}), 400
        if active['status'] == 'spinning':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'spin in progress'}), 400
        if active['status'] not in ('open', 'waiting'):
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Session non fermable'}), 400
        conn.execute(
            "UPDATE game_sessions SET status='closed', closed_at=? WHERE id=?",
            (_utcnow().isoformat(), active['id'])
        )
        # Switch to manual so the scheduler doesn't auto-reopen after a force-close
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_enabled','0')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/stats/reset', methods=['POST'])
def admin_stats_reset():
    """Set stats_reset_at to now — leaderboard will only count bets placed after this timestamp."""
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Use SQLite datetime format to match bets.created_at DEFAULT (datetime('now'))
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('stats_reset_at',?)",
            (_utcnow().isoformat(),)
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


# ─── Admin — mode ─────────────────────────────────────────────────────────────

@api_bp.route('/api/admin/mode', methods=['POST'])
def admin_set_mode():
    _require_admin()
    data     = request.get_json(force=True)
    mode     = data.get('mode', 'manual')
    interval = int(data.get('interval', 120))

    if mode not in ('manual', 'auto'):
        return jsonify({'error': 'mode invalide'}), 400
    if not (10 <= interval <= 3600):
        return jsonify({'error': 'interval doit être entre 10 et 3600s'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_ui',?)",
            ('1' if mode == 'auto' else '0',)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_interval_seconds',?)",
            (str(interval),)
        )
        if mode == 'manual':
            conn.execute(
                "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_enabled','0')"
            )
        conn.execute('COMMIT')

    return jsonify({'ok': True, 'mode': mode, 'interval': interval})


# ─── Admin — users ───────────────────────────────────────────────────────────

@api_bp.route('/api/admin/users/<int:uid>/zero-tokens', methods=['POST'])
def admin_zero_tokens(uid):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        conn.execute('UPDATE users SET tokens = 0 WHERE id=?', (uid,))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'new_balance': 0})


@api_bp.route('/api/admin/users/<int:uid>/delete', methods=['POST'])
def admin_delete_user(uid):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id, role, username FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        if user['role'] == 'admin':
            if flask_session.get('username') != 'admin':
                conn.execute('ROLLBACK')
                return jsonify({'error': 'Impossible de supprimer un administrateur'}), 403
            if user['username'] == 'admin':
                conn.execute('ROLLBACK')
                return jsonify({'error': 'Impossible de supprimer le super-admin'}), 403
        # Delete associated data (no FK cascade defined)
        conn.execute('DELETE FROM bets WHERE user_id=?', (uid,))
        conn.execute('DELETE FROM users WHERE id=?', (uid,))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/users/<int:uid>/decrement-tokens', methods=['POST'])
def admin_decrement_tokens(uid):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id, tokens FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        # Floor at 0 — balance cannot go negative
        conn.execute('UPDATE users SET tokens = MAX(0, tokens - 1) WHERE id=?', (uid,))
        new_balance = conn.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()['tokens']
        conn.execute('COMMIT')
    return jsonify({'new_balance': new_balance})


@api_bp.route('/api/admin/users/<int:uid>/add-tokens', methods=['POST'])
def admin_add_tokens(uid):
    _require_admin()
    data   = request.get_json(force=True)
    amount = data.get('amount')
    if not isinstance(amount, int) or amount == 0:
        return jsonify({'error': 'Montant invalide'}), 400
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        conn.execute('UPDATE users SET tokens = MAX(0, tokens + ?) WHERE id=?', (amount, uid))
        new_balance = conn.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()['tokens']
        conn.execute('COMMIT')
    return jsonify({'new_balance': new_balance})


@api_bp.route('/api/admin/users/create', methods=['POST'])
def admin_create_user():
    _require_admin()
    data     = request.get_json(force=True)
    username       = data.get('username', '').strip()
    role           = data.get('role', 'player')
    initial_tokens = data.get('initial_tokens', 0)
    if initial_tokens not in (0, 150, 350, 600):
        initial_tokens = 0
    if not username:
        return jsonify({'error': 'Nom requis'}), 400
    if role not in ('admin', 'player'):
        return jsonify({'error': 'Role invalide'}), 400
    if role == 'admin' and flask_session.get('username') != 'admin':
        return jsonify({'error': 'Seul le super-admin peut créer des comptes admin'}), 403
    password = _gen_password()
    pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    with db_conn() as conn:
        try:
            conn.execute(
                'INSERT INTO users(username, password_hash, role, tokens) VALUES (?,?,?,?)',
                (username, pw_hash, role, initial_tokens)
            )
            conn.commit()
        except Exception as e:
            if 'UNIQUE' in str(e):
                return jsonify({'error': 'Nom d\'utilisateur déjà pris'}), 409
            raise
    return jsonify({'username': username, 'password': password})


@api_bp.route('/api/admin/users/<int:uid>/set-role', methods=['POST'])
def admin_set_role(uid):
    _require_admin()
    if flask_session.get('username') != 'admin':
        return jsonify({'error': 'Seul le super-admin peut modifier les rôles'}), 403
    data = request.get_json(force=True)
    role = data.get('role', '')
    if role not in ('admin', 'player'):
        return jsonify({'error': 'Rôle invalide'}), 400
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id, username FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        if user['username'] == 'admin':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Impossible de modifier le super-admin'}), 403
        conn.execute('UPDATE users SET role=? WHERE id=?', (role, uid))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'role': role})


@api_bp.route('/api/admin/users/<int:uid>/reset-password', methods=['POST'])
def admin_reset_password(uid):
    _require_admin()
    password = _gen_password()
    pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    with db_conn() as conn:
        result = conn.execute(
            'UPDATE users SET password_hash=? WHERE id=?', (pw_hash, uid)
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({'error': 'Utilisateur introuvable'}), 404
    return jsonify({'password': password})


# ─── Vote — Admin — Catalogue ─────────────────────────────────────────────────

import json as _json

@api_bp.route('/api/admin/vote/catalogue')
def vote_catalogue():
    """Toutes les catégories + films — sans filtre display_category_id (usage admin)."""
    _require_admin()
    with db_conn() as conn:
        cats = conn.execute(
            'SELECT id, name, display_order FROM vote_categories ORDER BY display_order, id'
        ).fetchall()
        result = []
        for cat in cats:
            films = conn.execute(
                'SELECT id, title FROM vote_films WHERE category_id=? ORDER BY id',
                (cat['id'],)
            ).fetchall()
            result.append({
                'id':            cat['id'],
                'name':          cat['name'],
                'display_order': cat['display_order'],
                'films':         [{'id': f['id'], 'title': f['title']} for f in films],
            })
    return jsonify({'categories': result})


@api_bp.route('/api/admin/vote/categories', methods=['POST'])
def vote_create_category():
    _require_admin()
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Nom requis'}), 400
    with db_conn() as conn:
        existing = conn.execute(
            'SELECT id FROM vote_categories WHERE LOWER(name)=LOWER(?)', (name,)
        ).fetchone()
        if existing:
            return jsonify({'error': 'Catégorie déjà existante'}), 409
        max_ord = conn.execute(
            'SELECT COALESCE(MAX(display_order),0) FROM vote_categories'
        ).fetchone()[0]
        cur = conn.execute(
            'INSERT INTO vote_categories(name, display_order) VALUES (?,?)',
            (name, max_ord + 1)
        )
        conn.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid, 'name': name})


@api_bp.route('/api/admin/vote/categories/<int:cid>/delete', methods=['POST'])
def vote_delete_category(cid):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Check no active vote session uses films from this category
        active_vote = conn.execute(
            '''SELECT vs.id FROM vote_sessions vs
               JOIN vote_rankings vr ON vr.session_id = vs.id
               JOIN vote_films vf ON vf.id = vr.film_id
               WHERE vf.category_id = ? AND vs.status = 'open' LIMIT 1''',
            (cid,)
        ).fetchone()
        if active_vote:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Vote en cours pour cette catégorie'}), 400
        # Also block deletion if there is any open vote session at all (not just with rankings)
        open_session = conn.execute(
            "SELECT id FROM vote_sessions WHERE status='open' LIMIT 1"
        ).fetchone()
        if open_session:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Impossible de supprimer pendant un vote ouvert'}), 400
        film_ids = [r['id'] for r in conn.execute(
            'SELECT id FROM vote_films WHERE category_id=?', (cid,)
        ).fetchall()]
        for fid in film_ids:
            conn.execute('DELETE FROM vote_rankings WHERE film_id=?', (fid,))
        conn.execute('DELETE FROM vote_boosts WHERE category_id=?', (cid,))
        conn.execute('DELETE FROM vote_films WHERE category_id=?', (cid,))
        conn.execute('DELETE FROM vote_categories WHERE id=?', (cid,))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/vote/films', methods=['POST'])
def vote_create_film():
    _require_admin()
    data = request.get_json(force=True)
    title       = data.get('title', '').strip()
    category_id = data.get('category_id')
    if not title:
        return jsonify({'error': 'Titre requis'}), 400
    if not isinstance(category_id, int):
        return jsonify({'error': 'category_id requis'}), 400
    with db_conn() as conn:
        cat = conn.execute(
            'SELECT id FROM vote_categories WHERE id=?', (category_id,)
        ).fetchone()
        if not cat:
            return jsonify({'error': 'Catégorie introuvable'}), 404
        try:
            cur = conn.execute(
                'INSERT INTO vote_films(category_id, title) VALUES (?,?)',
                (category_id, title)
            )
            conn.commit()
        except Exception as e:
            if 'UNIQUE' in str(e):
                return jsonify({'error': 'Film déjà existant dans cette catégorie'}), 409
            raise
    return jsonify({'ok': True, 'id': cur.lastrowid, 'title': title, 'category_id': category_id})


@api_bp.route('/api/admin/vote/films/<int:fid>/delete', methods=['POST'])
def vote_delete_film(fid):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM vote_rankings WHERE film_id=?', (fid,))
        result = conn.execute('DELETE FROM vote_films WHERE id=?', (fid,))
        conn.execute('COMMIT')
        if result.rowcount == 0:
            return jsonify({'error': 'Film introuvable'}), 404
    return jsonify({'ok': True})


# ─── Vote — Admin — Session ───────────────────────────────────────────────────

@api_bp.route('/api/admin/vote/open', methods=['POST'])
def vote_open():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            "SELECT id FROM vote_sessions WHERE status IN ('open','waiting') LIMIT 1"
        ).fetchone()
        if existing:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Une session de vote est déjà en cours'}), 400
        now_iso = _utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO vote_sessions(status, opened_at) VALUES ('open', ?)",
            (now_iso,)
        )
        sid = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('current_vote_session_id',?)",
            (str(sid),)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','vote')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_revealed_categories','[]')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'session_id': sid})


@api_bp.route('/api/admin/vote/close', methods=['POST'])
def vote_close():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg   = get_config(conn)
        vs_id = cfg.get('current_vote_session_id', '')
        if not vs_id:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote active'}), 400
        vs = conn.execute(
            "SELECT * FROM vote_sessions WHERE id=? AND status='open'", (int(vs_id),)
        ).fetchone()
        if not vs:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote ouverte'}), 400

        # Compute points for each ranking
        categories = conn.execute(
            'SELECT id FROM vote_categories'
        ).fetchall()
        for cat in categories:
            cat_id = cat['id']
            films  = conn.execute(
                'SELECT id FROM vote_films WHERE category_id=?', (cat_id,)
            ).fetchall()
            n = len(films)
            if n == 0:
                continue
            base = max(10, n * 2.5)
            rankings = conn.execute(
                '''SELECT vr.id, vr.user_id, vr.rank
                   FROM vote_rankings vr
                   JOIN vote_films vf ON vf.id = vr.film_id
                   WHERE vr.session_id=? AND vf.category_id=?''',
                (int(vs_id), cat_id)
            ).fetchall()
            for rk in rankings:
                raw_float = base * (0.55 ** (rk['rank'] - 1))
                boost  = conn.execute(
                    '''SELECT amount FROM vote_boosts
                       WHERE session_id=? AND user_id=? AND category_id=?''',
                    (int(vs_id), rk['user_id'], cat_id)
                ).fetchone()
                boost_amount = boost['amount'] if boost else 0
                multiplier   = 1 + (boost_amount / 100)
                points       = max(1, round(raw_float * multiplier))
                conn.execute(
                    'UPDATE vote_rankings SET points=? WHERE id=?',
                    (points, rk['id'])
                )

        now_iso = _utcnow().isoformat()
        conn.execute(
            "UPDATE vote_sessions SET status='closed', closed_at=? WHERE id=?",
            (now_iso, int(vs_id))
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','closed')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/vote/palmares', methods=['POST'])
def vote_palmares():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg   = get_config(conn)
        vs_id = cfg.get('current_vote_session_id', '')
        if not vs_id:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote'}), 400
        vs = conn.execute(
            "SELECT * FROM vote_sessions WHERE id=? AND status='closed'", (int(vs_id),)
        ).fetchone()
        if not vs:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Session non fermée'}), 400
        conn.execute(
            "UPDATE vote_sessions SET status='palmares' WHERE id=?", (int(vs_id),)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','palmares')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_revealed_categories','[]')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/admin/vote/reveal-next', methods=['POST'])
def vote_reveal_next():
    _require_admin()
    data       = request.get_json(force=True) or {}
    cat_id_req = data.get('category_id')
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg      = get_config(conn)
        revealed = _json.loads(cfg.get('vote_revealed_categories', '[]'))
        cats     = conn.execute(
            'SELECT id, name FROM vote_categories ORDER BY display_order'
        ).fetchall()
        next_cat = None
        if cat_id_req is not None:
            for cat in cats:
                if cat['id'] == cat_id_req:
                    next_cat = cat
                    break
            if next_cat is None:
                conn.execute('ROLLBACK')
                return jsonify({'error': 'Catégorie introuvable'}), 400
            if cat_id_req in revealed:
                # Already revealed — just re-project without duplicating in revealed
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_display_category_id',?)",
                    (str(cat_id_req),)
                )
                conn.execute('COMMIT')
                return jsonify({
                    'ok': True,
                    'revealed_categories': revealed,
                    'category_name': next_cat['name'],
                    'all_revealed': len(revealed) == len(cats),
                })
        else:
            for cat in cats:
                if cat['id'] not in revealed:
                    next_cat = cat
                    break
        if next_cat is None:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Toutes les catégories sont déjà révélées'}), 400
        revealed.append(next_cat['id'])
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_revealed_categories',?)",
            (_json.dumps(revealed),)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_display_category_id',?)",
            (str(next_cat['id']),)
        )
        conn.execute('COMMIT')
    return jsonify({
        'ok': True,
        'revealed_categories': revealed,
        'category_name': next_cat['name'],
        'all_revealed': len(revealed) == len(cats),
    })


@api_bp.route('/api/admin/vote/display-category', methods=['POST'])
def vote_set_display_category():
    _require_admin()
    data   = request.get_json(force=True)
    cat_id = data.get('category_id')  # int or None
    val    = str(int(cat_id)) if cat_id is not None else ''
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_display_category_id',?)",
            (val,)
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'category_id': cat_id})


@api_bp.route('/api/admin/vote/reset-mode', methods=['POST'])
def vote_reset_mode():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Abandon any open/waiting vote session so it cannot block a future open
        conn.execute(
            "UPDATE vote_sessions SET status='closed', closed_at=datetime('now')"
            " WHERE status IN ('open','waiting')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','roulette')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('current_vote_session_id','')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('vote_display_category_id','')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


# ─── Vote — Player ────────────────────────────────────────────────────────────

@api_bp.route('/api/vote/state')
def vote_state():
    _require_login()
    user_id = flask_session['user_id']
    with db_conn() as conn:
        cfg   = get_config(conn)
        vs_id = cfg.get('current_vote_session_id', '')
        user  = conn.execute('SELECT tokens FROM users WHERE id=?', (user_id,)).fetchone()

        vs = None
        if vs_id:
            vs_row = conn.execute('SELECT id, status FROM vote_sessions WHERE id=?', (int(vs_id),)).fetchone()
            if vs_row:
                vs = {'id': vs_row['id'], 'status': vs_row['status']}

        disp_raw    = cfg.get('vote_display_category_id', '')
        disp_cat_id = int(disp_raw) if disp_raw else None
        if disp_cat_id is not None:
            cats = conn.execute(
                'SELECT id, name, display_order FROM vote_categories WHERE id=?',
                (disp_cat_id,)
            ).fetchall()
        else:
            cats = []
        result_cats = []
        for cat in cats:
            films = conn.execute(
                'SELECT id, title FROM vote_films WHERE category_id=? ORDER BY id',
                (cat['id'],)
            ).fetchall()
            user_rankings = []
            user_boost    = 0
            social_boost  = 0
            if vs and vs_id:
                ur_rows = conn.execute(
                    '''SELECT vr.film_id, vr.rank FROM vote_rankings vr
                       JOIN vote_films vf ON vf.id = vr.film_id
                       WHERE vr.session_id=? AND vr.user_id=? AND vf.category_id=?
                       ORDER BY vr.rank''',
                    (int(vs_id), user_id, cat['id'])
                ).fetchall()
                user_rankings = [{'film_id': r['film_id'], 'rank': r['rank']} for r in ur_rows]
                boost_row = conn.execute(
                    '''SELECT amount FROM vote_boosts
                       WHERE session_id=? AND user_id=? AND category_id=?''',
                    (int(vs_id), user_id, cat['id'])
                ).fetchone()
                user_boost = boost_row['amount'] if boost_row else 0
                social_boost = conn.execute(
                    '''SELECT COALESCE(SUM(amount),0) AS total FROM vote_boosts
                       WHERE session_id=? AND category_id=?''',
                    (int(vs_id), cat['id'])
                ).fetchone()['total']
            result_cats.append({
                'id': cat['id'],
                'name': cat['name'],
                'display_order': cat['display_order'],
                'films': [{'id': f['id'], 'title': f['title']} for f in films],
                'user_rankings': user_rankings,
                'user_boost': user_boost,
                'social_boost': social_boost,
            })
    return jsonify({
        'session': vs,
        'categories': result_cats,
        'user_tokens': user['tokens'] if user else 0,
    })


@api_bp.route('/api/vote/rankings', methods=['POST'])
def vote_rankings():
    _require_login()
    user_id = flask_session['user_id']
    data    = request.get_json(force=True)
    cat_id  = data.get('category_id')
    rankings = data.get('rankings', [])

    if not isinstance(cat_id, int):
        return jsonify({'error': 'category_id requis'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg   = get_config(conn)
        vs_id = cfg.get('current_vote_session_id', '')
        if not vs_id:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote active'}), 400
        vs = conn.execute(
            "SELECT id FROM vote_sessions WHERE id=? AND status='open'", (int(vs_id),)
        ).fetchone()
        if not vs:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Session de vote non ouverte'}), 400

        # Validate all film_ids belong to category
        film_ids_cat = {r['id'] for r in conn.execute(
            'SELECT id FROM vote_films WHERE category_id=?', (cat_id,)
        ).fetchall()}
        submitted_film_ids = [r['film_id'] for r in rankings]
        for fid in submitted_film_ids:
            if fid not in film_ids_cat:
                conn.execute('ROLLBACK')
                return jsonify({'error': f'film_id {fid} hors catégorie'}), 400

        # Validate ranks form a 1..N sequence without duplicates
        ranks = sorted([r['rank'] for r in rankings])
        if ranks and ranks != list(range(1, len(ranks) + 1)):
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Rangs invalides — séquence 1..N requise'}), 400

        for rk in rankings:
            conn.execute(
                '''INSERT OR REPLACE INTO vote_rankings(session_id, user_id, film_id, rank, updated_at)
                   VALUES (?,?,?,?,datetime('now'))''',
                (int(vs_id), user_id, rk['film_id'], rk['rank'])
            )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/vote/boost', methods=['POST'])
def vote_boost():
    _require_login()
    user_id = flask_session['user_id']
    data    = request.get_json(force=True)
    cat_id  = data.get('category_id')
    amount  = data.get('amount', 0)

    if not isinstance(cat_id, int):
        return jsonify({'error': 'category_id requis'}), 400
    if not isinstance(amount, int) or amount < 0:
        return jsonify({'error': 'Montant invalide'}), 400
    MAX_BOOST = 300
    if amount > MAX_BOOST:
        return jsonify({'error': f'Maximum {MAX_BOOST} jetons par catégorie'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg   = get_config(conn)
        vs_id = cfg.get('current_vote_session_id', '')
        if not vs_id:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote active'}), 400
        vs = conn.execute(
            "SELECT id FROM vote_sessions WHERE id=? AND status='open'", (int(vs_id),)
        ).fetchone()
        if not vs:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Session de vote non ouverte'}), 400

        existing = conn.execute(
            '''SELECT amount FROM vote_boosts
               WHERE session_id=? AND user_id=? AND category_id=?''',
            (int(vs_id), user_id, cat_id)
        ).fetchone()
        old_amount = existing['amount'] if existing else 0
        delta      = amount - old_amount

        user = conn.execute('SELECT tokens FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 401

        if delta > 0 and user['tokens'] < delta:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Solde insuffisant'}), 400

        if delta != 0:
            conn.execute(
                'UPDATE users SET tokens = tokens - ? WHERE id=?',
                (delta, user_id)
            )
        conn.execute(
            '''INSERT OR REPLACE INTO vote_boosts(session_id, user_id, category_id, amount, updated_at)
               VALUES (?,?,?,?,datetime('now'))''',
            (int(vs_id), user_id, cat_id, amount)
        )
        tokens_remaining = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()['tokens']
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'tokens_remaining': tokens_remaining,
                    'old_amount': old_amount, 'new_amount': amount})


# ─── Vote — Public (display) ──────────────────────────────────────────────────

@api_bp.route('/api/vote/results')
def vote_results():
    with db_conn() as conn:
        cfg      = get_config(conn)
        app_mode = cfg.get('app_mode', 'roulette')
        revealed = _json.loads(cfg.get('vote_revealed_categories', '[]'))
        cats     = conn.execute(
            'SELECT id, name FROM vote_categories ORDER BY display_order'
        ).fetchall()
        vs_id    = cfg.get('current_vote_session_id', '')

        result_cats = []
        for cat in cats:
            films    = conn.execute(
                'SELECT id, title FROM vote_films WHERE category_id=? ORDER BY id',
                (cat['id'],)
            ).fetchall()
            is_revealed = cat['id'] in revealed
            film_list = []
            if is_revealed and vs_id:
                scores = {r['film_id']: r['total'] for r in conn.execute(
                    '''SELECT film_id, SUM(points) AS total FROM vote_rankings
                       WHERE session_id=?
                       GROUP BY film_id''',
                    (int(vs_id),)
                ).fetchall()}
                sorted_films = sorted(films, key=lambda f: -scores.get(f['id'], 0))
                for rank_pos, f in enumerate(sorted_films, 1):
                    score = scores.get(f['id'], 0)
                    film_list.append({
                        'id': f['id'], 'title': f['title'],
                        'score': round(score, 2), 'rank': rank_pos,
                    })
            else:
                for f in films:
                    film_list.append({'id': f['id'], 'title': f['title'],
                                      'score': None, 'rank': None})
            result_cats.append({
                'id': cat['id'], 'name': cat['name'],
                'revealed': is_revealed,
                'films': film_list,
            })
    return jsonify({'app_mode': app_mode, 'categories': result_cats})


# ─── Vote — Public display state (no auth) ───────────────────────────────────

@api_bp.route('/api/vote/display-state')
def vote_display_state():
    """Public: current vote display config for display.html — no login required."""
    with db_conn() as conn:
        cfg       = get_config(conn)
        vs_id_str = cfg.get('current_vote_session_id', '')
        if not vs_id_str:
            return jsonify({'session': None, 'display_category': None})

        vs_row = conn.execute(
            'SELECT id, status FROM vote_sessions WHERE id=?', (int(vs_id_str),)
        ).fetchone()
        if not vs_row:
            return jsonify({'session': None, 'display_category': None})

        session_info = {'id': vs_row['id'], 'status': vs_row['status']}

        disp_raw = cfg.get('vote_display_category_id', '')
        cat_id   = int(disp_raw) if disp_raw else None
        display_category = None

        if cat_id is not None:
            cat_row = conn.execute(
                'SELECT id, name FROM vote_categories WHERE id=?', (cat_id,)
            ).fetchone()
            if cat_row:
                social_boost = conn.execute(
                    'SELECT COALESCE(SUM(amount),0) AS total FROM vote_boosts'
                    ' WHERE session_id=? AND category_id=?',
                    (int(vs_id_str), cat_id)
                ).fetchone()['total']
                voter_count = conn.execute(
                    'SELECT COUNT(DISTINCT user_id) AS cnt FROM vote_rankings'
                    ' WHERE session_id=? AND film_id IN'
                    ' (SELECT id FROM vote_films WHERE category_id=?)',
                    (int(vs_id_str), cat_id)
                ).fetchone()['cnt']
                display_category = {
                    'id':          cat_row['id'],
                    'name':        cat_row['name'],
                    'social_boost': social_boost,
                    'voter_count': voter_count,
                }

    return jsonify({'session': session_info, 'display_category': display_category})


# ─── Vote — Admin tracking ────────────────────────────────────────────────────

@api_bp.route('/api/admin/vote/tracking')
def vote_tracking():
    """Admin: live per-user vote rankings for the current vote session."""
    _require_admin()
    session_id_param = request.args.get('session_id')
    with db_conn() as conn:
        cfg       = get_config(conn)
        vs_id_str = session_id_param or cfg.get('current_vote_session_id', '')
        if not vs_id_str:
            return jsonify({'session_id': None, 'categories': []})

        vs_id = int(vs_id_str)
        cats  = conn.execute(
            'SELECT id, name FROM vote_categories ORDER BY display_order'
        ).fetchall()

        result_cats = []
        for cat in cats:
            cat_id = cat['id']
            films  = conn.execute(
                'SELECT id, title FROM vote_films WHERE category_id=? ORDER BY id',
                (cat_id,)
            ).fetchall()
            film_map = {f['id']: f['title'] for f in films}

            voter_rows = conn.execute(
                '''SELECT DISTINCT vr.user_id, u.username
                   FROM vote_rankings vr
                   JOIN users u ON u.id = vr.user_id
                   JOIN vote_films vf ON vf.id = vr.film_id
                   WHERE vr.session_id=? AND vf.category_id=?
                   ORDER BY u.username''',
                (vs_id, cat_id)
            ).fetchall()

            total_boost = conn.execute(
                'SELECT COALESCE(SUM(amount),0) AS total FROM vote_boosts'
                ' WHERE session_id=? AND category_id=?',
                (vs_id, cat_id)
            ).fetchone()['total']

            voters = []
            for vr in voter_rows:
                uid    = vr['user_id']
                rk_rows = conn.execute(
                    '''SELECT vr2.film_id, vr2.rank FROM vote_rankings vr2
                       JOIN vote_films vf ON vf.id = vr2.film_id
                       WHERE vr2.session_id=? AND vr2.user_id=? AND vf.category_id=?
                       ORDER BY vr2.rank ASC''',
                    (vs_id, uid, cat_id)
                ).fetchall()
                boost_row = conn.execute(
                    'SELECT amount FROM vote_boosts'
                    ' WHERE session_id=? AND user_id=? AND category_id=?',
                    (vs_id, uid, cat_id)
                ).fetchone()
                voters.append({
                    'username': vr['username'],
                    'rankings': [
                        {'film_id': r['film_id'],
                         'film_title': film_map.get(r['film_id'], '?'),
                         'rank': r['rank']}
                        for r in rk_rows
                    ],
                    'boost': boost_row['amount'] if boost_row else 0,
                })

            result_cats.append({
                'id':          cat_id,
                'name':        cat['name'],
                'voter_count': len(voter_rows),
                'total_boost': total_boost,
                'films':       [{'id': f['id'], 'title': f['title']} for f in films],
                'voters':      voters,
            })

    return jsonify({'session_id': vs_id, 'categories': result_cats})


@api_bp.route('/api/admin/vote/sessions')
def vote_sessions():
    """Admin: list all vote sessions ordered newest first."""
    _require_admin()
    with db_conn() as conn:
        rows = conn.execute(
            'SELECT id, status, opened_at, closed_at FROM vote_sessions ORDER BY id DESC'
        ).fetchall()
    return jsonify({'sessions': [
        {'id': r['id'], 'status': r['status'],
         'opened_at': r['opened_at'], 'closed_at': r['closed_at']}
        for r in rows
    ]})
