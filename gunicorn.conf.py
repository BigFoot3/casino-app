import os

workers = 4
bind = "0.0.0.0:5000"
preload_app = True
worker_class = "sync"

accesslog = "/root/casino/logs/access.log"
errorlog  = "/root/casino/logs/error.log"
loglevel  = "info"

# Tells app factory the scheduler should start (one process, before fork)
raw_env = ["SERVER_SOFTWARE=gunicorn"]
