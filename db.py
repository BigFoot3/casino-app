import sqlite3
import os
import logging
import random
from datetime import datetime, timezone
from contextlib import contextmanager

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'casino.db')

RED_NUMBERS = frozenset({1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    """Parse ISO datetime string, always return UTC-aware datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@contextmanager
def db_conn():
    """Raw connection with WAL + busy_timeout. isolation_level=None for manual tx."""
    conn = sqlite3.connect(DATABASE, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role         TEXT CHECK(role IN ('admin','player')) NOT NULL,
                tokens       INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS game_sessions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                status               TEXT CHECK(status IN ('waiting','open','spinning','closed')) DEFAULT 'waiting',
                mode                 TEXT CHECK(mode IN ('manual','auto')) DEFAULT 'manual',
                auto_interval_seconds INTEGER DEFAULT 120,
                winning_number       INTEGER,
                opened_at            TEXT,
                closed_at            TEXT,
                created_at           TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES game_sessions(id),
                user_id    INTEGER NOT NULL REFERENCES users(id),
                bet_type   TEXT CHECK(bet_type IN ('color','parity','number')) NOT NULL,
                bet_value  TEXT NOT NULL,
                amount     INTEGER CHECK(amount > 0) NOT NULL,
                result     TEXT CHECK(result IN ('win','loss')),
                payout     INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS rewards (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                token_cost  INTEGER CHECK(token_cost > 0) NOT NULL,
                stock       INTEGER DEFAULT 0,
                active      INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS reward_claims (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                reward_id  INTEGER NOT NULL REFERENCES rewards(id),
                claimed_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS app_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        ''')
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('auto_mode_enabled','0')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('auto_interval_seconds','120')")
        conn.commit()


def get_config(conn) -> dict:
    rows = conn.execute('SELECT key, value FROM app_config').fetchall()
    return {r['key']: r['value'] for r in rows}


def get_active_session(conn):
    """Return the most recent non-closed session, or None."""
    return conn.execute(
        "SELECT * FROM game_sessions WHERE status != 'closed' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def ensure_session(conn):
    """If no non-closed session exists, create one from app_config."""
    active = get_active_session(conn)
    if active:
        return
    cfg = get_config(conn)
    mode = 'auto' if cfg.get('auto_mode_enabled', '0') == '1' else 'manual'
    interval = int(cfg.get('auto_interval_seconds', '120'))
    conn.execute(
        "INSERT INTO game_sessions(status, mode, auto_interval_seconds) VALUES ('waiting', ?, ?)",
        (mode, interval)
    )
    conn.commit()


def startup_check():
    """Called on app startup to recover in-progress sessions."""
    with db_conn() as conn:
        ensure_session(conn)
        active = get_active_session(conn)
        if not active:
            return
        now = _utcnow()
        if active['status'] == 'spinning':
            logging.info('[startup] Found spinning session — resolving immediately')
            resolve_spin(active['id'])
        elif active['status'] == 'open' and active['opened_at']:
            elapsed = (now - _parse_dt(active['opened_at'])).total_seconds()
            if elapsed > 30:
                logging.info('[startup] Found stale open session — transitioning to spinning')
                conn.execute('BEGIN IMMEDIATE')
                winning = random.randint(0, 36)
                conn.execute(
                    "UPDATE game_sessions SET status='spinning', winning_number=? WHERE id=? AND status='open'",
                    (winning, active['id'])
                )
                conn.commit()
                resolve_spin(active['id'])


def resolve_spin(session_id: int):
    """
    Idempotent. Resolves a spinning session.
    On exception: rolls back, resets session to open for retry next cycle.
    """
    try:
        with db_conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            session = conn.execute(
                'SELECT * FROM game_sessions WHERE id=?', (session_id,)
            ).fetchone()

            if not session or session['status'] != 'spinning':
                conn.execute('ROLLBACK')
                return

            # Use pre-set winning_number (set during open→spinning transition) or generate
            winning_number = session['winning_number']
            if winning_number is None:
                winning_number = random.randint(0, 36)

            bets = conn.execute(
                'SELECT * FROM bets WHERE session_id=?', (session_id,)
            ).fetchall()

            for bet in bets:
                result = 'loss'
                payout = 0
                amount = bet['amount']

                if winning_number != 0:
                    btype = bet['bet_type']
                    bval  = bet['bet_value']
                    if btype == 'color':
                        if bval == 'red' and winning_number in RED_NUMBERS:
                            result, payout = 'win', amount * 2
                        elif bval == 'black' and winning_number not in RED_NUMBERS:
                            result, payout = 'win', amount * 2
                    elif btype == 'parity':
                        if bval == 'even' and winning_number % 2 == 0:
                            result, payout = 'win', amount * 2
                        elif bval == 'odd' and winning_number % 2 == 1:
                            result, payout = 'win', amount * 2
                    elif btype == 'number':
                        if int(bval) == winning_number:
                            result, payout = 'win', amount * 36

                conn.execute(
                    'UPDATE bets SET result=?, payout=? WHERE id=?',
                    (result, payout, bet['id'])
                )
                if result == 'win':
                    conn.execute(
                        'UPDATE users SET tokens = tokens + ? WHERE id=?',
                        (payout, bet['user_id'])
                    )

            cfg = get_config(conn)
            new_mode     = 'auto' if cfg.get('auto_mode_enabled', '0') == '1' else 'manual'
            new_interval = int(cfg.get('auto_interval_seconds', '120'))
            now_iso      = _utcnow().isoformat()

            conn.execute(
                "UPDATE game_sessions SET status='closed', winning_number=?, closed_at=? WHERE id=?",
                (winning_number, now_iso, session_id)
            )
            conn.execute(
                "INSERT INTO game_sessions(status, mode, auto_interval_seconds) VALUES ('waiting', ?, ?)",
                (new_mode, new_interval)
            )
            conn.execute('COMMIT')
            logging.info(f'[resolve_spin] Session {session_id} closed — winning: {winning_number}')

    except Exception as exc:
        logging.error(f'[resolve_spin] Error on session {session_id}: {exc}', exc_info=True)
        # Reset session to open so scheduler retries next tick
        try:
            with db_conn() as conn2:
                conn2.execute('BEGIN IMMEDIATE')
                conn2.execute(
                    "UPDATE game_sessions SET status='open', opened_at=? WHERE id=? AND status='spinning'",
                    (_utcnow().isoformat(), session_id)
                )
                conn2.execute('COMMIT')
        except Exception as exc2:
            logging.error(f'[resolve_spin] Failed to reset session {session_id}: {exc2}')
