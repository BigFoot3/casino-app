from flask import (Blueprint, render_template, session as flask_session,
                   redirect, url_for)

from db import db_conn, get_active_session, get_config

admin_bp = Blueprint('admin', __name__)


def _require_admin():
    if flask_session.get('role') != 'admin':
        return redirect(url_for('auth.login'))
    return None


@admin_bp.route('/admin')
def index():
    redir = _require_admin()
    if redir:
        return redir
    with db_conn() as conn:
        users    = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
        rewards  = conn.execute('SELECT * FROM rewards ORDER BY id').fetchall()
        active   = get_active_session(conn)
        cfg      = get_config(conn)
        app_mode = cfg.get('app_mode', 'roulette')
        # Current open vote session (if any)
        vsid_str = cfg.get('current_vote_session_id', '')
        current_vote = None
        if vsid_str:
            current_vote = conn.execute(
                'SELECT id, film_title FROM vote_sessions WHERE id=?', (int(vsid_str),)
            ).fetchone()
    return render_template('admin/index.html',
                           users=users,
                           rewards=rewards,
                           active=active,
                           cfg=cfg,
                           app_mode=app_mode,
                           current_vote=current_vote)
