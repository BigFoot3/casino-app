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
        users  = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
        active = get_active_session(conn)
        cfg    = get_config(conn)
    return render_template('admin/index.html',
                           users=users,
                           active=active,
                           cfg=cfg)
