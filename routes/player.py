from datetime import datetime, timezone, timedelta
from flask import (Blueprint, render_template, session as flask_session,
                   redirect, url_for, g)

from db import db_conn, get_active_session

player_bp = Blueprint('player', __name__)


def _require_player():
    if 'user_id' not in flask_session:
        return redirect(url_for('auth.login'))
    return None


def _utcnow():
    return datetime.now(timezone.utc)


@player_bp.route('/dashboard')
def dashboard():
    redir = _require_player()
    if redir:
        return redir
    with db_conn() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE id=?', (flask_session['user_id'],)
        ).fetchone()
        active = get_active_session(conn)
        # Last result: most recent closed session within 60s
        last_result = None
        prev = conn.execute(
            "SELECT * FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if prev and prev['closed_at']:
            from db import _parse_dt
            closed_ago = (_utcnow() - _parse_dt(prev['closed_at'])).total_seconds()
            if closed_ago < 60:
                bet = conn.execute(
                    'SELECT * FROM bets WHERE session_id=? AND user_id=?',
                    (prev['id'], flask_session['user_id'])
                ).fetchone()
                last_result = {'session': prev, 'bet': bet}

    return render_template('dashboard.html',
                           user=user,
                           active=active,
                           last_result=last_result)


@player_bp.route('/play')
def play():
    redir = _require_player()
    if redir:
        return redir
    with db_conn() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE id=?', (flask_session['user_id'],)
        ).fetchone()
    return render_template('play.html', user=user)


@player_bp.route('/roulette/display')
def roulette_display():
    """Public fullscreen display page — no auth required."""
    return render_template('roulette/display.html')


@player_bp.route('/rewards')
def rewards():
    redir = _require_player()
    if redir:
        return redir
    with db_conn() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE id=?', (flask_session['user_id'],)
        ).fetchone()
        rewards = conn.execute(
            "SELECT * FROM rewards WHERE active=1 ORDER BY token_cost"
        ).fetchall()
    return render_template('rewards.html', user=user, rewards=rewards)
