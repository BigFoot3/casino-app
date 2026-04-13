# Casino

Application roulette en ligne pour événements en présentiel — jusqu'à 100 joueurs simultanés.

> Fichier de référence pour Claude Code. Mettre à jour après chaque milestone.
> Dernière mise à jour : 2026-04-13

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
  auth.py            # /login  /logout
  player.py          # /dashboard  /play  /rewards  /roulette/display
  admin.py           # /admin
  api.py             # /api/session/*  /api/bet  /api/claim  /api/admin/*
templates/           # Jinja2 — base.html, dashboard.html, login.html, play.html, rewards.html
static/
  js/
    play.js          # Polling → formulaire de mise → affichage résultat
    admin.js         # Modal mot de passe, contrôles session, gestion users/récompenses
                     # + pollAdmin() toutes les 3s → updateControlsState(status, mode)
                     # + btn-stop-auto : arrêt mode auto immédiat sans rechargement
    display.js       # Lance spinWheel() depuis polling /api/session/status
                     # + cache leaderboard (isSpinning + lastLeaderboardCache) : tops
                     #   jamais vidés pendant le spin
  roulette/          # milsaware/javascript-roulette (cloné)
logs/                # access.log, error.log (Gunicorn)
casino.db            # Créé automatiquement au premier lancement
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
| `POST /api/claim` | api | Réclamer une récompense |
| `POST /api/admin/*` | api | Actions admin (tokens, users, rewards) |
| `POST /api/vote/open` | api | Ouvrir un vote film (admin) — body: `{film_title}` |
| `POST /api/vote/close` | api | Fermer le vote courant (admin) |
| `POST /api/vote/submit` | api | Voter (joueur) — body: `{score, bonus_amount}` |
| `GET /api/vote/results?session_id=X` | api | Résultats détaillés (admin) |
| `POST /api/vote/palmares` | api | Passer en mode palmarès (admin) |
| `GET /api/vote/summary` | api | Toutes les sessions fermées triées par note (admin) |
| `POST /api/vote/reset-mode` | api | Repasser en mode roulette (admin) |

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
| `CASINO_SECRET_KEY` | Clé secrète Flask — `secrets.token_hex(32)` |
| `FLASK_ENV` | `development` ou `production` (prod requis pour cookie Secure) |

`.env` est chargé manuellement dans `create_app()` (sans python-dotenv).

---

## Invariants critiques

- Toutes les opérations de tokens utilisent `BEGIN IMMEDIATE` avec vérification du solde — aucun double-spend.
- CSRF activé sur toutes les routes POST. Les appels API JSON envoient `X-CSRFToken` dans le header.
- `resolve_spin()` est **idempotent** — sûr à rappeler après un redémarrage.
- Le modal de mot de passe s'auto-supprime du DOM après 30s — jamais stocké côté client.
- Sessions en `open` ou `spinning` au démarrage sont automatiquement récupérées par `startup_check()`.

---

## Pièges connus

```
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
⚠️ leaderboard cache → display.js mémorise lastLeaderboardCache ; pendant isSpinning, un payload vide
                        ne vide pas les tops — comportement voulu, pas un bug
⚠️ btn-stop-auto    → met à jour l'UI immédiatement (pas de location.reload()) ; les autres contrôles
                        de session font toujours location.reload() après action manuelle
⚠️ /api/session/status → retourne `mode` ('auto'|'manual') et `auto_interval_seconds` — utilisés par
                          admin.js pour piloter le badge ⚡ AUTO et le bouton stop-auto
```

---

## Tests

```bash
cd /root/casino && source venv/bin/activate
pytest tests/ -v --tb=short   # 77 tests (76 passed + 1 xfail intentionnel)
```

Suite dans `tests/` :
- `conftest.py` — fixtures : `app` (DB temporaire isolée), `admin_client`, `player_client`, `player2_client`, `open_session`, `open_vote_session`
- `test_casino.py` — 77 tests sur 9 classes : `TestAuth`, `TestRoulette`, `TestBets`, `TestRewards`, `TestLeaderboard`, `TestVoteOpen`, `TestVoteSubmit`, `TestVoteClose`, `TestVoteResults`, `TestPalmares`, `TestAdminActions`

> `test_no_double_bet_same_session` → xfail intentionnel : l'app autorise les mises multiples par session (multi-bet frontend).

---

## Instructions pour Claude Code

À la fin de chaque session de travail :
1. Mettre à jour ce CLAUDE.md si l'architecture, les routes, les bugs connus ou la stack ont changé
2. Synchroniser la copie : `cp /root/casino/CLAUDE.md /root/CLAUDE_docs/CLAUDE-casino.md`
3. Commiter et pousser : `cd /root/casino && git add -A && git commit -m "..." && git push`
4. Redémarrer si des fichiers de prod ont été modifiés : `systemctl restart casino`
5. Mettre à jour `/root/VPS_OVERVIEW.md` si l'infrastructure a changé
