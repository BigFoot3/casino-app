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
    """Top 3 winners / top 3 losers over closed sessions only.
    Excludes bets from sessions still open or spinning (payout not yet resolved).
    Filtered by stats_reset_at if set in app_config.
    """
    _Q = '''
        SELECT u.username, SUM(b.payout - b.amount) AS net
        FROM bets b
        JOIN users u          ON b.user_id    = u.id
        JOIN game_sessions gs ON b.session_id = gs.id
        WHERE gs.status = 'closed'
          AND gs.closed_at > ?
        GROUP BY b.user_id
        HAVING net {cmp} 0
        ORDER BY net {order}
        LIMIT 3
    '''
    with db_conn() as conn:
        cfg      = get_config(conn)
        # Default: epoch — all bets pass. Format matches SQLite datetime('now'): 'YYYY-MM-DD HH:MM:SS'
        reset_at = cfg.get('stats_reset_at', '1970-01-01 00:00:00')
        winners  = conn.execute(_Q.format(cmp='>', order='DESC'), (reset_at,)).fetchall()
        losers   = conn.execute(_Q.format(cmp='<', order='ASC'),  (reset_at,)).fetchall()
        h_rows   = conn.execute(
            "SELECT username, tokens FROM users WHERE role='player' ORDER BY tokens DESC LIMIT 10"
        ).fetchall()
    top_holders = [{'rank': i + 1, 'username': r['username'], 'tokens': r['tokens']}
                   for i, r in enumerate(h_rows)]
    return jsonify({
        'top_winners': [{'username': r['username'], 'net': r['net']} for r in winners],
        'top_losers':  [{'username': r['username'], 'net': r['net']} for r in losers],
        'top_holders': top_holders,
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
            '''SELECT u.username, (b.payout - b.amount) AS net
               FROM bets b JOIN users u ON b.user_id = u.id
               WHERE b.session_id = ?
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

        app_mode = cfg.get('app_mode', 'roulette')

        # Vote session info (only when app_mode == 'vote')
        vote_session = None
        if app_mode == 'vote':
            vsid = cfg.get('current_vote_session_id', '')
            if vsid:
                vs = conn.execute(
                    'SELECT id, film_title FROM vote_sessions WHERE id=?', (int(vsid),)
                ).fetchone()
                if vs:
                    voter_count = conn.execute(
                        'SELECT COUNT(*) FROM votes WHERE vote_session_id=?', (vs['id'],)
                    ).fetchone()[0]
                    vote_session = {
                        'id': vs['id'],
                        'film_title': vs['film_title'],
                        'voter_count': voter_count,
                    }

        if not active:
            return jsonify({'status': 'waiting', 'time_remaining_seconds': 0,
                            'winning_number': None,
                            'mode': cfg.get('auto_mode_enabled','0') == '1' and 'auto' or 'manual',
                            'auto_interval_seconds': int(cfg.get('auto_interval_seconds', 120)),
                            'app_mode': app_mode,
                            'vote_session': vote_session})

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
                        'app_mode': app_mode,
                        'vote_session': vote_session,
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
            'mode': active['mode'],
            'auto_interval_seconds': active['auto_interval_seconds'],
            'app_mode': app_mode,
            'vote_session': vote_session,
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
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 401
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


# ─── Vote ────────────────────────────────────────────────────────────────────

@api_bp.route('/api/vote/open', methods=['POST'])
def vote_open():
    _require_admin()
    data       = request.get_json(force=True)
    film_title = (data.get('film_title') or '').strip()
    if not film_title:
        return jsonify({'error': 'film_title requis'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            "SELECT id FROM vote_sessions WHERE status='open'"
        ).fetchone()
        if existing:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Une session de vote est déjà ouverte'}), 400

        cur = conn.execute(
            "INSERT INTO vote_sessions(film_title, status, opened_at) VALUES (?,?,?)",
            (film_title, 'open', _utcnow().isoformat())
        )
        new_id = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','vote')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('current_vote_session_id',?)",
            (str(new_id),)
        )
        conn.execute('COMMIT')

    return jsonify({'ok': True, 'vote_session_id': new_id})


@api_bp.route('/api/vote/close', methods=['POST'])
def vote_close():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        vsid = conn.execute(
            "SELECT value FROM app_config WHERE key='current_vote_session_id'"
        ).fetchone()
        if not vsid or not vsid['value']:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote ouverte'}), 400

        conn.execute(
            "UPDATE vote_sessions SET status='closed', closed_at=? WHERE id=? AND status='open'",
            (_utcnow().isoformat(), int(vsid['value']))
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','roulette')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('current_vote_session_id','')"
        )
        conn.execute('COMMIT')

    return jsonify({'ok': True})


@api_bp.route('/api/vote/submit', methods=['POST'])
def vote_submit():
    _require_login()
    data         = request.get_json(force=True)
    score        = data.get('score')
    bonus_amount = data.get('bonus_amount', 0)
    user_id      = flask_session['user_id']

    if not isinstance(score, int) or not (1 <= score <= 10):
        return jsonify({'error': 'score invalide (1–10)'}), 400
    if bonus_amount not in (0, 25, 50):
        return jsonify({'error': 'bonus_amount invalide (0, 25 ou 50)'}), 400

    multiplier = {0: 1.0, 25: 1.5, 50: 2.0}[bonus_amount]
    weighted   = round(score * multiplier, 4)

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')

        cfg = get_config(conn)
        if cfg.get('app_mode') != 'vote':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucun vote en cours'}), 400

        vsid_str = cfg.get('current_vote_session_id', '')
        if not vsid_str:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Aucune session de vote active'}), 400
        vsid = int(vsid_str)

        # Read existing vote (if any)
        existing_vote = conn.execute(
            'SELECT bonus_amount FROM votes WHERE vote_session_id=? AND user_id=?',
            (vsid, user_id)
        ).fetchone()
        ancien_bonus = existing_vote['bonus_amount'] if existing_vote else 0

        # delta: positive = refund, negative = deduction
        delta_tokens = ancien_bonus - bonus_amount

        user = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 401
        if user['tokens'] + delta_tokens < 0:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Solde insuffisant pour ce bonus'}), 400

        # UPSERT vote
        if existing_vote:
            conn.execute(
                '''UPDATE votes
                   SET score=?, bonus_amount=?, weighted_score=?, updated_at=?
                   WHERE vote_session_id=? AND user_id=?''',
                (score, bonus_amount, weighted, _utcnow().isoformat(), vsid, user_id)
            )
        else:
            conn.execute(
                '''INSERT INTO votes(vote_session_id, user_id, score, bonus_amount, weighted_score, updated_at)
                   VALUES (?,?,?,?,?,?)''',
                (vsid, user_id, score, bonus_amount, weighted, _utcnow().isoformat())
            )

        conn.execute(
            'UPDATE users SET tokens = tokens + ? WHERE id=?',
            (delta_tokens, user_id)
        )
        new_balance = conn.execute(
            'SELECT tokens FROM users WHERE id=?', (user_id,)
        ).fetchone()['tokens']
        conn.execute('COMMIT')

    return jsonify({'ok': True, 'tokens_remaining': new_balance, 'weighted_score': weighted})


@api_bp.route('/api/vote/results')
def vote_results():
    _require_admin()
    session_id = request.args.get('session_id', type=int)
    if not session_id:
        return jsonify({'error': 'session_id requis'}), 400

    with db_conn() as conn:
        vs = conn.execute(
            'SELECT id, film_title, status FROM vote_sessions WHERE id=?', (session_id,)
        ).fetchone()
        if not vs:
            return jsonify({'error': 'Session introuvable'}), 404

        rows = conn.execute(
            '''SELECT u.username, v.score, v.bonus_amount, v.weighted_score
               FROM votes v JOIN users u ON v.user_id = u.id
               WHERE v.vote_session_id=?
               ORDER BY u.username''',
            (session_id,)
        ).fetchall()

        voter_count = len(rows)
        avg_weighted = round(sum(r['weighted_score'] for r in rows) / voter_count, 2) if voter_count else 0

        bonus_breakdown = {0: 0, 25: 0, 50: 0}
        for r in rows:
            bonus_breakdown[r['bonus_amount']] += 1

    return jsonify({
        'film_title': vs['film_title'],
        'voter_count': voter_count,
        'avg_weighted_score': avg_weighted,
        'bonus_breakdown': bonus_breakdown,
        'votes': [dict(r) for r in rows],
    })


@api_bp.route('/api/vote/palmares', methods=['POST'])
def vote_palmares():
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        open_vs = conn.execute(
            "SELECT id FROM vote_sessions WHERE status='open'"
        ).fetchone()
        if open_vs:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Fermer le vote en cours avant d\'afficher le palmarès'}), 400
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','palmares')"
        )
        conn.execute('COMMIT')

    return jsonify({'ok': True})


@api_bp.route('/api/vote/reset-mode', methods=['POST'])
def vote_reset_mode():
    """Switch app_mode back to roulette (used from palmares state)."""
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('app_mode','roulette')"
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@api_bp.route('/api/vote/summary')
def vote_summary():
    _require_admin()
    with db_conn() as conn:
        sessions = conn.execute(
            "SELECT id, film_title, opened_at, closed_at FROM vote_sessions WHERE status='closed' ORDER BY id DESC"
        ).fetchall()
        result = []
        for vs in sessions:
            row = conn.execute(
                '''SELECT COUNT(*) as voter_count,
                          ROUND(AVG(weighted_score), 2) as avg_weighted_score
                   FROM votes WHERE vote_session_id=?''',
                (vs['id'],)
            ).fetchone()
            result.append({
                'id': vs['id'],
                'film_title': vs['film_title'],
                'voter_count': row['voter_count'],
                'avg_weighted_score': row['avg_weighted_score'] or 0,
            })
        result.sort(key=lambda x: x['avg_weighted_score'], reverse=True)

    return jsonify(result)


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
            (_utcnow().strftime('%Y-%m-%d %H:%M:%S'),)
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
        enabled = '1' if mode == 'auto' else '0'
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_mode_enabled',?)", (enabled,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('auto_interval_seconds',?)",
            (str(interval),)
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
        user = conn.execute('SELECT id, role FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        if user['role'] == 'admin':
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Impossible de supprimer un administrateur'}), 403
        # Delete associated data (no FK cascade defined)
        conn.execute('DELETE FROM reward_claims WHERE user_id=?', (uid,))
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
    if not isinstance(amount, int) or amount <= 0:
        return jsonify({'error': 'Montant invalide'}), 400
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            conn.execute('ROLLBACK')
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        conn.execute('UPDATE users SET tokens = tokens + ? WHERE id=?', (amount, uid))
        new_balance = conn.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()['tokens']
        conn.execute('COMMIT')
    return jsonify({'new_balance': new_balance})


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
