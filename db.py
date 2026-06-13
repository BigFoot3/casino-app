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


def _migrate_vote_boosts_amount():
    """Replace CHECK(amount IN (0,25,50)) with CHECK(amount >= 0) on vote_boosts."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='vote_boosts'"
        ).fetchone()
        if not row or 'amount IN (0,25,50)' not in row['sql']:
            return
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('''
            CREATE TABLE vote_boosts_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES vote_sessions(id),
                user_id     INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES vote_categories(id),
                amount      INTEGER NOT NULL DEFAULT 0
                            CHECK(amount >= 0),
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, user_id, category_id)
            )
        ''')
        conn.execute('INSERT INTO vote_boosts_new SELECT * FROM vote_boosts')
        conn.execute('DROP TABLE vote_boosts')
        conn.execute('ALTER TABLE vote_boosts_new RENAME TO vote_boosts')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('COMMIT')
        print('DB migration: vote_boosts CHECK(amount >= 0) applied.', flush=True)


def _migrate_vote_schema(conn):
    """Upgrade old vote_sessions/vote_boosts schemas if they exist with pre-Phase-2 columns."""
    cols = [r[1] for r in conn.execute('PRAGMA table_info(vote_sessions)').fetchall()]
    if 'film_title' not in cols:
        return
    conn.execute('PRAGMA foreign_keys=OFF')
    for tbl in ('votes', 'vote_rankings', 'vote_boosts', 'vote_sessions'):
        conn.execute(f'DROP TABLE IF EXISTS {tbl}')
    conn.executescript('''
        CREATE TABLE vote_sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            status     TEXT NOT NULL DEFAULT 'waiting'
                       CHECK(status IN ('waiting','open','closed','palmares')),
            opened_at  TEXT,
            closed_at  TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE vote_boosts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES vote_sessions(id),
            user_id     INTEGER NOT NULL REFERENCES users(id),
            category_id INTEGER NOT NULL REFERENCES vote_categories(id),
            amount      INTEGER NOT NULL DEFAULT 0
                        CHECK(amount >= 0),
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(session_id, user_id, category_id)
        );
        CREATE TABLE vote_rankings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES vote_sessions(id),
            user_id    INTEGER NOT NULL REFERENCES users(id),
            film_id    INTEGER NOT NULL REFERENCES vote_films(id),
            rank       INTEGER NOT NULL,
            points     REAL NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(session_id, user_id, film_id)
        );
    ''')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.commit()


def _migrate_shop_item_images():
    """Create shop_item_images table and migrate existing image_path entries if absent."""
    with db_conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shop_item_images'"
        ).fetchone()
        if exists:
            return
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''
            CREATE TABLE shop_item_images (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id       INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE CASCADE,
                image_path    TEXT NOT NULL,
                is_primary    INTEGER NOT NULL DEFAULT 0,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        ''')
        rows = conn.execute(
            "SELECT id, image_path FROM shop_items WHERE image_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO shop_item_images(item_id, image_path, is_primary, display_order)"
                " VALUES(?, ?, 1, 0)",
                (row['id'], row['image_path'])
            )
        conn.execute('COMMIT')
        print('DB migration: shop_item_images created.', flush=True)


def _migrate_shop_order_lines_price():
    """Add unit_price column to shop_order_lines if missing."""
    with db_conn() as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(shop_order_lines)').fetchall()]
        if 'unit_price' in cols:
            return
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('ALTER TABLE shop_order_lines ADD COLUMN unit_price REAL')
        conn.execute('COMMIT')
        print('DB migration: shop_order_lines.unit_price added.', flush=True)


def _migrate_shop_preorder():
    """Add preorder column to shop_items if missing."""
    with db_conn() as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(shop_items)').fetchall()]
        if 'preorder' in cols:
            return
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('ALTER TABLE shop_items ADD COLUMN preorder INTEGER NOT NULL DEFAULT 0')
        conn.execute('COMMIT')
        print('DB migration: shop_items.preorder added.', flush=True)


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
                session_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                bet_type   TEXT NOT NULL,
                bet_value  TEXT NOT NULL,
                amount     INTEGER NOT NULL,
                payout     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS app_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vote_categories (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS vote_films (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES vote_categories(id),
                title       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(category_id, title)
            );
            CREATE TABLE IF NOT EXISTS vote_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                status     TEXT NOT NULL DEFAULT 'waiting'
                           CHECK(status IN ('waiting','open','closed','palmares')),
                opened_at  TEXT,
                closed_at  TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS vote_rankings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES vote_sessions(id),
                user_id    INTEGER NOT NULL REFERENCES users(id),
                film_id    INTEGER NOT NULL REFERENCES vote_films(id),
                rank       INTEGER NOT NULL,
                points     REAL NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, user_id, film_id)
            );
            CREATE TABLE IF NOT EXISTS vote_boosts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES vote_sessions(id),
                user_id     INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES vote_categories(id),
                amount      INTEGER NOT NULL DEFAULT 0
                            CHECK(amount >= 0),
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, user_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS shop_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                price       REAL,
                image_path  TEXT,
                active      INTEGER NOT NULL DEFAULT 1,
                preorder    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS shop_variants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id    INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE CASCADE,
                size_label TEXT NOT NULL,
                stock      INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS shop_orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name  TEXT NOT NULL,
                phone      TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending'
                           CHECK(status IN ('pending','confirmed','cancelled')),
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS shop_order_lines (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL REFERENCES shop_orders(id) ON DELETE CASCADE,
                variant_id INTEGER NOT NULL REFERENCES shop_variants(id),
                quantity   INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS shop_item_images (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id       INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE CASCADE,
                image_path    TEXT NOT NULL,
                is_primary    INTEGER NOT NULL DEFAULT 0,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );
        ''')
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('auto_mode_enabled','0')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('auto_interval_seconds','120')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('app_mode','roulette')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('current_vote_session_id','')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('vote_revealed_categories','[]')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('vote_display_category_id','')")
        conn.execute("INSERT OR IGNORE INTO app_config(key,value) VALUES ('shop_enabled','0')")
        conn.commit()
        _migrate_vote_schema(conn)
        _migrate_vote_boosts_amount()
        _migrate_shop_preorder()
        _migrate_shop_item_images()
        _migrate_shop_order_lines_price()


def get_config(conn) -> dict:
    rows = conn.execute('SELECT key, value FROM app_config').fetchall()
    return {r['key']: r['value'] for r in rows}


def get_shop_config(db) -> bool:
    row = db.execute(
        "SELECT value FROM app_config WHERE key='shop_enabled'"
    ).fetchone()
    return row is not None and row['value'] == '1'


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
            if elapsed > active['auto_interval_seconds']:
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
                payout = 0
                amount = bet['amount']

                if winning_number != 0:
                    btype = bet['bet_type']
                    bval  = bet['bet_value']
                    if btype == 'color':
                        if bval == 'red' and winning_number in RED_NUMBERS:
                            payout = amount * 2
                        elif bval == 'black' and winning_number not in RED_NUMBERS:
                            payout = amount * 2
                    elif btype == 'parity':
                        if bval == 'even' and winning_number % 2 == 0:
                            payout = amount * 2
                        elif bval == 'odd' and winning_number % 2 == 1:
                            payout = amount * 2
                    elif btype == 'number':
                        if int(bval) == winning_number:
                            payout = amount * 36
                    elif btype == 'column':
                        col = int(bval)
                        if col == 1 and winning_number % 3 == 0:
                            payout = amount * 3
                        elif col == 2 and winning_number % 3 == 2:
                            payout = amount * 3
                        elif col == 3 and winning_number % 3 == 1:
                            payout = amount * 3
                    elif btype == 'dozen':
                        doz = int(bval)
                        if doz == 1 and 1 <= winning_number <= 12:
                            payout = amount * 3
                        elif doz == 2 and 13 <= winning_number <= 24:
                            payout = amount * 3
                        elif doz == 3 and 25 <= winning_number <= 36:
                            payout = amount * 3
                    elif btype == 'half':
                        if bval == 'low' and 1 <= winning_number <= 18:
                            payout = amount * 2
                        elif bval == 'high' and 19 <= winning_number <= 36:
                            payout = amount * 2

                conn.execute(
                    'UPDATE bets SET payout=? WHERE id=?',
                    (payout, bet['id'])
                )
                if payout > 0:
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
