# CasinoApp

Application de roulette en ligne pour événements en salle — jusqu'à 100 joueurs simultanés.

---

## Fonctionnalités

- **Roulette complète** : mise sur couleur (rouge/noir), parité (pair/impair) ou numéro (0–36)
- **Cycle de session** : `waiting → open (30 s de mises) → spinning → closed → waiting`
- **Mode manuel** : l'admin ouvre et lance chaque tour depuis le panel
- **Mode automatique** : le scheduler ouvre les sessions à intervalle configurable (60–300 s), sans intervention
- **Affichage salle** (`/roulette/display`) : page publique plein écran, roue animée, QR code vers `/play`, polling 1 s
- **Panel admin** : gestion des utilisateurs, ajout de tokens, réinitialisation de mot de passe, catalogue de récompenses
- **Modal mot de passe sécurisé** : le mot de passe s'affiche 30 s dans un modal avec bouton copier, puis disparaît du DOM
- **Catalogue de récompenses** : échange de tokens contre des récompenses physiques, décrémentation atomique du stock
- **Création d'utilisateurs via CLI** : mot de passe aléatoire 8 caractères affiché uniquement en stdout
- **Sécurité** : CSRF sur tous les POST, rate-limiting sur `/login` (10 req/min/IP), hachage bcrypt, cookies `HttpOnly` + `SameSite=Lax`
- **Concurrence** : toutes les opérations sur les tokens utilisent `BEGIN IMMEDIATE` avec vérification de solde
- **Reprise au démarrage** : une session en cours (`open` ou `spinning`) est résolue automatiquement au redémarrage

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Backend   | Python 3.12, Flask 3.1 |
| Base de données | SQLite (mode WAL, `busy_timeout=10 s`) |
| Serveur WSGI | Gunicorn 23 — 4 workers, `preload_app=True` |
| Scheduler | APScheduler 3.10 — 1 process, tick toutes les 5 s |
| Auth / CSRF | Flask-WTF 1.2, bcrypt 4.2 |
| Rate limiting | Flask-Limiter 3.9 |
| QR code | qrcode 8.0 + Pillow |
| Frontend | Jinja2, Bootstrap 5.3 (CDN), JS vanilla |
| Roue | [milsaware/javascript-roulette](https://github.com/milsaware/javascript-roulette) |

---

## Installation

### Prérequis

- Python 3.12+
- pip
- systemd (pour la production)

### Clone + setup

```bash
git clone <repo> /root/casino
cd /root/casino
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### Configuration `.env`

```bash
cp .env.example .env
```

Édite `.env` :

```env
CASINO_SECRET_KEY=<clé aléatoire forte>   # ex: python3 -c "import secrets; print(secrets.token_hex(32))"
FLASK_ENV=development                      # development = HTTP ok ; production = cookie Secure (HTTPS requis)
```

### Initialisation de la base de données

La base est créée automatiquement au premier lancement. Aucune commande d'initialisation séparée n'est nécessaire.

---

## Lancement

### En développement

```bash
venv/bin/flask --app "app:create_app()" run
# Accessible sur http://localhost:5000
```

### En production (systemd)

```bash
cp casino.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable casino
systemctl start casino

# Suivi des logs
journalctl -u casino -f
```

> En production, positionner `FLASK_ENV=production` dans `.env` et servir l'app derrière un reverse proxy HTTPS (nginx + certbot) pour activer le cookie `Secure`.

---

## Utilisation admin

### Créer un utilisateur

```bash
venv/bin/flask --app "app:create_app()" create-user <nom> <role>
# role : player | admin
# Exemple :
venv/bin/flask --app "app:create_app()" create-user alice player
venv/bin/flask --app "app:create_app()" create-user bob admin
```

Le mot de passe généré (8 caractères alphanumériques) est affiché **une seule fois** dans le terminal.

### Accéder au panel admin

Ouvrir `http://<IP>:5000/admin` et se connecter avec un compte `admin`.

Depuis le panel :
- **Utilisateurs** : voir les soldes, ajouter des tokens, réinitialiser un mot de passe (le nouveau mot de passe s'affiche dans un modal 30 s)
- **Récompenses** : ajouter/modifier une récompense, ajuster le stock, activer/désactiver

### Lancer la roulette — mode manuel

1. Cliquer **"Ouvrir session"** → les joueurs ont 30 s pour miser sur `/play`
2. Cliquer **"Lancer roue"** (ou attendre que le scheduler déclenche automatiquement après 30 s)
3. Le résultat s'affiche sur `/roulette/display` et sur `/play`

### Lancer la roulette — mode automatique

1. Dans le panel admin, sélectionner **Auto** et définir l'intervalle (60–300 s)
2. Cliquer **Appliquer**
3. Le scheduler ouvre et résout les sessions sans intervention — désactiver après le tour en cours

### Affichage salle

Ouvrir `http://<IP>:5000/roulette/display` sur le projecteur ou l'écran de salle.

- Page publique (aucune authentification requise)
- Affiche la roue animée, le numéro gagnant et un QR code pointant vers `/play`
- Se met à jour automatiquement toutes les secondes

---

## Structure du projet

```
/root/casino/
├── app.py                  # Fabrique Flask (factory pattern)
├── db.py                   # Connexions SQLite, schéma, resolve_spin()
├── scheduler.py            # APScheduler — game_tick toutes les 5 s
├── cli.py                  # Commande flask create-user
├── gunicorn.conf.py        # 4 workers, preload_app=True, logs/
├── casino.service          # Unit systemd
├── requirements.txt
├── .env.example
├── routes/
│   ├── auth.py             # /login  /logout
│   ├── player.py           # /dashboard  /play  /rewards  /roulette/display
│   ├── admin.py            # /admin
│   └── api.py              # /api/session/*  /api/bet  /api/claim  /api/admin/*
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── play.html
│   ├── rewards.html
│   ├── admin/index.html
│   └── roulette/display.html
├── static/
│   ├── js/
│   │   ├── play.js         # Polling status → formulaire mise → résultat
│   │   ├── admin.js        # Modal mot de passe, contrôles session, gestion users/rewards
│   │   └── display.js      # Pilote spinWheel() depuis le polling /api/session/status
│   └── roulette/           # milsaware/javascript-roulette (cloné)
├── logs/
│   ├── access.log
│   └── error.log
└── casino.db               # Généré au premier lancement
```

---

## Licence

MIT — voir [LICENSE](LICENSE) pour les détails.
