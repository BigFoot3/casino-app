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


def _create_reward(admin_client, name='Cadeau', cost=100, stock=5) -> int:
    r = admin_client.post('/api/admin/rewards',
                          json={'name': name, 'token_cost': cost, 'stock': stock},
                          headers={'X-CSRFToken': 'test'})
    assert r.status_code == 200, f"_create_reward failed: {r.data}"
    return r.get_json()['id']


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


def _vote(client, score: int, bonus: int):
    return client.post('/api/vote/submit',
                       json={'score': score, 'bonus_amount': bonus},
                       headers={'X-CSRFToken': 'test'})


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
# TestRewards
# ═══════════════════════════════════════════════════════════════════════════════

class TestRewards:

    def test_admin_give_reward(self, app, admin_client, player_client):
        """Admin give endpoint → stock -1, claim created, tokens untouched."""
        rid = _create_reward(admin_client, cost=100, stock=5)
        _set_tokens('player1', 200)
        r = admin_client.post('/api/admin/reward/give',
                              json={'username': 'player1', 'reward_id': rid},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        with db_conn() as conn:
            rw  = conn.execute('SELECT stock FROM rewards WHERE id=?', (rid,)).fetchone()
            uid = conn.execute('SELECT id FROM users WHERE username=?', ('player1',)).fetchone()['id']
            clm = conn.execute(
                'SELECT id FROM reward_claims WHERE user_id=? AND reward_id=?', (uid, rid)
            ).fetchone()
            tokens = conn.execute('SELECT tokens FROM users WHERE id=?', (uid,)).fetchone()['tokens']
        assert rw['stock'] == 4
        assert clm is not None
        assert tokens == 200

    def test_admin_give_reward_unknown_user(self, app, admin_client):
        """Unknown username → 404."""
        rid = _create_reward(admin_client, cost=10, stock=5)
        r = admin_client.post('/api/admin/reward/give',
                              json={'username': 'nobody', 'reward_id': rid},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 404

    def test_admin_give_reward_out_of_stock(self, app, admin_client, player_client):
        """stock=0 → 400."""
        rid = _create_reward(admin_client, cost=10, stock=0)
        r = admin_client.post('/api/admin/reward/give',
                              json={'username': 'player1', 'reward_id': rid},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_admin_give_reward_forbidden_player(self, app, player_client):
        """Player cannot call admin give endpoint."""
        r = player_client.post('/api/admin/reward/give',
                               json={'username': 'player1', 'reward_id': 1},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403


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
# TestVoteOpen
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteOpen:

    def test_open_vote_session_admin(self, app, admin_client):
        """Admin ouvre vote → vote_session créée, app_mode='vote'."""
        r = admin_client.post('/api/vote/open',
                              json={'film_title': 'Dune'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        assert _get_config('app_mode') == 'vote'
        with db_conn() as conn:
            vs = conn.execute("SELECT * FROM vote_sessions WHERE status='open'").fetchone()
        assert vs is not None
        assert vs['film_title'] == 'Dune'

    def test_open_vote_forbidden_player(self, app, player_client):
        """Player → 403."""
        r = player_client.post('/api/vote/open',
                               json={'film_title': 'Dune'},
                               headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_cannot_open_vote_if_already_open(self, app, admin_client, open_vote_session):
        """Deuxième ouverture → 400."""
        r = admin_client.post('/api/vote/open',
                              json={'film_title': 'Dune 2'},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_session_status_returns_app_mode_vote(self, app, client, open_vote_session):
        """Status retourne app_mode='vote' + film_title correct."""
        r = client.get('/api/session/status')
        d = r.get_json()
        assert d['app_mode'] == 'vote'
        assert d['vote_session'] is not None
        assert d['vote_session']['film_title'] == 'Film Test'


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteSubmit
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteSubmit:

    def test_vote_submit_no_bonus(self, app, player_client, open_vote_session):
        """score=7, bonus=0 → tokens inchangés, weighted_score=7.0."""
        tokens_before = _get_tokens('player1')
        r = _vote(player_client, 7, 0)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d['tokens_remaining'] == tokens_before
        assert d['weighted_score'] == 7.0

    def test_vote_submit_bonus_25(self, app, player_client, open_vote_session):
        """score=8, bonus=25 → tokens -25, weighted_score=12.0."""
        tokens_before = _get_tokens('player1')
        r = _vote(player_client, 8, 25)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d['tokens_remaining'] == tokens_before - 25
        assert d['weighted_score'] == 12.0

    def test_vote_submit_bonus_50(self, app, player_client, open_vote_session):
        """score=6, bonus=50 → tokens -50, weighted_score=12.0."""
        tokens_before = _get_tokens('player1')
        r = _vote(player_client, 6, 50)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d['tokens_remaining'] == tokens_before - 50
        assert d['weighted_score'] == 12.0

    def test_vote_modify_bonus_up(self, app, player_client, open_vote_session):
        """Modifier bonus 0→25 → delta -25 tokens."""
        _vote(player_client, 5, 0)
        tokens_mid = _get_tokens('player1')
        r = _vote(player_client, 5, 25)
        assert r.status_code == 200, r.get_json()
        assert _get_tokens('player1') == tokens_mid - 25

    def test_vote_modify_bonus_down(self, app, player_client, open_vote_session):
        """Modifier bonus 50→25 → remboursement +25."""
        _vote(player_client, 5, 50)
        tokens_mid = _get_tokens('player1')
        r = _vote(player_client, 5, 25)
        assert r.status_code == 200, r.get_json()
        assert _get_tokens('player1') == tokens_mid + 25

    def test_vote_modify_bonus_remove(self, app, player_client, open_vote_session):
        """Modifier bonus 50→0 → remboursement +50."""
        _vote(player_client, 5, 50)
        tokens_mid = _get_tokens('player1')
        r = _vote(player_client, 5, 0)
        assert r.status_code == 200, r.get_json()
        assert _get_tokens('player1') == tokens_mid + 50

    def test_vote_modify_score_only(self, app, player_client, open_vote_session):
        """Changer score sans changer bonus → tokens inchangés, weighted recalculé."""
        _vote(player_client, 5, 25)
        tokens_mid = _get_tokens('player1')
        r = _vote(player_client, 8, 25)
        assert r.status_code == 200, r.get_json()
        assert _get_tokens('player1') == tokens_mid
        assert r.get_json()['weighted_score'] == 12.0

    def test_vote_insufficient_tokens_for_bonus(self, app, player_client, open_vote_session):
        """Solde < 25 → 400 sur bonus=25, tokens inchangés."""
        _set_tokens('player1', 10)
        tokens_before = _get_tokens('player1')
        r = _vote(player_client, 7, 25)
        assert r.status_code == 400
        assert _get_tokens('player1') == tokens_before

    def test_vote_outside_open_session(self, app, player_client):
        """Soumettre sans vote_session ouverte → 400."""
        r = _vote(player_client, 7, 0)
        assert r.status_code == 400

    def test_vote_score_out_of_range(self, app, player_client, open_vote_session):
        """score=0 ou score=11 → 400."""
        assert _vote(player_client, 0, 0).status_code == 400
        assert _vote(player_client, 11, 0).status_code == 400

    def test_vote_invalid_bonus(self, app, player_client, open_vote_session):
        """bonus=30 → 400."""
        r = _vote(player_client, 7, 30)
        assert r.status_code == 400

    def test_vote_upsert_single_row(self, app, player_client, open_vote_session):
        """Voter deux fois → une seule ligne en DB (UPSERT)."""
        _vote(player_client, 5, 0)
        _vote(player_client, 8, 0)
        with db_conn() as conn:
            count = conn.execute(
                'SELECT COUNT(*) FROM votes WHERE vote_session_id=?', (open_vote_session,)
            ).fetchone()[0]
        assert count == 1

    def test_vote_delta_atomic_no_double_spend(self, app, player_client, open_vote_session):
        """Deux soumissions simultanées bonus=25 depuis un premier vote bonus=0
        → tokens débités au maximum -25 (pas -50).
        """
        _vote(player_client, 5, 0)
        tokens_after_first = _get_tokens('player1')

        results = []

        def do_vote():
            with app.test_client() as c:
                _login(c, 'player1', 'playerpass')
                r = _vote(c, 7, 25)
                results.append(r.status_code)

        t1 = threading.Thread(target=do_vote)
        t2 = threading.Thread(target=do_vote)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert 200 in results, f"Au moins une doit réussir, résultats={results}"
        final_tokens = _get_tokens('player1')
        # Delta max autorisé = -25 (une seule transition 0→25)
        assert final_tokens >= tokens_after_first - 25, \
            f"Double débit détecté : {tokens_after_first} → {final_tokens}"


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteClose
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteClose:

    def test_close_vote_session_admin(self, app, admin_client, open_vote_session):
        """Admin ferme vote → status='closed', app_mode='roulette'."""
        r = admin_client.post('/api/vote/close',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert _get_config('app_mode') == 'roulette'
        assert _get_config('current_vote_session_id') == ''
        with db_conn() as conn:
            vs = conn.execute(
                'SELECT status FROM vote_sessions WHERE id=?', (open_vote_session,)
            ).fetchone()
        assert vs['status'] == 'closed'

    def test_close_vote_forbidden_player(self, app, player_client, open_vote_session):
        """Player → 403."""
        r = player_client.post('/api/vote/close',
                               json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_close_vote_when_none_open(self, app, admin_client):
        """Fermer sans session ouverte → 400."""
        r = admin_client.post('/api/vote/close',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_vote_submit_rejected_after_close(self, app, admin_client, player_client, open_vote_session):
        """Soumettre après fermeture → 400."""
        admin_client.post('/api/vote/close', json={}, headers={'X-CSRFToken': 'test'})
        r = _vote(player_client, 7, 0)
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestVoteResults
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoteResults:

    def test_results_admin_only(self, app, player_client, open_vote_session):
        """Player → 403."""
        r = player_client.get(f'/api/vote/results?session_id={open_vote_session}')
        assert r.status_code == 403

    def test_results_correct_avg_weighted_score(self, app, admin_client, player_client,
                                                player2_client, open_vote_session):
        """3 votes variés → avg_weighted_score correct."""
        # player1 : score=7, bonus=0 → weighted=7.0
        _vote(player_client, 7, 0)
        # player2 : score=8, bonus=25 → weighted=12.0
        _vote(player2_client, 8, 25)
        # player3 : score=6, bonus=50 → weighted=12.0
        _create_user(db_module.DATABASE, 'player3', 'p3pass', 'player', tokens=1000)
        with app.test_client() as c3:
            _login(c3, 'player3', 'p3pass')
            _vote(c3, 6, 50)

        expected_avg = round((7.0 + 12.0 + 12.0) / 3, 2)
        r = admin_client.get(f'/api/vote/results?session_id={open_vote_session}')
        assert r.status_code == 200
        d = r.get_json()
        assert d['avg_weighted_score'] == expected_avg

    def test_results_voter_count(self, app, admin_client, player_client,
                                 player2_client, open_vote_session):
        """N votes → voter_count=N."""
        _vote(player_client, 7, 0)
        _vote(player2_client, 5, 0)
        r = admin_client.get(f'/api/vote/results?session_id={open_vote_session}')
        assert r.get_json()['voter_count'] == 2

    def test_results_bonus_breakdown(self, app, admin_client, player_client,
                                     player2_client, open_vote_session):
        """Répartition 0/25/50 comptée correctement."""
        _vote(player_client, 7, 0)
        _vote(player2_client, 8, 25)
        r = admin_client.get(f'/api/vote/results?session_id={open_vote_session}')
        bd = r.get_json()['bonus_breakdown']
        assert bd['0'] == 1
        assert bd['25'] == 1
        assert bd['50'] == 0

    def test_results_unknown_session(self, app, admin_client):
        """session_id inexistant → 404."""
        r = admin_client.get('/api/vote/results?session_id=99999')
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# TestPalmares
# ═══════════════════════════════════════════════════════════════════════════════

class TestPalmares:

    def _run_vote_session(self, admin_client, film_title: str, votes: list) -> int:
        """Ouvre un vote, fait voter, ferme. votes = [(client, score, bonus), ...]"""
        r = admin_client.post('/api/vote/open',
                              json={'film_title': film_title},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200, f"open failed: {r.data}"
        vsid = r.get_json()['vote_session_id']
        for c, score, bonus in votes:
            _vote(c, score, bonus)
        admin_client.post('/api/vote/close', json={}, headers={'X-CSRFToken': 'test'})
        return vsid

    def test_palmares_admin_only(self, app, player_client):
        """Player → 403."""
        r = player_client.post('/api/vote/palmares',
                               json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 403

    def test_palmares_sets_app_mode(self, app, admin_client):
        """POST /api/vote/palmares → app_mode='palmares'."""
        r = admin_client.post('/api/vote/palmares',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert _get_config('app_mode') == 'palmares'

    def test_palmares_blocked_if_vote_open(self, app, admin_client, open_vote_session):
        """Session vote ouverte → 400."""
        r = admin_client.post('/api/vote/palmares',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 400

    def test_summary_sorted_by_score_desc(self, app, admin_client, player_client):
        """3 films → retournés dans l'ordre décroissant de avg_weighted_score."""
        self._run_vote_session(admin_client, 'Film A', [(player_client, 9, 0)])  # avg=9.0
        self._run_vote_session(admin_client, 'Film B', [(player_client, 5, 0)])  # avg=5.0
        self._run_vote_session(admin_client, 'Film C', [(player_client, 7, 0)])  # avg=7.0
        r = admin_client.get('/api/vote/summary')
        assert r.status_code == 200
        titles = [f['film_title'] for f in r.get_json()]
        assert titles == ['Film A', 'Film C', 'Film B']

    def test_summary_admin_only(self, app, player_client):
        """Player → 403."""
        r = player_client.get('/api/vote/summary')
        assert r.status_code == 403

    def test_reset_mode_returns_to_roulette(self, app, admin_client):
        """POST /api/vote/reset-mode depuis palmares → app_mode='roulette'."""
        admin_client.post('/api/vote/palmares', json={}, headers={'X-CSRFToken': 'test'})
        assert _get_config('app_mode') == 'palmares'
        r = admin_client.post('/api/vote/reset-mode',
                              json={}, headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        assert _get_config('app_mode') == 'roulette'


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

    def test_create_reward_admin(self, app, admin_client):
        """Admin crée récompense → stock et token_cost corrects."""
        r = admin_client.post('/api/admin/rewards',
                              json={'name': 'Bière', 'token_cost': 75, 'stock': 10},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        rid = r.get_json()['id']
        with db_conn() as conn:
            rw = conn.execute('SELECT * FROM rewards WHERE id=?', (rid,)).fetchone()
        assert rw['token_cost'] == 75
        assert rw['stock'] == 10
        assert rw['active'] == 1

    def test_toggle_reward_active(self, app, admin_client):
        """Reward active → inactive → active."""
        rid = _create_reward(admin_client, cost=50, stock=5)

        # Désactiver
        r = admin_client.post(f'/api/admin/rewards/{rid}',
                              json={'active': 0},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            assert conn.execute('SELECT active FROM rewards WHERE id=?', (rid,)).fetchone()['active'] == 0

        # Réactiver
        r = admin_client.post(f'/api/admin/rewards/{rid}',
                              json={'active': 1},
                              headers={'X-CSRFToken': 'test'})
        assert r.status_code == 200
        with db_conn() as conn:
            assert conn.execute('SELECT active FROM rewards WHERE id=?', (rid,)).fetchone()['active'] == 1
