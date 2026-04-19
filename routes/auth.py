import bcrypt
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)

from db import db_conn
from extensions import limiter

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
def login():
    if request.method == 'POST':
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
            return redirect(url_for('player.play'))
        flash('Identifiants incorrects.')
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
