# Casino

Application roulette en ligne pour événements en présentiel — jusqu'à 100 joueurs simultanés.

> Fichier de référence pour Claude Code. Mettre à jour après chaque milestone.
> Dernière mise à jour : 2026-05-14 (session 2)

---

## Infrastructure

| Composant | Détail |
|-----------|--------|
| VPS | Hetzner CX23, Nuremberg — 178.104.41.200 |
| OS | Ubuntu 24.04, Python 3.12 |
| URL | `https://casino.kryptide.fr` (nginx → Gunicorn port 5000) |
| Service | `casino.service` |
| Logs | `journalctl -u casino -f` |

---

## Stack

| Package | Version | Rôle |
|---------|---------|------|
| Flask | 3.1.0 | Web framework (factory pattern) |
| Gunicorn | 23.0.0 | WSGI — 1 worker, 12 threads (gthread), `preload_app=True` |
| APScheduler | 3.10.4 | Game tick toutes les 5s |
| SQLite WAL | — | Base de données — `busy_timeout=10s` |
| Flask-WTF | 1.2.2 | CSRF sur tous les POST |
| Flask-Limiter | 3.9.0 | Rate limiting (10 req/min/IP sur `/login`) |
| bcrypt | 4.2.1 | Hash des mots de passe |
| qrcode / Pillow | 8.0 / 12.2.0 | QR codes |

---

## Structure du projet

```
app.py               # Flask factory — create_app()
db.py                # Connexion SQLite, schéma, resolve_spin()
scheduler.py         # APScheduler — game_tick() toutes les 5s
cli.py               # Commande CLI : flask create-user
extensions.py        # Limiter Flask-Limiter (partagé entre modules)
gunicorn.conf.py     # 1 worker, 12 threads (gthread), preload_app=True, logs/
casino.service       # Unité systemd
routes/
  auth.py            # /login  /logout — redirect post-login vers /play
  player.py          # /dashboard  /play  /rewards  /roulette/display
                     # + /rewards retourne claimed_ids (IDs des récompenses déjà réclamées)
  admin.py           # /admin
  api.py             # /api/session/*  /api/bet  /api/admin/*
templates/           # Jinja2 — base.html, dashboard.html, login.html, play.html, rewards.html
static/
  css/
    midnight-gala.css  # 577 lignes — tout le CSS inline extrait des templates (refactor 2026-04-19)
  js/
    play.js          # Polling → formulaire de mise → affichage résultat
                     # + mises colonne/douzaine/moitié (column/dozen/half)
                     # + gridLocked : grille déverrouillée pendant spin pour pré-miser le tour suivant
    admin.js         # Modal mot de passe, contrôles session, gestion users/récompenses
                     # + pollAdmin() toutes les 3s → updateControlsState(status, mode)
                     # + btn-action : bouton contextuel unique (▶ Ouvrir / 🎯 Lancer / ⏳ En cours…)
                     # + btn-close : visible uniquement en open (Fermer ↯)
                     # + toggle Manuel/Auto (btn-mode-manual/auto) + interval-wrap
                     # + filtre recherche live users et films
                     # + CRUD films (renommer, supprimer) et suppression récompenses
    display.js       # Lance spinWheel() depuis polling /api/session/status
                     # + cache leaderboard (isSpinning + lastLeaderboardCache) : tops
                     #   jamais vidés pendant le spin
  roulette/          # milsaware/javascript-roulette (cloné)
logs/                # access.log, error.log (Gunicorn)
casino.db            # Créé automatiquement au premier lancement
tests/
  conftest.py        # Fixtures : app, admin_client, player_client, player2_client, open_session
  test_casino.py     # 77 tests unitaires
  locustfile.py      # Load testing Locust — 3 scénarios (CasinoPlayer, BetStorm, PollingOnly)
  load_test_users.json  # 100 comptes de test pré-générés (ne pas commiter)
  run_load_test.sh   # Wrapper bash pour lancer locust en headless
```

---

## Routes

| Route | Blueprint | Description |
|-------|-----------|-------------|
| `GET /login` | auth | Page de login |
| `POST /login` | auth | Authentification |
| `GET /logout` | auth | Déconnexion |
| `GET /dashboard` | player | Tableau de bord joueur |
| `GET /play` | player | Interface de mise |
| `GET /rewards` | player | Catalogue de récompenses |
| `GET /roulette/display` | player | Affichage salle (grand écran) |
| `GET /admin` | admin | Panel administrateur |
| `GET /api/session/status` | api | Statut courant (JSON) + `app_mode` + `vote_session` |
| `POST /api/session/open` | api | Ouvrir une session roulette (admin) |
| `POST /api/session/spin` | api | Lancer la roue (admin) |
| `POST /api/bet` | api | Placer une mise (joueur) |
| `POST /api/admin/*` | api | Actions admin (tokens, users, rewards) |
| `POST /api/vote/open` | api | Ouvrir un vote film (admin) — body: `{film_title}` |
| `POST /api/vote/close` | api | Fermer le vote courant (admin) |
| `POST /api/vote/submit` | api | Voter (joueur) — body: `{score, bonus_amount}` |
| `GET /api/vote/results?session_id=X` | api | Résultats détaillés (admin) |
| `POST /api/vote/palmares` | api | Passer en mode palmarès (admin) |
| `GET /api/vote/summary` | api | Toutes les sessions fermées triées par note (admin) |
| `POST /api/vote/reset-mode` | api | Repasser en mode roulette (admin) |
| `POST /api/admin/vote/<id>/delete` | api | Supprimer une session film + ses votes (admin) |
| `POST /api/admin/vote/<id>/rename` | api | Renommer le titre d'un film (admin) — body: `{film_title}` |
| `POST /api/admin/rewards/<id>/delete` | api | Supprimer une récompense + son historique claims (admin) |

---

## Schéma SQLite (`db.py`)

```sql
users          (id, username, password_hash, role, tokens, created_at)
game_sessions  (id, status, mode, auto_interval_seconds, winning_number, opened_at, closed_at, created_at)
bets           (id, session_id, user_id, bet_type, bet_value, amount, payout)
rewards        (id, name, description, token_cost, stock, active)
reward_claims  (id, user_id, reward_id, claimed_at)
app_config     (key, value)   -- auto_mode_enabled, auto_interval_seconds, app_mode, current_vote_session_id
vote_sessions  (id, film_title, status, opened_at, closed_at, created_at)
votes          (id, vote_session_id, user_id, score, bonus_amount, weighted_score, updated_at)
               UNIQUE(vote_session_id, user_id)
```

`status` roulette : `waiting → open (30s) → spinning → closed → waiting`
`app_mode` : `'roulette'` / `'vote'` / `'palmares'`
`status` vote : `'waiting'` / `'open'` / `'closed'`

---

## Cycle de session

```
waiting → open (fenêtre de mise 30s) → spinning → closed → waiting
```

- **Mode manuel** : l'admin clique "Ouvrir session" puis "Lancer roue".
- **Mode auto** : le scheduler ouvre et résout les sessions à intervalle configurable (60–300s).
- **Redémarrage** : `startup_check()` dans `db.py` récupère automatiquement toute session en `spinning` ou `open` stale.

### Payouts roulette
| Type de mise | Condition | Multiplicateur |
|-------------|-----------|----------------|
| `color` red/black | Numéro rouge/noir (0 = House win) | ×2 |
| `parity` even/odd | Parité (0 = House win) | ×2 |
| `number` | Numéro exact | ×36 |
| `column` 1/2/3 | Colonne (0 = House win) | ×3 |
| `dozen` 1/2/3 | 1–12 / 13–24 / 25–36 (0 = House win) | ×3 |
| `half` low/high | 1–18 / 19–36 (0 = House win) | ×2 |

Correspondance colonnes :
- `column=1` → numéros 3,6,9,…,36 (n%3==0) — bouton "2→1" haut
- `column=2` → numéros 2,5,8,…,35 (n%3==2) — bouton "2→1" milieu
- `column=3` → numéros 1,4,7,…,34 (n%3==1) — bouton "2→1" bas

Disposition du tableau de mise (play.html et display.html) :
```
[ 1-18 ]  [ 19-36 ]                      ← #half-bets / #live-half
[ 0 ][ 3 ][ 6 ]…[ 36 ][ 2→1 ]           ← #roulette-grid / #live-roulette-grid
     [ 2 ][ 5 ]…[ 35 ][ 2→1 ]
     [ 1 ][ 4 ]…[ 34 ][ 2→1 ]
[ 1ère (1-12) ][ 2ème (13-24) ][ 3ème ]  ← #dozen-bets / #live-dozens
[ PAIR ][ ROUGE ][ NOIR ][ IMPAIR ]       ← #outside-bets / #live-outside
```

---

## Scheduler (`scheduler.py`)

`game_tick()` toutes les 5 secondes — lancé uniquement dans le processus Gunicorn master (`preload_app=True`). Ne jamais appeler en mode dev Flask (`flask run`).

```python
if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
    from scheduler import start_scheduler
    start_scheduler(app)
```

---

## Gestion des utilisateurs

```bash
# Créer un utilisateur (mot de passe affiché une seule fois)
flask --app "app:create_app()" create-user <nom> <role>
# role : player | admin
```

---

## Services

```bash
systemctl status casino
systemctl restart casino
journalctl -u casino -f
journalctl -u casino -n 50 --no-pager
```

---

## Développement local

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Renseigner CASINO_SECRET_KEY
flask --app "app:create_app()" run
```

---

## Environnement (`.env`)

| Variable | Description |
|----------|-------------|
| `CASINO_SECRET_KEY` | Clé secrète Flask — **obligatoire**, lève `RuntimeError` si absente |
| `FLASK_ENV` | `development` ou `production` (prod requis pour cookie Secure) |
| `CASINO_BASE_URL` | URL de base pour le QR code (ex. `https://casino.kryptide.fr`) — si absent, utilise `request.host_url` |

`.env` est chargé manuellement dans `create_app()` (sans python-dotenv).

---

## Invariants critiques

- Toutes les opérations de tokens utilisent `BEGIN IMMEDIATE` avec vérification du solde — aucun double-spend.
- CSRF activé sur toutes les routes POST. Les appels API JSON envoient `X-CSRFToken` dans le header.
- `resolve_spin()` est **idempotent** — sûr à rappeler après un redémarrage.
- Le modal de mot de passe s'auto-supprime du DOM après 30s — jamais stocké côté client.
- Sessions en `open` ou `spinning` au démarrage sont automatiquement récupérées par `startup_check()`.
- `CASINO_SECRET_KEY` est **obligatoire** — `create_app()` lève `RuntimeError` si la variable est absente ou vide.
- `ProxyFix(x_for=1, x_proto=1, x_host=1)` appliqué sur `app.wsgi_app` — nécessaire pour que Flask-Limiter lise la vraie IP derrière nginx.
- Tout rendu de données API en JavaScript utilise `textContent` — jamais `innerHTML` avec données non-trusted.

---

## Pièges connus

```
⚠️ midnight-gala.css → tout le CSS de l'app est dans static/css/midnight-gala.css — ne pas remettre de style inline dans les templates
⚠️ claimed_ids       → /rewards retourne les IDs des récompenses déjà réclamées par le joueur — utilisé côté client pour désactiver les boutons
⚠️ redirect /play    → routes/auth.py redirige vers /play après login (pas /dashboard)
⚠️ RATELIMIT_ENABLED → variable d'env lue dans extensions.py — mettre à false pour les tests de charge Locust
⚠️ APScheduler      → un seul processus (Gunicorn master) — ne pas lancer en mode flask run
⚠️ SQLite WAL       → busy_timeout=10s — les connexions ne doivent pas rester ouvertes longtemps
⚠️ isolation_level  → None dans db_conn() — transactions manuelles (BEGIN IMMEDIATE requis)
⚠️ resolve_spin()   → idempotent — status != 'spinning' → ROLLBACK silencieux
⚠️ SESSION_COOKIE_SECURE → True en prod, False en dev HTTP (sinon cookie jamais envoyé)
⚠️ preload_app=True → forking : les objets initialisés avant le fork sont partagés — éviter les connexions DB globales
⚠️ Rôles           → 'admin' | 'player' — constraint SQLite CHECK
⚠️ winning_number  → 0 = House win (aucun parieur ne gagne, ni rouge/noir ni pair/impair)
⚠️ vote delta_tokens → ancien_bonus - nouveau_bonus : positif=remboursement, négatif=déduction
⚠️ vote weighted_score → score × (1 / 1.5 / 2) selon bonus (0 / 25 / 50)
⚠️ vote UPSERT      → UNIQUE(vote_session_id, user_id) — un seul vote par user par session, modifiable
⚠️ app_mode         → stocké dans app_config — roulette par défaut, reset via /api/vote/reset-mode
⚠️ admin boutons    → états pilotés exclusivement par pollAdmin() (toutes les 3s) via updateControlsState()
                      — ne jamais ajouter de variable JS locale pour l'état des boutons
                      — currentStatus (admin.js) est mis à jour par updateControlsState(), pas autrement
⚠️ leaderboard cache → display.js mémorise lastLeaderboardCache ; pendant isSpinning, un payload vide
                        ne vide pas les tops — comportement voulu, pas un bug
⚠️ btn-action       → bouton unique contextuel : waiting→▶ Ouvrir, open→🎯 Lancer, spinning→⏳ En cours…
                        les actions sont routées selon currentStatus — ne pas restaurer les 3 boutons séparés
⚠️ btn-close        → affiché uniquement quand status='open' (Fermer ↯) — action d'urgence destructive
⚠️ mode toggle      → btn-mode-manual / btn-mode-auto — applique immédiatement via applyMode()
                        interval-wrap visible uniquement en auto ; change sur blur du champ intervalle
⚠️ /api/session/status → retourne `mode` ('auto'|'manual') et `auto_interval_seconds` — utilisés par
                          admin.js pour piloter le toggle et interval-wrap
⚠️ grace period spinning → /api/session/status simule 'spinning' pendant 12s après fermeture normale
                            pour la page display — court-circuité si winning_number IS NULL (force-close admin)
                            — ne pas retirer la condition `prev['winning_number'] is not None`
⚠️ /api/claim          → endpoint supprimé (2026-05-08) — ne plus utiliser ; les récompenses sont
                          distribuées exclusivement via /api/admin/reward/give (admin)
⚠️ CASINO_SECRET_KEY   → obligatoire en prod ET en test — conftest.py injecte 'test-secret-for-pytest'
                          via os.environ.setdefault() avant create_app()
⚠️ ProxyFix            → configuré dans app.py (x_for=1, x_proto=1, x_host=1) — ne pas le retirer,
                          Flask-Limiter lirait 127.0.0.1 pour toutes les IPs sans lui
⚠️ CSP nginx           → 'unsafe-inline' conservé pour scripts (inline script dans play.html)
                          — à durcir avec nonce si les templates sont refactorisés
⚠️ CASINO_BASE_URL     → si absent, le QR code utilise request.host_url (Host header — injectable)
                          — toujours définir dans .env en production
⚠️ stats_reset_at      → stocké en isoformat() (ex: '2026-05-13T14:15:14+00:00') pour comparaison
                          lexicographique correcte avec closed_at — ne jamais utiliser strftime()
⚠️ column bet display  → les colBtn dans display.html doivent avoir data-type="column" et data-val
                          — sans ces attributs renderChips() ignore silencieusement les mises colonne
⚠️ admin films         → suppression protégée si status='open' (vote en cours) — renommer idem
⚠️ admin users         → liste collapsible (collapse show par défaut) + filtre live + scroll 5 lignes
⚠️ admin récompenses   → suppression cascade reward_claims via /api/admin/rewards/<id>/delete
⚠️ dozen/half display  → les cellules ldoz/lhalf dans display.html doivent avoir data-type et data-val
                          — sans ces attributs renderChips() ignore silencieusement ces mises
⚠️ live-half/dozens/outside → width: calc(100% - 86px) = 46px (zero) + 40px (colBtn 38px + gap 2px)
                               — ne pas utiliser calc(100% - 46px), sinon déborde sous les 2→1
⚠️ balanceDelta vs netDelta → play.js pollResult() : balanceDelta = sum(payout) pour mettre à jour
                               balanceEl (amount déjà déduit à la mise) ; netDelta = sum(payout-amount)
                               pour l'affichage du résultat (profit/perte net) — ne pas confondre les deux
⚠️ gridLocked vs betPlaced → gridLocked : verrou UI (grille non-cliquable entre soumission et spin)
                              betPlaced : routage vers pollResult() — les deux sont indépendants
                              gridLocked=false dès que spinning démarre, betPlaced reste true jusqu'au résultat
```

---

## Tests

```bash
cd /root/casino && source venv/bin/activate
pytest tests/ -v --tb=short   # 72 tests (71 passed + 1 xfail intentionnel) — dernière exécution : 2026-05-14 (session 2)
```

Suite dans `tests/` :
- `conftest.py` — fixtures : `app` (DB temporaire isolée), `admin_client`, `player_client`, `player2_client`, `open_session`, `open_vote_session`
- `test_casino.py` — 72 tests sur 11 classes : `TestAuth`, `TestRoulette`, `TestBets`, `TestRewards`, `TestLeaderboard`, `TestVoteOpen`, `TestVoteSubmit`, `TestVoteClose`, `TestVoteResults`, `TestPalmares`, `TestAdminActions`

> `test_no_double_bet_same_session` → xfail intentionnel : l'app autorise les mises multiples par session (multi-bet frontend).

---

## Load testing (Locust)

Suite de montée en charge pour valider la tenue sous 100 joueurs simultanés.

```bash
cd /root/casino && source venv/bin/activate

# Prérequis : désactiver le rate limiter pour les tests
export RATELIMIT_ENABLED=false

# Créer les 100 comptes de test (une seule fois)
python tests/setup_load_test.py

# Scénario complet — 100 users, spawn 10/s, durée 3 min
locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
       --users=100 --spawn-rate=10 --run-time=3m --headless \
       --class-picker CasinoPlayer

# Pic de mises — 100 users, spawn rapide
locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
       --users=100 --spawn-rate=50 --run-time=2m --headless \
       --class-picker BetStorm

# Baseline polling seul
locust -f tests/locustfile.py --host=http://127.0.0.1:5000 \
       --users=100 --spawn-rate=10 --run-time=2m --headless \
       --class-picker PollingOnly

# Interface web (localhost:8089) pour visualisation temps réel
locust -f tests/locustfile.py --host=http://127.0.0.1:5000
```

**3 scénarios Locust :**
| Scénario | Comportement |
|----------|-------------|
| `CasinoPlayer` | Joueur réaliste : login → polling status → mise → navigation rewards |
| `BetStorm` | Pic de mises simultanées lors d'une ouverture de session |
| `PollingOnly` | Seulement `/api/session/status` — baseline latence serveur |

> `load_test_users.json` — 100 comptes précréés par `setup_load_test.py`. Ne pas commiter.
> `RATELIMIT_ENABLED=false` — variable d'env lue dans `extensions.py` pour désactiver Flask-Limiter pendant les tests de charge.

---

## Instructions pour Claude Code

À la fin de chaque session de travail :
1. Mettre à jour ce CLAUDE.md si l'architecture, les routes, les bugs connus ou la stack ont changé
2. Synchroniser la copie : `cp /root/casino/CLAUDE.md /root/CLAUDE_docs/CLAUDE-casino.md`
3. Commiter et pousser : `cd /root/casino && git add -A && git commit -m "..." && git push`
4. Redémarrer si des fichiers de prod ont été modifiés : `systemctl restart casino`
5. Mettre à jour `/root/VPS_OVERVIEW.md` si l'infrastructure a changé
