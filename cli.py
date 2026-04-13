import random
import string
import click
import bcrypt
from flask import current_app

from db import db_conn


def register_cli(app):
    @app.cli.command('create-user')
    @click.argument('username')
    @click.argument('role', type=click.Choice(['admin', 'player']))
    def create_user(username, role):
        """Create a user and print the generated password to stdout."""
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
        with db_conn() as conn:
            try:
                conn.execute(
                    'INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)',
                    (username, pw_hash, role)
                )
                conn.commit()
            except Exception as e:
                if 'UNIQUE' in str(e):
                    raise click.ClickException(f'Username "{username}" already exists.')
                raise
        click.echo(f'User created: {username} / {password}  (role: {role})')
