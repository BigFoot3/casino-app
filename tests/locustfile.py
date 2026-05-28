"""
Test de montée en charge — Casino Kryptide
100 joueurs simultanés, soirée événement.

Scénarios disponibles :
  CasinoPlayer (défaut) — joueur réaliste : polling + mise + navigation
  BetStorm              — pic de mises simultanées lors d'une ouverture de session
  PollingOnly           — baseline polling status (sans action)
  VotePolling           — baseline polling /api/vote/display-state (sans auth)
  VotePlayer            — joueur réaliste en phase vote : rankings + boost

Usage :
    cd /root/casino && source venv/bin/activate

    # Test headless — 100 users, spawn 10/s, durée 3min
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
           --users=100 --spawn-rate=10 --run-time=3m --headless \
           --class-picker CasinoPlayer

    # Interface web (localhost:8089)
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000

    # Pic de mises uniquement (100 users, spawn rapide)
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
           --users=100 --spawn-rate=50 --run-time=2m --headless \
           --class-picker BetStorm

    # Baseline polling status seul
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
           --users=100 --spawn-rate=10 --run-time=2m --headless \
           --class-picker PollingOnly

    # Baseline polling vote display (public, sans auth)
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
           --users=100 --spawn-rate=10 --run-time=2m --headless \
           --class-picker VotePolling

    # Joueur réaliste en phase vote (rankings + boost)
    locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
           --users=100 --spawn-rate=10 --run-time=3m --headless \
           --class-picker VotePlayer

Prérequis :
    python tests/setup_load_test.py   # crée les 100 comptes de test

Notes .env pour les tests locaux (HTTP) :
    FLASK_ENV=development      # désactive SESSION_COOKIE_SECURE
    RATELIMIT_ENABLED=false    # désactive le rate limit /login
"""

import json
import os
import re
import random
import threading
from locust import HttpUser, task, between, events

# --- Credentials -----------------------------------------------------------

CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_test_users.json")
_creds = []
_cred_lock = threading.Lock()
_cred_index = 0


def _load_credentials():
    global _creds
    if not os.path.exists(CRED_FILE):
        raise FileNotFoundError(
            f"Fichier de credentials introuvable : {CRED_FILE}\n"
            "Lancez d'abord : python tests/setup_load_test.py"
        )
    with open(CRED_FILE) as f:
        _creds = json.load(f)
    print(f"[setup] {len(_creds)} comptes de test chargés.")


def _next_credentials():
    """Distribue les credentials en round-robin (thread-safe)."""
    global _cred_index
    with _cred_lock:
        creds = _creds[_cred_index % len(_creds)]
        _cred_index += 1
    return creds


@events.init.add_listener
def on_locust_init(environment, **_kwargs):
    _load_credentials()


# --- Helpers ---------------------------------------------------------------

def _extract_csrf(html: str) -> str | None:
    """Extrait le token CSRF depuis la balise <meta name='csrf-token'>."""
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if m:
        return m.group(1)
    # Fallback : champ hidden dans le formulaire de login
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


BET_TYPES = [
    ("color", "red"),
    ("color", "black"),
    ("parity", "even"),
    ("parity", "odd"),
]


# ---------------------------------------------------------------------------
# Scénario principal : joueur réaliste
# ---------------------------------------------------------------------------

class CasinoPlayer(HttpUser):
    """
    Simule un joueur lors d'une soirée :
    - Se connecte au démarrage
    - Polle le statut toutes les 2–5 secondes (comportement JS)
    - Place une mise quand une session est ouverte (1 fois sur 3)
    - Consulte son dashboard et ses récompenses de temps en temps
    """

    wait_time = between(2, 5)

    def on_start(self):
        self.csrf = None
        self.logged_in = False
        self.creds = _next_credentials()
        self._login()

    # ---- Auth ----

    def _login(self):
        r = self.client.get("/login")
        csrf = _extract_csrf(r.text)
        if not csrf:
            return

        with self.client.post(
            "/login",
            data={
                "username": self.creds["username"],
                "password": self.creds["password"],
                "csrf_token": csrf,
            },
            allow_redirects=True,
            catch_response=True,
            name="/login [POST]",
        ) as r:
            if r.status_code == 200 and "Identifiants incorrects" not in r.text:
                self.csrf = _extract_csrf(r.text)
                self.logged_in = True
            else:
                r.failure(f"Login échoué pour {self.creds['username']} (HTTP {r.status_code})")

    def _refresh_csrf(self):
        """Recharge le CSRF depuis /play si nécessaire."""
        r = self.client.get("/play", name="/play [csrf refresh]")
        if r.status_code == 200:
            self.csrf = _extract_csrf(r.text)

    # ---- Tâches ----

    @task(15)
    def poll_status(self):
        """Polling principal — émule le JS qui tourne toutes les 3s."""
        with self.client.get(
            "/api/session/status",
            catch_response=True,
            name="/api/session/status",
        ) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
                return
            try:
                data = r.json()
            except Exception:
                r.failure("JSON invalide")
                return
            # Tente une mise si la session est ouverte
            if data.get("status") == "open" and random.random() < 0.33:
                self._place_bet(data.get("session_id"))

    @task(3)
    def visit_dashboard(self):
        self.client.get("/dashboard", name="/dashboard")

    # @task(1) — /rewards supprimé (template + route retirés du projet)
    # def visit_rewards(self): self.client.get("/rewards", name="/rewards")

    @task(2)
    def visit_play(self):
        r = self.client.get("/play", name="/play")
        if r.status_code == 200:
            self.csrf = _extract_csrf(r.text)

    # ---- Action de mise ----

    def _place_bet(self, session_id):
        if not session_id:
            return
        if not self.csrf:
            self._refresh_csrf()
        if not self.csrf:
            return

        bet_type, bet_value = random.choice(BET_TYPES)
        amount = random.choice([10, 20, 50])

        with self.client.post(
            "/api/bet",
            json={"session_id": session_id, "bet_type": bet_type,
                  "bet_value": bet_value, "amount": amount},
            headers={"X-CSRFToken": self.csrf, "Content-Type": "application/json"},
            catch_response=True,
            name="/api/bet",
        ) as r:
            if r.status_code not in (200, 201, 400):
                # 400 = mise invalide (solde insuffisant, session fermée) → attendu
                r.failure(f"bet HTTP {r.status_code}: {r.text[:120]}")


# ---------------------------------------------------------------------------
# Scénario BetStorm : pic de mises simultanées
# ---------------------------------------------------------------------------

class BetStorm(HttpUser):
    """
    Tous les users attendent qu'une session s'ouvre puis misent d'un coup.
    Reproduit le pire cas : ~100 POST /api/bet dans la fenêtre de 30s.
    Lancer avec l'admin qui ouvre une session manuellement pendant le test.
    """

    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.csrf = None
        self.creds = _next_credentials()
        self._login()

    def _login(self):
        r = self.client.get("/login")
        csrf = _extract_csrf(r.text)
        if not csrf:
            return
        with self.client.post(
            "/login",
            data={"username": self.creds["username"],
                  "password": self.creds["password"],
                  "csrf_token": csrf},
            allow_redirects=True,
            catch_response=True,
            name="/login [POST]",
        ) as r:
            if r.status_code == 200 and "Identifiants incorrects" not in r.text:
                self.csrf = _extract_csrf(r.text)
            else:
                r.failure(f"Login échoué {self.creds['username']}")

    @task(20)
    def poll_and_bet(self):
        with self.client.get(
            "/api/session/status", catch_response=True, name="/api/session/status"
        ) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
                return
            try:
                data = r.json()
            except Exception:
                return

            if data.get("status") == "open" and self.csrf:
                session_id = data.get("session_id")
                bet_type, bet_value = random.choice(BET_TYPES)
                with self.client.post(
                    "/api/bet",
                    json={"session_id": session_id, "bet_type": bet_type,
                          "bet_value": bet_value, "amount": 20},
                    headers={"X-CSRFToken": self.csrf,
                             "Content-Type": "application/json"},
                    catch_response=True,
                    name="/api/bet",
                ) as rb:
                    if rb.status_code not in (200, 201, 400):
                        rb.failure(f"bet HTTP {rb.status_code}")


# ---------------------------------------------------------------------------
# Scénario baseline : polling seul
# ---------------------------------------------------------------------------

class PollingOnly(HttpUser):
    """Mesure uniquement le coût du polling status sans aucune autre action.
    Utile pour établir le baseline avant d'ajouter des mises."""

    wait_time = between(3, 5)

    def on_start(self):
        self.creds = _next_credentials()
        r = self.client.get("/login")
        csrf = _extract_csrf(r.text)
        if csrf:
            self.client.post(
                "/login",
                data={"username": self.creds["username"],
                      "password": self.creds["password"],
                      "csrf_token": csrf},
                allow_redirects=True,
                name="/login [POST]",
            )

    @task
    def poll(self):
        self.client.get("/api/session/status", name="/api/session/status")


# ---------------------------------------------------------------------------
# Scénario VotePolling : baseline endpoint vote public (sans auth)
# ---------------------------------------------------------------------------

class VotePolling(HttpUser):
    """
    Mesure la latence de /api/vote/display-state (endpoint public, sans auth).
    Émule le grand écran qui rafraîchit la catégorie projetée toutes les 5s.
    Pas de login requis — aucune dépendance à app_mode ou à une session active.
    """

    wait_time = between(4, 6)

    @task
    def poll_vote_display(self):
        with self.client.get(
            "/api/vote/display-state",
            catch_response=True,
            name="/api/vote/display-state",
        ) as r:
            if r.status_code != 200:
                r.failure(f"vote/display-state HTTP {r.status_code}")


# ---------------------------------------------------------------------------
# Scénario VotePlayer : joueur réaliste en phase vote
# ---------------------------------------------------------------------------

class VotePlayer(HttpUser):
    """
    Simule un joueur pendant la phase vote :
    - Se connecte au démarrage (même pattern que CasinoPlayer)
    - Polle /api/vote/state toutes les 2–5s
    - Si une catégorie est projetée et le vote ouvert :
        • Soumet un classement aléatoire des films disponibles
        • 1 fois sur 3 : place un boost sur la catégorie
    - Dégradation gracieuse si app_mode != 'vote' ou pas de session ouverte
    """

    wait_time = between(2, 5)

    def on_start(self):
        self.csrf = None
        self.logged_in = False
        self.creds = _next_credentials()
        self._login()

    # ---- Auth (identique à CasinoPlayer) ----

    def _login(self):
        r = self.client.get("/login")
        csrf = _extract_csrf(r.text)
        if not csrf:
            return
        with self.client.post(
            "/login",
            data={
                "username": self.creds["username"],
                "password": self.creds["password"],
                "csrf_token": csrf,
            },
            allow_redirects=True,
            catch_response=True,
            name="/login [POST]",
        ) as r:
            if r.status_code == 200 and "Identifiants incorrects" not in r.text:
                self.csrf = _extract_csrf(r.text)
                self.logged_in = True
            else:
                r.failure(f"Login échoué pour {self.creds['username']} (HTTP {r.status_code})")

    # ---- Tâche principale ----

    @task
    def vote_cycle(self):
        """Cycle complet : poll état → rankings → boost (1/3)."""
        # 1. Récupère l'état du vote courant
        state = self._get_vote_state()
        if state is None:
            return

        session = state.get("session")
        if not session or session.get("status") != "open":
            # Pas de vote actif — attente gracieuse, pas d'erreur
            return

        # 2. Tente d'abord le catalogue admin (403 attendu pour les joueurs)
        #    Utilise les catégories de /api/vote/state comme fallback.
        categories = self._try_catalogue() or state.get("categories", [])
        if not categories:
            return

        # Prend la première catégorie projetée (ou la première disponible)
        cat = categories[0]
        cat_id = cat.get("id")
        films  = cat.get("films", [])
        if not cat_id or not films:
            return

        film_ids = [f["id"] for f in films]

        # 3. Soumet un classement aléatoire
        self._submit_rankings(cat_id, film_ids)

        # 4. Boost 1 fois sur 3
        if random.random() < 0.33:
            self._submit_boost(cat_id)

    # ---- Helpers vote ----

    def _get_vote_state(self):
        """GET /api/vote/state — retourne le JSON ou None en cas d'erreur."""
        with self.client.get(
            "/api/vote/state",
            catch_response=True,
            name="/api/vote/state",
        ) as r:
            if r.status_code != 200:
                r.failure(f"vote/state HTTP {r.status_code}")
                return None
            try:
                return r.json()
            except Exception:
                r.failure("vote/state JSON invalide")
                return None

    def _try_catalogue(self):
        """
        GET /api/admin/vote/catalogue — 403 attendu pour les joueurs (non-admin).
        Retourne la liste des catégories ou None si inaccessible.
        Le 403 est marqué comme succès pour ne pas polluer les stats.
        """
        with self.client.get(
            "/api/admin/vote/catalogue",
            catch_response=True,
            name="/api/admin/vote/catalogue",
        ) as r:
            if r.status_code == 403:
                r.success()   # attendu pour les joueurs — pas un échec
                return None
            if r.status_code != 200:
                r.failure(f"catalogue HTTP {r.status_code}")
                return None
            try:
                return r.json().get("categories", [])
            except Exception:
                r.failure("catalogue JSON invalide")
                return None

    def _submit_rankings(self, cat_id, film_ids):
        """POST /api/vote/rankings — classement aléatoire des films de la catégorie."""
        if not self.csrf or not film_ids:
            return
        shuffled = list(film_ids)
        random.shuffle(shuffled)
        rankings = [{"film_id": fid, "rank": i + 1} for i, fid in enumerate(shuffled)]
        with self.client.post(
            "/api/vote/rankings",
            json={"category_id": cat_id, "rankings": rankings},
            headers={"X-CSRFToken": self.csrf, "Content-Type": "application/json"},
            catch_response=True,
            name="/api/vote/rankings",
        ) as r:
            if r.status_code not in (200, 400):
                # 400 = vote fermé ou film hors catégorie → attendu en fin de session
                r.failure(f"rankings HTTP {r.status_code}: {r.text[:80]}")

    def _submit_boost(self, cat_id):
        """POST /api/vote/boost — montant aléatoire (10–50) sur la catégorie."""
        if not self.csrf:
            return
        amount = random.randint(10, 50)
        with self.client.post(
            "/api/vote/boost",
            json={"category_id": cat_id, "amount": amount},
            headers={"X-CSRFToken": self.csrf, "Content-Type": "application/json"},
            catch_response=True,
            name="/api/vote/boost",
        ) as r:
            if r.status_code not in (200, 400):
                # 400 = solde insuffisant ou session fermée → attendu
                r.failure(f"boost HTTP {r.status_code}: {r.text[:80]}")
