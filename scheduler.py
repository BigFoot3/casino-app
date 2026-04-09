import logging
import random
from datetime import timezone, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from db import db_conn, get_active_session, get_config, ensure_session, resolve_spin

_scheduler = None


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_dt(s):
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def game_tick(app):
    with app.app_context():
        try:
            with db_conn() as conn:
                ensure_session(conn)
                active = get_active_session(conn)
                if not active:
                    return

                now = _utcnow()
                sid = active['id']

                if active['status'] == 'open' and active['opened_at']:
                    elapsed = (now - _parse_dt(active['opened_at'])).total_seconds()
                    if elapsed >= 30:
                        winning = random.randint(0, 36)
                        conn.execute('BEGIN IMMEDIATE')
                        conn.execute(
                            "UPDATE game_sessions SET status='spinning', winning_number=?"
                            " WHERE id=? AND status='open'",
                            (winning, sid)
                        )
                        conn.execute('COMMIT')
                        logging.info(f'[scheduler] Session {sid} → spinning (winning: {winning})')

                elif active['status'] == 'spinning':
                    resolve_spin(sid)
                    logging.info(f'[scheduler] resolve_spin({sid}) triggered')

                elif active['status'] == 'waiting':
                    cfg = get_config(conn)
                    if cfg.get('auto_mode_enabled', '0') != '1':
                        return
                    interval = int(cfg.get('auto_interval_seconds', '120'))
                    prev = conn.execute(
                        "SELECT * FROM game_sessions WHERE status='closed' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    should_open = False
                    if prev and prev['closed_at']:
                        closed_ago = (now - _parse_dt(prev['closed_at'])).total_seconds()
                        if closed_ago >= (interval - 30):
                            should_open = True
                    elif not prev:
                        should_open = True  # first ever session, open immediately

                    if should_open:
                        conn.execute('BEGIN IMMEDIATE')
                        conn.execute(
                            "UPDATE game_sessions SET status='open', opened_at=? WHERE id=? AND status='waiting'",
                            (now.isoformat(), sid)
                        )
                        conn.execute('COMMIT')
                        logging.info(f'[scheduler] Session {sid} auto-opened')

        except Exception as exc:
            logging.error(f'[scheduler] game_tick error: {exc}', exc_info=True)


def start_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        lambda: game_tick(app),
        'interval',
        seconds=5,
        max_instances=1,
        misfire_grace_time=30,
        id='game_tick',
    )
    _scheduler.start()
    logging.info('[scheduler] APScheduler started (game_tick every 5s)')
