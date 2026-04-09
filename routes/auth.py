import bcrypt
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, current_app)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from db import db_conn

auth_bp = Blueprint('auth', __name__)


def _limiter():
    from app import limiter
    return limiter


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    from app import limiter

    @limiter.limit('10 per minute')
    def _post():
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with db_conn() as conn:
            user = conn.execute(
                'SELECT * FROM users WHERE username=?', (username,)
            ).fetchone()
        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            session.clear()
            session['user_id']   = user['id']
            session['username']  = user['username']
            session['role']      = user['role']
            session.permanent    = True
            if user['role'] == 'admin':
                return redirect(url_for('admin.index'))
            return redirect(url_for('player.dashboard'))
        flash('Identifiants incorrects.')
        return render_template('login.html')

    if request.method == 'POST':
        return _post()
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
