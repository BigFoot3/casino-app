import io
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
        abort(401)


def _require_admin():
    if flask_session.get('role') != 'admin':
        abort(403)


def _gen_password(n=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=n))


# ─── Session status ──────────────────────────────────────────────────────────

@api_bp.route('/api/session/status')
def session_status():
    with db_conn() as conn:
        active = get_active_session(conn)
        cfg    = get_config(conn)

        if not active:
            return jsonify({'status': 'waiting', 'time_remaining_seconds': 0,
                            'winning_number': None,
                            'mode': cfg.get('auto_mode_enabled','0') == '1' and 'auto' or 'manual',
                            'auto_interval_seconds': int(cfg.get('auto_interval_seconds', 120))})

        # Grace period: if active is 'waiting' but previous session closed < 12s ago,
        # report 'spinning' so the display page can run the wheel animation.
        if active['status'] == 'waiting':
            prev = conn.execute(
                "SELECT * FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if prev and prev['closed_at']:
                closed_ago = (_utcnow() - _parse_dt(prev['closed_at'])).total_seconds()
                if closed_ago < 12:
                    return jsonify({
                        'status': 'spinning',
                        'winning_number': prev['winning_number'],
                        'time_remaining_seconds': 0,
                        'session_id': prev['id'],
                        'mode': active['mode'],
                        'auto_interval_seconds': active['auto_interval_seconds'],
                    })

        time_remaining = 0
        if active['status'] == 'open' and active['opened_at']:
            elapsed = (_utcnow() - _parse_dt(active['opened_at'])).total_seconds()
            time_remaining = max(0, int(30 - elapsed))

        return jsonify({
            'status': active['status'],
            'winning_number': active['winning_number'],
            'time_remaining_seconds': time_remaining,
            'session_id': active['id'],
            'mode': active['mode'],
            'auto_interval_seconds': active['auto_interval_seconds'],
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
        # Only return if there's no newer active session (i.e., result is for the last round)
        active = get_active_session(conn)
        # Return result for the most recently closed session
        user_bet = conn.execute(
            'SELECT * FROM bets WHERE session_id=? AND user_id=?',
            (closed['id'], flask_session['user_id'])
        ).fetchone()
        return jsonify({
            'winning_number': closed['winning_number'],
            'user_bet': dict(user_bet) if user_bet else None,
        })


# ─── QR code ─────────────────────────────────────────────────────────────────

@api_bp.route('/api/session/qr')
def session_qr():
    with db_conn() as conn:
        active = get_active_session(conn)
        sid = active['id'] if active else 0

    if sid not in _qr_cache:
        # Generate QR pointing to /play
        base = request.host_url.rstrip('/')
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

    if bet_type not in ('color', 'parity', 'number'):
        return jsonify({'error': 'bet_type invalide'}), 400
    if bet_type == 'color' and bet_value not in ('red', 'black'):
        return jsonify({'error': 'bet_value invalide'}), 400
    if bet_type == 'parity' and bet_value not in ('even', 'odd'):
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
        if user['tokens'] < amount:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Solde insuffisant'}), 400

        # Check duplicate bet
        existing = conn.execute(
            'SELECT id FROM bets WHERE session_id=? AND user_id=?',
            (active['id'], user_id)
        ).fetchone()
        if existing:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Mise déjà enregistrée'}), 409

        conn.execute(
            'UPDATE users SET tokens = tokens - ? WHERE id=? AND tokens >= ?',
            (amount, user_id, amount)
        )
        conn.execute(
            'INSERT INTO bets(session_id, user_id, bet_type, bet_value, amount) VALUES (?,?,?,?,?)',
            (active['id'], user_id, bet_type, bet_value, amount)
        )
        new_balance = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()['tokens']
        conn.execute('COMMIT')

    return jsonify({'new_balance': new_balance})


# ─── Claim reward ─────────────────────────────────────────────────────────────

@api_bp.route('/api/claim', methods=['POST'])
def claim_reward():
    _require_login()
    data      = request.get_json(force=True)
    reward_id = data.get('reward_id')
    user_id   = flask_session['user_id']

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        reward = conn.execute(
            'SELECT * FROM rewards WHERE id=? AND active=1', (reward_id,)
        ).fetchone()
        if not reward:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Récompense introuvable'}), 400
        if reward['stock'] <= 0:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Stock épuisé'}), 400

        user = conn.execute(
            'SELECT * FROM users WHERE id=?', (user_id,)
        ).fetchone()
        if user['tokens'] < reward['token_cost']:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Solde insuffisant'}), 400

        conn.execute(
            'UPDATE users SET tokens = tokens - ? WHERE id=? AND tokens >= ?',
            (reward['token_cost'], user_id, reward['token_cost'])
        )
        conn.execute(
            'UPDATE rewards SET stock = stock - 1 WHERE id=?', (reward_id,)
        )
        conn.execute(
            'INSERT INTO reward_claims(user_id, reward_id) VALUES (?,?)',
            (user_id, reward_id)
        )
        new_balance = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()['tokens']
        conn.execute('COMMIT')

    return jsonify({'new_balance': new_balance})


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
        conn.execute(
            "UPDATE game_sessions SET status='open', opened_at=? WHERE id=?",
            (_utcnow().isoformat(), active['id'])
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


# ─── Admin — mode ─────────────────────────────────────────────────────────────

@api_bp.route('/api/admin/mode', methods=['POST'])
def admin_set_mode():
    _require_admin()
    data     = request.get_json(force=True)
    mode     = data.get('mode', 'manual')
    interval = int(data.get('interval', 120))

    if mode not in ('manual', 'auto'):
        return jsonify({'error': 'mode invalide'}), 400
    if not (60 <= interval <= 300):
        return jsonify({'error': 'interval doit être entre 60 et 300s'}), 400

    with db_conn() as conn:
        enabled = '1' if mode == 'auto' else '0'
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_enabled',?)", (enabled,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_interval_seconds',?)",
            (str(interval),)
        )
        conn.commit()

    return jsonify({'ok': True, 'mode': mode, 'interval': interval})


# ─── Admin — users ───────────────────────────────────────────────────────────

@api_bp.route('/api/admin/users/<int:uid>/add-tokens', methods=['POST'])
def admin_add_tokens(uid):
    _require_admin()
    data   = request.get_json(force=True)
    amount = data.get('amount')
    if not isinstance(amount, int) or amount <= 0:
        return jsonify({'error': 'Montant invalide'}), 400
    with db_conn() as conn:
        conn.execute('UPDATE users SET tokens = tokens + ? WHERE id=?', (amount, uid))
        conn.commit()
        user = conn.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        return jsonify({'error': 'Utilisateur introuvable'}), 404
    return jsonify({'new_balance': user['tokens']})


@api_bp.route('/api/admin/users/create', methods=['POST'])
def admin_create_user():
    _require_admin()
    data     = request.get_json(force=True)
    username = data.get('username', '').strip()
    role     = data.get('role', 'player')
    if not username:
        return jsonify({'error': 'Nom requis'}), 400
    if role not in ('admin', 'player'):
        return jsonify({'error': 'Role invalide'}), 400
    password = _gen_password()
    pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with db_conn() as conn:
        try:
            conn.execute(
                'INSERT INTO users(username, password_hash, role) VALUES (?,?,?)',
                (username, pw_hash, role)
            )
            conn.commit()
        except Exception as e:
            if 'UNIQUE' in str(e):
                return jsonify({'error': 'Nom d\'utilisateur déjà pris'}), 409
            raise
    return jsonify({'username': username, 'password': password})


@api_bp.route('/api/admin/users/<int:uid>/reset-password', methods=['POST'])
def admin_reset_password(uid):
    _require_admin()
    password = _gen_password()
    pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with db_conn() as conn:
        result = conn.execute(
            'UPDATE users SET password_hash=? WHERE id=?', (pw_hash, uid)
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({'error': 'Utilisateur introuvable'}), 404
    return jsonify({'password': password})


# ─── Admin — rewards ─────────────────────────────────────────────────────────

@api_bp.route('/api/admin/rewards', methods=['POST'])
def admin_create_reward():
    _require_admin()
    data  = request.get_json(force=True)
    name  = data.get('name', '').strip()
    desc  = data.get('description', '').strip()
    cost  = data.get('token_cost')
    stock = data.get('stock', 0)
    if not name or not isinstance(cost, int) or cost <= 0:
        return jsonify({'error': 'Données invalides'}), 400
    with db_conn() as conn:
        cur = conn.execute(
            'INSERT INTO rewards(name, description, token_cost, stock) VALUES (?,?,?,?)',
            (name, desc, cost, stock)
        )
        conn.commit()
        rid = cur.lastrowid
    return jsonify({'id': rid})


@api_bp.route('/api/admin/rewards/<int:rid>', methods=['POST'])
def admin_update_reward(rid):
    _require_admin()
    data = request.get_json(force=True)
    with db_conn() as conn:
        reward = conn.execute('SELECT * FROM rewards WHERE id=?', (rid,)).fetchone()
        if not reward:
            return jsonify({'error': 'Introuvable'}), 404
        name  = data.get('name', reward['name'])
        desc  = data.get('description', reward['description'])
        cost  = data.get('token_cost', reward['token_cost'])
        stock = data.get('stock', reward['stock'])
        active = data.get('active', reward['active'])
        conn.execute(
            'UPDATE rewards SET name=?, description=?, token_cost=?, stock=?, active=? WHERE id=?',
            (name, desc, cost, stock, active, rid)
        )
        conn.commit()
    return jsonify({'ok': True})
