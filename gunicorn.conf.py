import os

workers = 1          # Single worker: in-memory rate-limiter (Flask-Limiter MemoryStorage)
                     # is per-process — multiple workers would give N×limit per IP.
                     # For concurrent I/O, use threads instead.
threads = 12         # 12 threads → 12 concurrent bcrypt logins (~7 logins/s, 100 joueurs en ~14s→~9s)
timeout = 30         # bcrypt peut prendre ~600ms — évite timeout prématuré sous charge
bind = "0.0.0.0:5000"
preload_app = True
worker_class = "gthread"  # thread-based worker required for threads > 1

accesslog = "/root/casino/logs/access.log"
errorlog  = "/root/casino/logs/error.log"
loglevel  = "info"

# Tells app factory the scheduler should start (one process, before fork)
raw_env = ["SERVER_SOFTWARE=gunicorn"]
