"""
conftest.py — fixtures pytest pour l'app casino Flask.

Isolation DB :
  Chaque test utilise un fichier SQLite temporaire distinct — pas :memory:
  car db_conn() rouvre la connexion à chaque appel (une DB :memory: serait
  vide à chaque réouverture).
  db_module.DATABASE est monkey-patché avant create_app() pour que tous les
  appels db_conn() dans l'app pointent vers le fichier temporaire du test.

Isolation client :
  admin_client, player_client, player2_client ont CHACUN leur propre
  app.test_client() indépendant — partager le même client_fixture ferait
  que le dernier login() écraserait le précédent.
"""

import os
import tempfile
import bcrypt
import pytest

import db as db_module
from app import create_app
from db import db_conn


# ─── Helpers internes (réexportés pour test_casino.py) ───────────────────────

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _create_user(db_path: str, username: str, password: str, role: str, tokens: int = 0) -> int:
    """Insère un utilisateur directement en DB, retourne son id."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    pw_hash = _hash(password)
    cur = conn.execute(
        'INSERT INTO users(username, password_hash, role, tokens) VALUES (?,?,?,?)',
        (username, pw_hash, role, tokens)
    )
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def _login(client, username: str, password: str):
    """Envoie un POST /login, retourne la réponse (sans follow_redirects)."""
    return client.post('/login',
                       data={'username': username, 'password': password},
                       follow_redirects=False)


# ─── Fixture de base ─────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """App Flask isolée : DB temporaire, CSRF désactivé, pas de scheduler."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    # Patch le chemin DB au niveau module AVANT create_app()
    original_db = db_module.DATABASE
    db_module.DATABASE = db_path

    flask_app = create_app()
    flask_app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SESSION_COOKIE_SECURE': False,
    })

    yield flask_app

    db_module.DATABASE = original_db
    for path in (db_path, db_path + '-wal', db_path + '-shm'):
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture
def client(app):
    """Client non authentifié — usage pour tests auth et routes publiques."""
    return app.test_client()


@pytest.fixture
def admin_client(app):
    """Client indépendant authentifié en tant qu'admin."""
    _create_user(db_module.DATABASE, 'admin', 'adminpass', 'admin', tokens=0)
    c = app.test_client()
    _login(c, 'admin', 'adminpass')
    return c


@pytest.fixture
def player_client(app):
    """Client indépendant authentifié en tant que joueur (1000 tokens)."""
    _create_user(db_module.DATABASE, 'player1', 'playerpass', 'player', tokens=1000)
    c = app.test_client()
    _login(c, 'player1', 'playerpass')
    return c


@pytest.fixture
def player2_client(app):
    """Second joueur indépendant (1000 tokens)."""
    _create_user(db_module.DATABASE, 'player2', 'player2pass', 'player', tokens=1000)
    c = app.test_client()
    _login(c, 'player2', 'player2pass')
    return c


@pytest.fixture
def open_session(app, admin_client):
    """Ouvre une game_session via l'API admin, retourne session_id."""
    r = admin_client.post('/api/admin/session/open',
                          json={},
                          headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, f"open_session fixture failed ({r.status_code}): {r.data}"
    with db_conn() as conn:
        active = conn.execute(
            "SELECT id FROM game_sessions WHERE status='open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert active is not None, "open_session: aucune session open trouvée"
    return active['id']


@pytest.fixture
def open_vote_session(app, admin_client):
    """Ouvre une vote_session 'Film Test' via l'API admin, retourne vote_session_id."""
    r = admin_client.post('/api/vote/open',
                          json={'film_title': 'Film Test'},
                          headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, f"open_vote_session fixture failed ({r.status_code}): {r.data}"
    return r.get_json()['vote_session_id']
