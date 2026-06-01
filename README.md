# Casino App

A real-time multiplayer roulette and voting app for live events — built to run on a projector with up to 100 concurrent players on their phones.

![CI](https://github.com/BigFoot3/casino-app/actions/workflows/tests.yml/badge.svg) ![Coverage](https://codecov.io/gh/BigFoot3/casino-app/branch/main/graph/badge.svg) ![Python](https://img.shields.io/badge/python-3.12-blue) ![Flask](https://img.shields.io/badge/flask-3.1.3-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Demo

Live demo: https://casino.kryptide.fr

![Demo](static/img/demo.gif)

---

## Features

Casino App runs through three coordinated modes — roulette, vote, and palmares — each managed from the admin panel with no page reload required. In roulette mode, up to 100 players simultaneously place bets from their phones while an animated wheel spins on the room display; payouts are calculated instantly and credited to each player's token balance. The token economy supports chip denominations from 1 to 100, column and dozen bets, and a rewards catalogue where players can redeem tokens for physical prizes. In vote mode, players rank films within categories and optionally spend tokens to boost their preferred entries, with live rankings updating in the room display as votes come in. The admin panel covers the full lifecycle: opening and closing sessions, switching between manual and automatic mode, managing users and token balances, resetting passwords, and tracking vote submissions in real time. The interface is mobile-first, designed to work on any phone browser without installation.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask 3.1.3 |
| Database | SQLite (WAL mode, `busy_timeout=10s`) |
| Server | Gunicorn 23.0.0 — 1 worker, 12 threads, behind nginx |
| Scheduling | APScheduler 3.10.4 — `game_tick` every 5s |
| Auth & Security | bcrypt, Flask-WTF (CSRF), Flask-Limiter, `BEGIN IMMEDIATE` transactions |
| Frontend | Jinja2, Bootstrap 5, vanilla JS, Space Grotesk (Google Fonts) |
| Testing | pytest, pytest-cov, Locust (load testing) |
| CI/CD | GitHub Actions, Codecov |

---

## Architecture

The app uses Flask's application factory pattern (`create_app()` in `app.py`), which keeps configuration, extensions, and blueprints cleanly separated and makes the test suite straightforward to wire up. SQLite runs in WAL mode with a 10-second busy timeout; all token operations use `BEGIN IMMEDIATE` transactions with balance verification to prevent double-spend under concurrent load, and `resolve_spin()` is fully idempotent so the app recovers cleanly from mid-spin restarts. APScheduler runs a `game_tick` every five seconds exclusively in the Gunicorn master process (`preload_app=True`), advancing session state from open to spinning to closed without any external queue. A single Gunicorn worker with 12 threads sits behind nginx, which handles TLS termination and static files; `ProxyFix` ensures Flask-Limiter sees real client IPs rather than the proxy address.

---

## Quick Start

```bash
git clone git@github.com:BigFoot3/casino-app.git
cd casino-app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set CASINO_SECRET_KEY
flask --app "app:create_app()" create-user admin admin
flask --app "app:create_app()" run
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CASINO_SECRET_KEY` | Yes | Flask secret key — app refuses to start if missing |
| `FLASK_ENV` | No | `development` (HTTP cookies) or `production` (requires HTTPS for `Secure` cookie) |
| `CASINO_BASE_URL` | No | Base URL used for QR code generation; defaults to `request.host_url` if unset |
| `RATELIMIT_ENABLED` | No | Set to `false` to disable rate limiting during load tests |

---

## Running Tests

```bash
pytest tests/ -v --tb=short
```

65 tests total: 64 passed, 1 expected failure (`xfail`). Coverage sits at approximately 77%, reported to Codecov on every push.

---

## Project Status

Built and deployed for a live event in June 2026; the service remains running as a public demo at `https://casino.kryptide.fr`. The repo is open — issues and pull requests are welcome.

---

## License

MIT — see [LICENSE](LICENSE) for details.
