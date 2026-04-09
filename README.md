# CasinoApp

Flask + SQLite roulette casino for up to 100 players.

## Setup

```bash
cd /root/casino
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env: set CASINO_SECRET_KEY to a strong random value
```

## Create users

```bash
venv/bin/flask --app "app:create_app()" create-user alice player
venv/bin/flask --app "app:create_app()" create-user bob admin
# Password is printed to stdout only — store it securely
```

## Run (development)

```bash
venv/bin/flask --app "app:create_app()" run
```

## Deploy (systemd)

```bash
cp casino.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable casino
systemctl start casino
```

## URLs

| URL | Description |
|-----|-------------|
| `/login` | Player / admin login |
| `/dashboard` | Player dashboard |
| `/play` | Place bets |
| `/rewards` | Claim rewards |
| `/admin` | Admin panel |
| `/roulette/display` | **Public** fullscreen display + QR code |

## Roulette modes

**Manual mode** (default):
- Admin opens a session via `/admin` → "Ouvrir session"
- Players bet for 30 seconds
- Admin clicks "Lancer roue" or the scheduler auto-triggers after 30s

**Auto mode**:
- Admin sets mode=auto + interval (60–300s) in `/admin`
- Scheduler auto-opens sessions: new round starts every `interval` seconds
- Deactivation takes effect after current round ends

## Architecture

- `app.py` — Flask factory
- `db.py` — SQLite WAL, `resolve_spin()`, schema
- `scheduler.py` — APScheduler (runs in gunicorn master only via `preload_app=True`)
- `routes/` — auth, player, admin, api blueprints
