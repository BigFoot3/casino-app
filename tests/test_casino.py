"""
test_casino.py — tests d'intégration pour l'app casino.

Stratégie winning_number :
  Pour forcer le résultat d'un spin, on patche random.randint dans routes.api
  (c'est là qu'il est appelé dans admin_spin_session()) puis on appelle
  POST /api/admin/session/spin. resolve_spin() lit le winning_number déjà
  écrit par le spin, donc le patch suffit.

Bogues app documentés :
  - test_no_double_bet_same_session : l'app autorise les mises multiples par
    session (multi-bet frontend intentionnel). Marqué xfail strict.
"""

import io
import tempfile
import threading
from unittest.mock import patch

import bcrypt
import pytest

import db as db_module
from db import db_conn
from tests.conftest import _login, _create_user


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers locaux
# ═══════════════════════════════════════════════════════════════════════════════

def _get_tokens(username: str) -> int:
    with db_conn() as conn:
        row = conn.execute('SELECT tokens FROM users WHERE username=?', (username,)).fetchone()
    return row['tokens']


def _set_tokens(username: str, amount: int):
    with db_conn() as conn:
        conn.execute('UPDATE users SET tokens=? WHERE username=?', (amount, username))
        conn.commit()


def _force_spin(admin_client, winning_number: int):
    """Spin en forçant winning_number via patch de random.randint."""
    with patch('routes.api.random.randint', return_value=winning_number):
        r = admin_client.post('/api/admin/session/spin',
                              json={}, headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, f"_force_spin failed ({r.status_code}): {r.data}"


def _place_bet(client, bet_type='color', bet_value='red', amount=50):
    return client.post('/api/bet',
                       json={'bet_type': bet_type, 'bet_value': bet_value, 'amount': amount},
                       headers={'X-CSRFToken': 'test'})


def _get_config(key: str) -> str:
    with db_conn() as conn:
        row = conn.execute('SELECT value FROM app_config WHERE key=?', (key,)).fetchone()
    return row['value'] if row else None


def _insert_closed_session_with_bet(username: str, bet_type: str, bet_value: str,
                                    amount: int, payout: int, winning_number: int) -> int:
    """Insère directement une session fermée avec un bet résolu."""
    with db_conn() as conn:
        uid = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()['id']
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute(
            "INSERT INTO game_sessions(status, mode, winning_number, opened_at, closed_at) "
            "VALUES ('closed','manual',?,datetime('now'),datetime('now'))",
            (winning_number,)
        )
        sid = cur.lastrowid
        conn.execute(
            'INSERT INTO bets(session_id, user_id, bet_type, bet_value, amount, payout) '
            'VALUES (?,?,?,?,?,?)',
            (sid, uid, bet_type, bet_value, amount, payout)
        )
        conn.execute('COMMIT')
    return sid


# ═══════════════════════════════════════════════════════════════════════════════
# TestAuth
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_login_valid(self, client, app):
        """Credentials corrects → redirect dashboard, session active."""
        pw = bcrypt.hashpw(b'pass', bcrypt.gensalt()).decode()
        with db_conn() as conn:
            conn.execute(
                'INSERT INTO users(username,password_hash,role,tokens) VALUES (?,?,?,?)',
                ('u1', pw, 'player', 0)
            )
            conn.commit()
        r = client.post('/login', data={'username': 'u1', 'password': 'pass'},
                        follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get('user_id') is not None

    def test_login_invalid_password(self, client, app):
        """Mauvais mot de passe → 200 + flash, pas de session."""
        pw = bcrypt.hashpw(b'correct', bcrypt.gensalt()).decode()
        with db_conn() as conn:
            conn.execute(
                'INSERT INTO users(username,password_hash,role,tokens) VALUES (?,?,?,?)',
                ('u2', pw, 'player', 0)
            )
            conn.commit()
        r = client.post('/login', data={'username': 'u2', 'password': 'wrong'})
        assert r.status_code == 200
        with client.session_transaction() as sess:
            assert 'user_id' not in sess

    def test_login_unknown_user(self, client, app):
        """Utilisateur inexistant → même comportement que mauvais mot de passe."""
        r = client.post('/login', data={'username': 'nobody', 'password': 'x'})
        assert r.status_code == 200
        with client.session_transaction() as sess:
            assert 'user_id' not in sess

    def test_logout(self, player_client):
        """Logout → session détruite, redirect /login."""
        with player_client.session_transaction() as sess:
            assert 'user_id' in sess
        r = player_client.get('/logout', follow_redirects=False)
        assert r.status_code == 302
        assert '/login' in r.headers['Location']
        with player_client.session_transaction() as sess:
            assert 'user_id' not in sess

    def test_protected_route_redirects_unauthenticated(self, client):
        """GET /play sans session → redirect /login."""
        r = client.get('/play', follow_redirects=False)
        assert r.status_code == 302
        assert '/login' in r.headers['Location']


# ═══════════════════════════════════════════════════════════════════════════════
# TestRoulette
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoulette:

    def test_session_status_waiting(self, client):
        """Sans session ouverte → status='waiting'."""
        r = client.get('/api/session/status')
        assert r.status_code == 200
        assert r.get_json()['status'] == 'waiting'

    def test_open_session_admin(self, app, admin_client):
        """Admin ouvre session → status='open', opened_at non null."""
        r = admin_client.post('/api/admin/session/open',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            row = conn.execute(
                "SELECT status, opened_at FROM game_sessions WHERE status='open'"
            ).fetchone()
        assert row is not None
        assert row['opened_at'] is not None

    def test_open_session_forbidden_player(self, player_client):
        """Player → 403."""
        r = player_client.post('/api/admin/session/open',
                               json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_spin_session_admin(self, app, admin_client, open_session):
        """Spin admin → winning_number in 0..36, session closed."""
        _force_spin(admin_client, 15)
        with db_conn() as conn:
            row = conn.execute(
                "SELECT status, winning_number FROM game_sessions WHERE id=?",
                (open_session,)
            ).fetchone()
        assert row['status'] == 'closed'
        assert 0 <= row['winning_number'] <= 36

    def test_spin_forbidden_player(self, player_client, open_session):
        """Player → 403."""
        r = player_client.post('/api/admin/session/spin',
                               json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_cannot_open_two_sessions(self, app, admin_client, open_session):
        """Deuxième ouverture → 400 (session déjà active)."""
        r = admin_client.post('/api/admin/session/open',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestBets
# ═══════════════════════════════════════════════════════════════════════════════

class TestBets:

    def test_bet_color_red_win(self, app, admin_client, player_client, open_session):
        """Mise color=red, winning rouge → payout = amount×2."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'color', 'red', 50)
        assert r.status_code == 200
        _force_spin(admin_client, 3)   # 3 est rouge
        assert _get_tokens('player1') == tokens_before - 50 + 100  # net +50

    def test_bet_color_red_lose(self, app, admin_client, player_client, open_session):
        """Mise color=red, winning noir → payout=0."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'color', 'red', 50)
        assert r.status_code == 200
        _force_spin(admin_client, 2)   # 2 est noir
        assert _get_tokens('player1') == tokens_before - 50

    def test_bet_color_zero_house_win(self, app, admin_client, player_client, open_session):
        """winning_number=0 → house win, payout=0."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'color', 'red', 50)
        assert r.status_code == 200
        _force_spin(admin_client, 0)
        assert _get_tokens('player1') == tokens_before - 50

    def test_bet_parity_even_win(self, app, admin_client, player_client, open_session):
        """Mise parity=even, winning pair → ×2."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'parity', 'even', 50)
        assert r.status_code == 200
        _force_spin(admin_client, 4)   # 4 pair
        assert _get_tokens('player1') == tokens_before - 50 + 100

    def test_bet_parity_zero_house_win(self, app, admin_client, player_client, open_session):
        """winning_number=0 → house win même sur parity."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'parity', 'even', 50)
        assert r.status_code == 200
        _force_spin(admin_client, 0)
        assert _get_tokens('player1') == tokens_before - 50

    def test_bet_number_exact_win(self, app, admin_client, player_client, open_session):
        """Mise number=7, winning=7 → ×36."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'number', '7', 10)
        assert r.status_code == 200
        _force_spin(admin_client, 7)
        assert _get_tokens('player1') == tokens_before - 10 + 360

    def test_bet_number_exact_lose(self, app, admin_client, player_client, open_session):
        """Mise number=7, winning=8 → payout=0."""
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'number', '7', 10)
        assert r.status_code == 200
        _force_spin(admin_client, 8)
        assert _get_tokens('player1') == tokens_before - 10

    def test_bet_insufficient_tokens(self, app, player_client, open_session):
        """Mise > solde → 400, tokens inchangés."""
        _set_tokens('player1', 30)
        tokens_before = _get_tokens('player1')
        r = _place_bet(player_client, 'color', 'red', 100)
        assert r.status_code == 400
        assert _get_tokens('player1') == tokens_before

    def test_bet_zero_amount(self, app, player_client, open_session):
        """amount=0 → 400."""
        r = _place_bet(player_client, 'color', 'red', 0)
        assert r.status_code == 400

    def test_bet_negative_amount(self, app, player_client, open_session):
        """amount=-50 → 400."""
        r = _place_bet(player_client, 'color', 'red', -50)
        assert r.status_code == 400

    def test_bet_outside_open_window(self, app, player_client):
        """Mise hors session open → 400."""
        r = _place_bet(player_client, 'color', 'red', 50)
        assert r.status_code == 400

    @pytest.mark.xfail(
        strict=True,
        reason="BUG APP : l'app autorise les mises multiples par session (multi-bet "
               "frontend intentionnel). Contrainte UNIQUE(session_id,user_id) non implémentée."
    )
    def test_no_double_bet_same_session(self, app, player_client, open_session):
        """Deux mises identiques sur la même session → 400 sur la deuxième."""
        r1 = _place_bet(player_client, 'color', 'red', 50)
        assert r1.status_code == 200
        r2 = _place_bet(player_client, 'color', 'red', 50)
        assert r2.status_code == 400  # échoue → BUG APP

    def test_tokens_atomic_no_double_spend(self, app, admin_client, player_client, open_session):
        """Deux requêtes concurrentes de mise → tokens débités une seule fois.

        Solde = 100, amount = 100. Une seule mise peut passer (solde exact).
        WAL + BEGIN IMMEDIATE garantit l'atomicité.
        """
        _set_tokens('player1', 100)
        amount = 100

        results = []

        def do_bet():
            with app.test_client() as c:
                _login(c, 'player1', 'playerpass')
                r = _place_bet(c, 'color', 'red', amount)
                results.append(r.status_code)

        t1 = threading.Thread(target=do_bet)
        t2 = threading.Thread(target=do_bet)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results.count(200) == 1, f"Attendu 1 succès, résultats={results}"
        assert results.count(400) == 1
        assert _get_tokens('player1') == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestLeaderboard
# ═══════════════════════════════════════════════════════════════════════════════

class TestLeaderboard:

    def test_leaderboard_empty(self, client):
        """Sans bets → winners=[], losers=[]."""
        r = client.get('/api/leaderboard')
        d = r.get_json()
        assert d['top_winners'] == []
        assert d['top_losers'] == []

    def test_leaderboard_winners_populated(self, app, player_client):
        """Bets gagnants sur session closed → winners triés net DESC."""
        _insert_closed_session_with_bet('player1', 'color', 'red', 50, 100, 3)
        r = player_client.get('/api/leaderboard')
        d = r.get_json()
        assert len(d['top_winners']) >= 1
        assert d['top_winners'][0]['net'] > 0

    def test_leaderboard_losers_populated(self, app, player_client):
        """Bets perdants → losers triés net ASC."""
        _insert_closed_session_with_bet('player1', 'color', 'red', 50, 0, 2)
        r = player_client.get('/api/leaderboard')
        d = r.get_json()
        assert len(d['top_losers']) >= 1
        assert d['top_losers'][0]['net'] < 0

    def test_leaderboard_excludes_open_session(self, app, player_client, admin_client, open_session):
        """Bets sur session open → absents du leaderboard."""
        _place_bet(player_client, 'color', 'red', 50)
        r = player_client.get('/api/leaderboard')
        d = r.get_json()
        assert d['top_winners'] == []
        assert d['top_losers'] == []

    def test_leaderboard_excludes_spinning_session(self, app, player_client, admin_client, open_session):
        """Bets sur session spinning → absents du leaderboard."""
        _place_bet(player_client, 'color', 'red', 50)
        with db_conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                "UPDATE game_sessions SET status='spinning', winning_number=7 WHERE status='open'"
            )
            conn.execute('COMMIT')
        r = player_client.get('/api/leaderboard')
        d = r.get_json()
        assert d['top_winners'] == []
        assert d['top_losers'] == []

    def test_round_result_returns_null_when_not_closed(self, app, player_client, open_session):
        """Session open en cours → round_result ne retourne pas ses bets."""
        r = player_client.get('/api/session/round_result')
        d = r.get_json()
        # La session ouverte n'est pas dans round_result (qui ne regarde que 'closed')
        assert d.get('session_id') != open_session

    def test_round_result_after_spin(self, app, admin_client, player_client, open_session):
        """Après spin avec mise gagnante → round_result contient le bon net."""
        _place_bet(player_client, 'color', 'red', 50)
        _force_spin(admin_client, 3)   # rouge → win
        r = player_client.get('/api/session/round_result')
        d = r.get_json()
        assert d['session_id'] == open_session
        assert len(d['winners']) >= 1
        assert d['winners'][0]['net'] > 0

# ═══════════════════════════════════════════════════════════════════════════════
# TestAdminActions
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminActions:

    def _player_id(self) -> int:
        with db_conn() as conn:
            return conn.execute('SELECT id FROM users WHERE username=?', ('player1',)).fetchone()['id']

    def test_add_tokens_admin(self, app, admin_client, player_client):
        """Admin ajoute tokens → solde augmente."""
        uid = self._player_id()
        tokens_before = _get_tokens('player1')
        r = admin_client.post(f'/api/admin/users/{uid}/add-tokens',
                              json={'amount': 200},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['new_balance'] == tokens_before + 200
        assert _get_tokens('player1') == tokens_before + 200

    def test_add_tokens_forbidden_player(self, app, admin_client, player_client):
        """Player ne peut pas ajouter des tokens → 403."""
        uid = self._player_id()
        r = player_client.post(f'/api/admin/users/{uid}/add-tokens',
                               json={'amount': 100},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_reset_password_admin(self, app, admin_client, player_client):
        """Reset password → nouveau hash, ancien mot de passe invalide."""
        uid = self._player_id()
        r = admin_client.post(f'/api/admin/users/{uid}/reset-password',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        new_pw = r.get_json()['password']
        assert new_pw

        # Ancien mot de passe → login échoue
        with app.test_client() as c:
            r_old = c.post('/login',
                           data={'username': 'player1', 'password': 'playerpass'},
                           follow_redirects=False)
            # Soit redirect vers login (bad creds), soit 200 avec flash
            with c.session_transaction() as sess:
                assert 'user_id' not in sess

        # Nouveau mot de passe → login réussit
        with app.test_client() as c:
            r_new = c.post('/login',
                           data={'username': 'player1', 'password': new_pw},
                           follow_redirects=False)
            assert r_new.status_code == 302


# ═══════════════════════════════════════════════════════════════════════════════
# Vote — helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _create_category(admin_client, name):
    r = admin_client.post('/api/admin/vote/categories',
                          json={'name': name}, headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _add_film(admin_client, title, cat_id):
    r = admin_client.post('/api/admin/vote/films',
                          json={'title': title, 'category_id': cat_id},
                          headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _open_vote(admin_client):
    r = admin_client.post('/api/admin/vote/open',
                          json={}, headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['session_id']


def _close_vote(admin_client):
    r = admin_client.post('/api/admin/vote/close',
                          json={}, headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, r.get_json()


def _submit_rankings(player_client, cat_id, film_ids):
    rankings = [{'film_id': fid, 'rank': i + 1} for i, fid in enumerate(film_ids)]
    r = player_client.post('/api/vote/rankings',
                           json={'category_id': cat_id, 'rankings': rankings},
                           headers={'X-CSRFToken': 'test'})
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteCategories
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteCategories:
    def test_create_category_admin(self, app, admin_client):
        r = admin_client.post('/api/admin/vote/categories',
                              json={'name': 'Drame'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        d = r.get_json()
        assert d['name'] == 'Drame'
        assert 'id' in d

    def test_create_category_duplicate(self, app, admin_client):
        _create_category(admin_client, 'Action')
        r = admin_client.post('/api/admin/vote/categories',
                              json={'name': 'Action'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 409

    def test_create_category_forbidden_player(self, app, player_client):
        r = player_client.post('/api/admin/vote/categories',
                               json={'name': 'Comédie'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_delete_category_admin(self, app, admin_client):
        cid = _create_category(admin_client, 'Horreur')
        r = admin_client.post(f'/api/admin/vote/categories/{cid}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            row = conn.execute('SELECT id FROM vote_categories WHERE id=?', (cid,)).fetchone()
        assert row is None


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteFilms
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteFilms:
    def test_add_film_admin(self, app, admin_client):
        cid = _create_category(admin_client, 'SF')
        r = admin_client.post('/api/admin/vote/films',
                              json={'title': 'Interstellar', 'category_id': cid},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['title'] == 'Interstellar'

    def test_add_film_duplicate(self, app, admin_client):
        cid = _create_category(admin_client, 'Aventure')
        _add_film(admin_client, 'Avatar', cid)
        r = admin_client.post('/api/admin/vote/films',
                              json={'title': 'Avatar', 'category_id': cid},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 409

    def test_delete_film_admin(self, app, admin_client):
        cid = _create_category(admin_client, 'Western')
        fid = _add_film(admin_client, 'Django', cid)
        r = admin_client.post(f'/api/admin/vote/films/{fid}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            row = conn.execute('SELECT id FROM vote_films WHERE id=?', (fid,)).fetchone()
        assert row is None

    def test_delete_film_blocked_when_vote_open(self, app, admin_client):
        cid = _create_category(admin_client, 'Action')
        fid = _add_film(admin_client, 'Mad Max', cid)
        _open_vote(admin_client)
        r = admin_client.post(f'/api/admin/vote/films/{fid}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        assert 'ouvert' in r.get_json()['error'].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteSession
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteSession:
    def test_open_session_admin(self, app, admin_client):
        r = admin_client.post('/api/admin/vote/open',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert 'session_id' in r.get_json()
        with db_conn() as conn:
            cfg = conn.execute("SELECT value FROM app_config WHERE key='app_mode'").fetchone()
        assert cfg['value'] == 'vote'

    def test_open_session_twice(self, app, admin_client):
        _open_vote(admin_client)
        r = admin_client.post('/api/admin/vote/open',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_close_session_computes_points(self, app, admin_client, player_client):
        cid = _create_category(admin_client, 'Comédie')
        fid1 = _add_film(admin_client, 'Film A', cid)
        fid2 = _add_film(admin_client, 'Film B', cid)
        _open_vote(admin_client)
        _submit_rankings(player_client, cid, [fid1, fid2])
        _close_vote(admin_client)
        with db_conn() as conn:
            ranks = conn.execute(
                'SELECT points FROM vote_rankings ORDER BY rank'
            ).fetchall()
        assert len(ranks) == 2
        assert ranks[0]['points'] > ranks[1]['points']

    def test_palmares_after_close(self, app, admin_client):
        _open_vote(admin_client)
        _close_vote(admin_client)
        r = admin_client.post('/api/admin/vote/palmares',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            cfg = conn.execute("SELECT value FROM app_config WHERE key='app_mode'").fetchone()
        assert cfg['value'] == 'palmares'

    def test_reset_mode(self, app, admin_client):
        _open_vote(admin_client)
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.post('/api/admin/vote/reset-mode',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            cfg = conn.execute("SELECT value FROM app_config WHERE key='app_mode'").fetchone()
        assert cfg['value'] == 'roulette'


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteRankings
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteRankings:
    def _setup(self, admin_client):
        cid  = _create_category(admin_client, 'Thriller')
        fid1 = _add_film(admin_client, 'Film X', cid)
        fid2 = _add_film(admin_client, 'Film Y', cid)
        fid3 = _add_film(admin_client, 'Film Z', cid)
        _open_vote(admin_client)
        return cid, fid1, fid2, fid3

    def test_submit_rankings_valid(self, app, admin_client, player_client):
        cid, fid1, fid2, fid3 = self._setup(admin_client)
        r = _submit_rankings(player_client, cid, [fid1, fid2, fid3])
        assert r.status_code == 200

    def test_submit_rankings_invalid_sequence(self, app, admin_client, player_client):
        cid, fid1, fid2, fid3 = self._setup(admin_client)
        r = player_client.post('/api/vote/rankings',
                               json={'category_id': cid,
                                     'rankings': [{'film_id': fid1, 'rank': 1},
                                                  {'film_id': fid2, 'rank': 3}]},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_submit_rankings_wrong_category(self, app, admin_client, player_client):
        cid, fid1, fid2, fid3 = self._setup(admin_client)
        cid2 = _create_category(admin_client, 'Action2')
        fid_wrong = _add_film(admin_client, 'Wrong Film', cid2)
        r = player_client.post('/api/vote/rankings',
                               json={'category_id': cid,
                                     'rankings': [{'film_id': fid_wrong, 'rank': 1}]},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_submit_outside_session(self, app, admin_client, player_client):
        cid  = _create_category(admin_client, 'Romance')
        fid1 = _add_film(admin_client, 'Film R', cid)
        # No open vote session
        r = player_client.post('/api/vote/rankings',
                               json={'category_id': cid,
                                     'rankings': [{'film_id': fid1, 'rank': 1}]},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_upsert_rankings(self, app, admin_client, player_client):
        cid, fid1, fid2, fid3 = self._setup(admin_client)
        _submit_rankings(player_client, cid, [fid1, fid2, fid3])
        # Resubmit with different order
        r = _submit_rankings(player_client, cid, [fid3, fid1, fid2])
        assert r.status_code == 200
        with db_conn() as conn:
            rk = conn.execute(
                'SELECT rank FROM vote_rankings WHERE film_id=? ORDER BY rank',
                (fid3,)
            ).fetchone()
        assert rk['rank'] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteBoosts
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteBoosts:
    def _setup(self, admin_client, player_client):
        cid = _create_category(admin_client, 'Animation')
        _add_film(admin_client, 'Film A1', cid)
        _open_vote(admin_client)
        return cid

    def test_boost_25_deducts_tokens(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        before = _get_tokens('player1')
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 25},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        d = r.get_json()
        assert d['tokens_remaining'] == before - 25

    def test_boost_insufficient_tokens(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        _set_tokens('player1', 10)
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 25},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_boost_change_refunds_delta(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        player_client.post('/api/vote/boost',
                           json={'category_id': cid, 'amount': 50},
                           headers={'X-CSRFToken': 'test'})
        before_change = _get_tokens('player1')
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 25},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        d = r.get_json()
        # Going 50→25: refund 25
        assert d['tokens_remaining'] == before_change + 25

    def test_boost_reset_to_zero_refunds(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        player_client.post('/api/vote/boost',
                           json={'category_id': cid, 'amount': 25},
                           headers={'X-CSRFToken': 'test'})
        before = _get_tokens('player1')
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 0},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['tokens_remaining'] == before + 25

    def test_boost_custom_amount(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        before = _get_tokens('player1')
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 75},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['tokens_remaining'] == before - 75

    def test_boost_exceeds_max(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': 301},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        r2 = player_client.post('/api/vote/boost',
                                json={'category_id': cid, 'amount': 300},
                                headers={'X-CSRFToken': 'test'})
        assert r2.status_code == 200

    def test_boost_negative(self, app, admin_client, player_client):
        cid = self._setup(admin_client, player_client)
        r = player_client.post('/api/vote/boost',
                               json={'category_id': cid, 'amount': -10},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteState
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteState:
    def test_state_no_session(self, app, player_client):
        r = player_client.get('/api/vote/state')
        assert r.status_code == 200
        d = r.get_json()
        assert d['session'] is None

    def test_state_with_session_and_rankings(self, app, admin_client, player_client):
        cid  = _create_category(admin_client, 'Doc')
        fid1 = _add_film(admin_client, 'Doc A', cid)
        fid2 = _add_film(admin_client, 'Doc B', cid)
        _open_vote(admin_client)
        # Project the category so players can see it (U1 fix — only displayed cat returned)
        admin_client.post('/api/admin/vote/display-category',
                          json={'category_id': cid}, headers={'X-CSRFToken': 'test'})
        _submit_rankings(player_client, cid, [fid1, fid2])

        r = player_client.get('/api/vote/state')
        assert r.status_code == 200
        d = r.get_json()
        assert d['session']['status'] == 'open'
        cat = d['categories'][0]
        assert len(cat['user_rankings']) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteResults
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteResults:
    def test_results_empty_revealed(self, app, admin_client):
        cid  = _create_category(admin_client, 'Musical')
        _add_film(admin_client, 'Musical A', cid)
        _open_vote(admin_client)
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.get('/api/vote/results')
        assert r.status_code == 200
        d = r.get_json()
        assert d['categories'][0]['revealed'] is False
        assert d['categories'][0]['films'][0]['score'] is None

    def test_results_partial_reveal(self, app, admin_client, player_client):
        cid1 = _create_category(admin_client, 'CatA')
        cid2 = _create_category(admin_client, 'CatB')
        _add_film(admin_client, 'Film1', cid1)
        _add_film(admin_client, 'Film2', cid2)
        _open_vote(admin_client)
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        admin_client.post('/api/admin/vote/reveal-next', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.get('/api/vote/results')
        d = r.get_json()
        revealed_cats = [c for c in d['categories'] if c['revealed']]
        hidden_cats   = [c for c in d['categories'] if not c['revealed']]
        assert len(revealed_cats) == 1
        assert len(hidden_cats) == 1

    def test_results_score_calculation(self, app, admin_client, player_client):
        cid  = _create_category(admin_client, 'ScoreTest')
        fid1 = _add_film(admin_client, 'Top Film', cid)
        fid2 = _add_film(admin_client, 'Second Film', cid)
        _open_vote(admin_client)
        _submit_rankings(player_client, cid, [fid1, fid2])
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        admin_client.post('/api/admin/vote/reveal-next', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.get('/api/vote/results')
        d = r.get_json()
        cat = d['categories'][0]
        assert cat['revealed'] is True
        # Top film should be rank 1
        top = [f for f in cat['films'] if f['rank'] == 1][0]
        assert top['title'] == 'Top Film'
        assert top['score'] > 0

    def test_reveal_next_admin(self, app, admin_client):
        _create_category(admin_client, 'RevealMe')
        _open_vote(admin_client)
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.post('/api/admin/vote/reveal-next',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        d = r.get_json()
        assert d['category_name'] == 'RevealMe'
        assert d['all_revealed'] is True

    def test_reveal_next_all_revealed(self, app, admin_client):
        _create_category(admin_client, 'OnlyCat')
        _open_vote(admin_client)
        _close_vote(admin_client)
        admin_client.post('/api/admin/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        admin_client.post('/api/admin/vote/reveal-next', json={}, headers={'X-CSRFToken': 'test'})
        r = admin_client.post('/api/admin/vote/reveal-next',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestShop
# ═══════════════════════════════════════════════════════════════════════════════

class TestShop:

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _enable_shop(self, admin_client):
        r = admin_client.post('/api/admin/shop/shop_enabled',
                              json={'enabled': True},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200, f"_enable_shop failed: {r.data}"

    def _create_item(self, admin_client, name='Article Test', price=25.0, variants=None):
        r = admin_client.post('/api/admin/shop/items',
                              json={'name': name, 'price': price, 'variants': variants or []},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200, f"_create_item failed: {r.data}"
        return r.get_json()['item_id']

    def _create_variant(self, admin_client, item_id, size_label='M', stock=5):
        r = admin_client.post('/api/admin/shop/variants',
                              json={'item_id': item_id, 'size_label': size_label, 'stock': stock},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200, f"_create_variant failed: {r.data}"
        return r.get_json()['variant_id']

    def _place_order(self, client, variant_id, quantity=1,
                     first_name='Jean', last_name='Dupont', phone='0612345678'):
        return client.post('/api/shop/order',
                           json={
                               'first_name': first_name,
                               'last_name':  last_name,
                               'phone':      phone,
                               'lines':      [{'variant_id': variant_id, 'quantity': quantity}],
                           })

    @staticmethod
    def _minimal_png() -> bytes:
        """Génère un PNG 1×1 valide en mémoire (PIL déjà disponible via qrcode[pil])."""
        from PIL import Image as _Img
        buf = io.BytesIO()
        _Img.new('RGB', (1, 1), color=(255, 0, 0)).save(buf, format='PNG')
        return buf.getvalue()

    def _upload_image(self, admin_client, item_id, filename='test.png'):
        """Upload un PNG minimal valide — patche _SHOP_DIR vers un dossier temporaire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('routes.shop._SHOP_DIR', tmpdir):
                r = admin_client.post(
                    f'/api/admin/shop/items/{item_id}/image',
                    data={'image': (io.BytesIO(self._minimal_png()), filename)},
                    content_type='multipart/form-data',
                    headers={'X-CSRFToken': 'test'}
                )
        return r.status_code, r.get_json()

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_shop_disabled_by_default(self, app, client):
        """shop_enabled='0' par défaut — GET /shop retourne 200 dans tous les cas."""
        assert _get_config('shop_enabled') == '0'
        r = client.get('/shop')
        assert r.status_code == 200

    def test_toggle_shop_enabled(self, app, admin_client):
        """POST shop_enabled {enabled:true} → app_config shop_enabled='1'."""
        self._enable_shop(admin_client)
        assert _get_config('shop_enabled') == '1'

        r = admin_client.post('/api/admin/shop/shop_enabled',
                              json={'enabled': False},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert _get_config('shop_enabled') == '0'

    def test_create_item_admin(self, app, admin_client):
        """Création d'un article → 200, item_id retourné, ligne en DB."""
        r = admin_client.post('/api/admin/shop/items',
                              json={'name': 'T-Shirt', 'description': 'Coton bio',
                                    'price': 20.0, 'variants': []},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        d = r.get_json()
        assert d['ok'] is True
        assert isinstance(d['item_id'], int)
        with db_conn() as conn:
            row = conn.execute('SELECT name, price FROM shop_items WHERE id=?',
                               (d['item_id'],)).fetchone()
        assert row['name'] == 'T-Shirt'
        assert row['price'] == 20.0

    def test_create_variant(self, app, admin_client):
        """Création variante sur article existant → vérifiée en DB."""
        item_id    = self._create_item(admin_client, 'Hoodie')
        variant_id = self._create_variant(admin_client, item_id, 'L', stock=10)
        with db_conn() as conn:
            row = conn.execute(
                'SELECT size_label, stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()
        assert row['size_label'] == 'L'
        assert row['stock'] == 10

    def test_order_success_decrements_stock(self, app, client, admin_client):
        """Commande réussie → stock décrémenté, order_id retourné, ligne en DB."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Casquette')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=3)

        r = self._place_order(client, variant_id, quantity=2)
        assert r.status_code == 200
        d = r.get_json()
        assert d['ok'] is True
        assert isinstance(d['order_id'], int)

        with db_conn() as conn:
            v   = conn.execute(
                'SELECT stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()
            sol = conn.execute(
                'SELECT quantity FROM shop_order_lines WHERE order_id=?', (d['order_id'],)
            ).fetchone()
        assert v['stock'] == 1
        assert sol['quantity'] == 2

    def test_order_blocked_if_stock_zero(self, app, client, admin_client):
        """Commande sur variante stock=0 → 400."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Mug')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=0)

        r = self._place_order(client, variant_id, quantity=1)
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_order_invalid_phone(self, app, client, admin_client):
        """Téléphone hors format → 400."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Stylo')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        r = self._place_order(client, variant_id, phone='1234')
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_order_missing_first_name(self, app, client, admin_client):
        """Prénom vide → 400."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Badge')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        r = self._place_order(client, variant_id, first_name='')
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_order_empty_lines(self, app, client, admin_client):
        """Lignes vides → 400."""
        self._enable_shop(admin_client)
        r = client.post('/api/shop/order',
                        json={'first_name': 'Jean', 'last_name': 'Dupont',
                              'phone': '0612345678', 'lines': []})
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_delete_item_blocked_if_orders_exist(self, app, client, admin_client):
        """Suppression article impossible si commandes existent → 400."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Carnet')
        variant_id = self._create_variant(admin_client, item_id, 'A5', stock=5)
        self._place_order(client, variant_id)

        r = admin_client.post(f'/api/admin/shop/items/{item_id}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_admin_order_status_change(self, app, client, admin_client):
        """Changement statut commande confirmed → vérifié en DB."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Tote bag')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)
        order_id   = self._place_order(client, variant_id).get_json()['order_id']

        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'confirmed'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        with db_conn() as conn:
            row = conn.execute('SELECT status FROM shop_orders WHERE id=?', (order_id,)).fetchone()
        assert row['status'] == 'confirmed'

    def test_shop_public_no_auth_required(self, app, client):
        """GET /shop accessible sans authentification → 200."""
        r = client.get('/shop')
        assert r.status_code == 200

    def test_order_public_no_auth_required(self, app, client, admin_client):
        """POST /api/shop/order accessible sans authentification — pas de 401/403."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, "Pin's")
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        # client est non authentifié (fixture sans login)
        r = self._place_order(client, variant_id)
        assert r.status_code not in (401, 403)
        assert r.status_code == 200

    def test_order_blocked_if_shop_disabled(self, app, client, admin_client):
        """Commande rejetée si boutique fermée (shop_enabled='0')."""
        # shop non activée — état par défaut
        item_id    = self._create_item(admin_client, 'Décapsuleur')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        r = self._place_order(client, variant_id)
        assert r.status_code == 400
        d = r.get_json()
        assert d['ok'] is False
        assert 'fermée' in d['error']

    def test_cancel_order_restores_stock(self, app, client, admin_client):
        """Annulation d'une commande pending → stock restitué."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Carafe')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)
        order_id   = self._place_order(client, variant_id, quantity=3).get_json()['order_id']

        with db_conn() as conn:
            before = conn.execute(
                'SELECT stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()['stock']
        assert before == 2  # 5 - 3

        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'cancelled'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            after = conn.execute(
                'SELECT stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()['stock']
        assert after == 5  # stock restitué

    def test_cancel_already_cancelled_no_double_credit(self, app, client, admin_client):
        """Re-annuler une commande déjà annulée → stock non crédité deux fois."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Lampe')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=4)
        order_id   = self._place_order(client, variant_id, quantity=2).get_json()['order_id']

        # Première annulation
        admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                          json={'status': 'cancelled'}, headers={'X-CSRFToken': 'test'})

        with db_conn() as conn:
            stock_after_first = conn.execute(
                'SELECT stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()['stock']
        assert stock_after_first == 4  # restitué une fois

        # Deuxième annulation — ne doit rien changer
        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'cancelled'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200

        with db_conn() as conn:
            stock_after_second = conn.execute(
                'SELECT stock FROM shop_variants WHERE id=?', (variant_id,)
            ).fetchone()['stock']
        assert stock_after_second == 4  # inchangé — pas de double crédit

    def test_confirm_cancelled_order_decrements_stock(self, app, client, admin_client):
        """Confirmer une commande annulée → stock décrémenté."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Réveil')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=6)
        order_id   = self._place_order(client, variant_id, quantity=2).get_json()['order_id']

        # Annuler → stock restitué (stock=6)
        admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                          json={'status': 'cancelled'}, headers={'X-CSRFToken': 'test'})

        with db_conn() as conn:
            s = conn.execute('SELECT stock FROM shop_variants WHERE id=?',
                             (variant_id,)).fetchone()['stock']
        assert s == 6

        # Confirmer → stock décrémenté
        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'confirmed'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            s = conn.execute('SELECT stock FROM shop_variants WHERE id=?',
                             (variant_id,)).fetchone()['stock']
        assert s == 4  # 6 - 2

    def test_cancel_preorder_order_does_not_change_stock(self, app, client, admin_client):
        """Annuler une commande preorder ne doit PAS modifier le stock."""
        self._enable_shop(admin_client)
        item_id = admin_client.post('/api/admin/shop/items',
                                    json={'name': 'Préco', 'price': 10, 'preorder': 1, 'variants': []},
                                    headers={'X-CSRFToken': 'test'}).get_json()['item_id']
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=0)
        order_id = self._place_order(client, variant_id, quantity=2).get_json()['order_id']

        with db_conn() as conn:
            stock_before = conn.execute('SELECT stock FROM shop_variants WHERE id=?',
                                        (variant_id,)).fetchone()['stock']

        admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                          json={'status': 'cancelled'}, headers={'X-CSRFToken': 'test'})

        with db_conn() as conn:
            stock_after = conn.execute('SELECT stock FROM shop_variants WHERE id=?',
                                       (variant_id,)).fetchone()['stock']
        assert stock_after == stock_before  # stock inchangé pour preorder

    def test_reconfirm_preorder_order_does_not_decrement_stock(self, app, client, admin_client):
        """Reconfirmer une commande preorder annulée ne doit PAS décrémenter le stock."""
        self._enable_shop(admin_client)
        item_id = admin_client.post('/api/admin/shop/items',
                                    json={'name': 'Préco2', 'price': 10, 'preorder': 1, 'variants': []},
                                    headers={'X-CSRFToken': 'test'}).get_json()['item_id']
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=0)
        order_id = self._place_order(client, variant_id, quantity=2).get_json()['order_id']

        admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                          json={'status': 'cancelled'}, headers={'X-CSRFToken': 'test'})
        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'confirmed'}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            stock = conn.execute('SELECT stock FROM shop_variants WHERE id=?',
                                 (variant_id,)).fetchone()['stock']
        assert stock == 0  # stock inchangé pour preorder

    def test_order_phone_international_format(self, app, client, admin_client):
        """Téléphone +33XXXXXXXXX normalisé et accepté."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Agenda')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        r = self._place_order(client, variant_id, phone='+33612345678')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        # Vérifier que le numéro est stocké en format local normalisé
        with db_conn() as conn:
            order = conn.execute(
                'SELECT phone FROM shop_orders WHERE id=?', (r.get_json()['order_id'],)
            ).fetchone()
        assert order['phone'] == '0612345678'

    def test_order_phone_0033_format(self, app, client, admin_client):
        """Téléphone 0033XXXXXXXXX normalisé et accepté."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Carnet')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)

        r = self._place_order(client, variant_id, phone='0033612345678')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            order = conn.execute(
                'SELECT phone FROM shop_orders WHERE id=?', (r.get_json()['order_id'],)
            ).fetchone()
        assert order['phone'] == '0612345678'

    def test_delete_item_allowed_if_all_orders_cancelled(self, app, client, admin_client):
        """Suppression article autorisée si toutes les commandes sont annulées → 200."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Porte-clés')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=3)
        order_id   = self._place_order(client, variant_id).get_json()['order_id']

        # Annuler la commande
        r = admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                              json={'status': 'cancelled'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200

        # Suppression doit maintenant réussir
        r = admin_client.post(f'/api/admin/shop/items/{item_id}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            row = conn.execute('SELECT id FROM shop_items WHERE id=?', (item_id,)).fetchone()
        assert row is None

    def test_delete_item_still_blocked_with_active_orders(self, app, client, admin_client):
        """Suppression article toujours bloquée si commande pending/confirmed existe → 400."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Stylo')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=5)
        self._place_order(client, variant_id)  # commande pending, non annulée

        r = admin_client.post(f'/api/admin/shop/items/{item_id}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_has_orders_flag_excludes_cancelled(self, app, client, admin_client):
        """GET /api/admin/shop/items — has_orders=False si seules commandes annulées."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Badge')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=2)
        order_id   = self._place_order(client, variant_id).get_json()['order_id']

        admin_client.post(f'/api/admin/shop/orders/{order_id}/status',
                          json={'status': 'cancelled'},
                          headers={'X-CSRFToken': 'test'})

        r = admin_client.get('/api/admin/shop/items')
        assert r.status_code == 200
        items = r.get_json()
        target = next(i for i in items if i['id'] == item_id)
        assert target['has_orders'] is False
        assert target['variants'][0]['has_orders'] is False

    def test_update_item_price(self, app, admin_client):
        """POST /api/admin/shop/items/<id>/price — mise à jour prix persistée en DB."""
        item_id = self._create_item(admin_client, 'Article Prix', price=25.0)
        r = admin_client.post(f'/api/admin/shop/items/{item_id}/price',
                              json={'price': 30.0},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        items = admin_client.get('/api/admin/shop/items').get_json()
        target = next(i for i in items if i['id'] == item_id)
        assert target['price'] == 30.0

    def test_update_item_price_invalid(self, app, admin_client):
        """POST /api/admin/shop/items/<id>/price — prix négatif → 400."""
        item_id = self._create_item(admin_client, 'Article Prix 2', price=25.0)
        r = admin_client.post(f'/api/admin/shop/items/{item_id}/price',
                              json={'price': -5},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_create_item_with_preorder(self, app, admin_client):
        """POST /api/admin/shop/items {preorder:1} — preorder=1 persisté en DB."""
        r = admin_client.post('/api/admin/shop/items',
                              json={'name': 'Article Précommande', 'preorder': 1, 'variants': []},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        item_id = r.get_json()['item_id']
        items = admin_client.get('/api/admin/shop/items').get_json()
        target = next(i for i in items if i['id'] == item_id)
        assert target['preorder'] == 1

    def test_toggle_preorder(self, app, admin_client):
        """POST /api/admin/shop/items/<id>/preorder — bascule preorder 0→1 en DB."""
        item_id = self._create_item(admin_client, 'Article Standard')
        items = admin_client.get('/api/admin/shop/items').get_json()
        assert next(i for i in items if i['id'] == item_id)['preorder'] == 0

        r = admin_client.post(f'/api/admin/shop/items/{item_id}/preorder',
                              json={'preorder': True},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['preorder'] == 1

        items = admin_client.get('/api/admin/shop/items').get_json()
        assert next(i for i in items if i['id'] == item_id)['preorder'] == 1

    def test_order_preorder_ignores_stock(self, app, client, admin_client):
        """Commande article preorder avec stock=0 → 200, stock inchangé."""
        self._enable_shop(admin_client)
        r = admin_client.post('/api/admin/shop/items',
                              json={'name': 'T-Shirt Préco', 'preorder': 1, 'variants': []},
                              headers={'X-CSRFToken': 'test'})
        item_id    = r.get_json()['item_id']
        variant_id = self._create_variant(admin_client, item_id, 'M', stock=0)

        r = self._place_order(client, variant_id)
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        variants = admin_client.get('/api/admin/shop/items').get_json()
        target_item = next(i for i in variants if i['id'] == item_id)
        assert target_item['variants'][0]['stock'] == 0

    def test_order_standard_still_blocked_if_stock_zero(self, app, client, admin_client):
        """Commande article standard avec stock=0 → 400 (comportement inchangé)."""
        self._enable_shop(admin_client)
        item_id    = self._create_item(admin_client, 'Article Standard Épuisé')
        variant_id = self._create_variant(admin_client, item_id, 'Unique', stock=0)

        r = self._place_order(client, variant_id)
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_update_item_name(self, app, admin_client):
        """POST /api/admin/shop/items/<id>/name — mise à jour nom persistée en DB."""
        item_id = self._create_item(admin_client, 'Ancien nom')
        r = admin_client.post(f'/api/admin/shop/items/{item_id}/name',
                              json={'name': 'Nouveau nom'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert data['name'] == 'Nouveau nom'
        items = admin_client.get('/api/admin/shop/items').get_json()
        target = next(i for i in items if i['id'] == item_id)
        assert target['name'] == 'Nouveau nom'

    def test_update_item_name_empty(self, app, admin_client):
        """POST /api/admin/shop/items/<id>/name {name:'   '} → 400."""
        item_id = self._create_item(admin_client, 'Article Nom Test')
        r = admin_client.post(f'/api/admin/shop/items/{item_id}/name',
                              json={'name': '   '},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_upload_invalid_image_rejected(self, app, admin_client):
        """Upload d'un fichier non-image avec extension .png → 400 (magic bytes invalides)."""
        item_id = self._create_item(admin_client, 'Article Fake')
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('routes.shop._SHOP_DIR', tmpdir):
                r = admin_client.post(
                    f'/api/admin/shop/items/{item_id}/image',
                    data={'image': (io.BytesIO(b'not-an-image-data'), 'fake.png')},
                    content_type='multipart/form-data',
                    headers={'X-CSRFToken': 'test'}
                )
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_upload_first_image_sets_primary(self, app, admin_client):
        """Premier upload → is_primary=1 en DB, image_path de shop_items mis à jour."""
        item_id = self._create_item(admin_client, 'Article Image Test')
        status, data = self._upload_image(admin_client, item_id)
        assert status == 200
        assert data['ok'] is True
        img = data['image']
        assert img['is_primary'] == 1
        with db_conn() as conn:
            row = conn.execute(
                "SELECT is_primary FROM shop_item_images WHERE id=?", (img['id'],)
            ).fetchone()
            assert row['is_primary'] == 1
            item_row = conn.execute(
                "SELECT image_path FROM shop_items WHERE id=?", (item_id,)
            ).fetchone()
            assert item_row['image_path'] == img['image_path']

    def test_upload_max_5_images(self, app, admin_client):
        """5 uploads réussis → 200 ; 6ème → 400."""
        item_id = self._create_item(admin_client, 'Article 5 Photos')
        for i in range(5):
            status, data = self._upload_image(admin_client, item_id, f'img{i}.jpg')
            assert status == 200, f"Upload {i + 1}/5 échoué : {data}"
        status, data = self._upload_image(admin_client, item_id, 'img5.jpg')
        assert status == 400
        assert data['ok'] is False

    def test_delete_primary_image_promotes_next(self, app, admin_client):
        """Suppression de l'image principale → la suivante promue principale,
        image_path de shop_items mis à jour."""
        item_id = self._create_item(admin_client, 'Article Promo')
        _, d1 = self._upload_image(admin_client, item_id, 'first.jpg')
        _, d2 = self._upload_image(admin_client, item_id, 'second.jpg')
        id1 = d1['image']['id']
        id2 = d2['image']['id']

        r = admin_client.post(f'/api/admin/shop/images/{id1}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert data['new_primary_id'] == id2

        with db_conn() as conn:
            assert conn.execute(
                "SELECT is_primary FROM shop_item_images WHERE id=?", (id2,)
            ).fetchone()['is_primary'] == 1
            img2_path = conn.execute(
                "SELECT image_path FROM shop_item_images WHERE id=?", (id2,)
            ).fetchone()['image_path']
            assert conn.execute(
                "SELECT image_path FROM shop_items WHERE id=?", (item_id,)
            ).fetchone()['image_path'] == img2_path

    def test_delete_last_image_clears_image_path(self, app, admin_client):
        """Suppression de la seule image → image_path=NULL dans shop_items."""
        item_id = self._create_item(admin_client, 'Article Seul')
        _, d = self._upload_image(admin_client, item_id, 'solo.jpg')
        img_id = d['image']['id']

        r = admin_client.post(f'/api/admin/shop/images/{img_id}/delete',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        with db_conn() as conn:
            assert conn.execute(
                "SELECT image_path FROM shop_items WHERE id=?", (item_id,)
            ).fetchone()['image_path'] is None

    def test_set_primary_image(self, app, admin_client):
        """POST /images/<id2>/set_primary → is_primary=1 sur id2, 0 sur id1,
        image_path de shop_items mis à jour."""
        item_id = self._create_item(admin_client, 'Article Set Primary')
        _, d1 = self._upload_image(admin_client, item_id, 'img1.jpg')
        _, d2 = self._upload_image(admin_client, item_id, 'img2.jpg')
        id1 = d1['image']['id']
        id2 = d2['image']['id']

        r = admin_client.post(f'/api/admin/shop/images/{id2}/set_primary',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        with db_conn() as conn:
            assert conn.execute(
                "SELECT is_primary FROM shop_item_images WHERE id=?", (id1,)
            ).fetchone()['is_primary'] == 0
            assert conn.execute(
                "SELECT is_primary FROM shop_item_images WHERE id=?", (id2,)
            ).fetchone()['is_primary'] == 1
            img2_path = conn.execute(
                "SELECT image_path FROM shop_item_images WHERE id=?", (id2,)
            ).fetchone()['image_path']
            assert conn.execute(
                "SELECT image_path FROM shop_items WHERE id=?", (item_id,)
            ).fetchone()['image_path'] == img2_path

    def test_images_included_in_shop_items_response(self, app, admin_client, client):
        """GET /api/shop/items → images[] présent et non vide après upload."""
        self._enable_shop(admin_client)
        item_id = self._create_item(admin_client, 'Article Images API')
        self._upload_image(admin_client, item_id, 'api.jpg')

        r = client.get('/api/shop/items')
        assert r.status_code == 200
        items = r.get_json()
        target = next((i for i in items if i['id'] == item_id), None)
        assert target is not None
        assert 'images' in target
        assert len(target['images']) == 1
        assert target['images'][0]['is_primary'] == 1

