"""
Crée 100 comptes de test pour la montée en charge.
À lancer UNE FOIS avant le test locust.

Usage :
    cd /root/casino && source venv/bin/activate
    python tests/setup_load_test.py [--clean]

Options :
    --clean   Supprime les comptes de test existants avant de les recréer
"""

import sys
import os
import json
import bcrypt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db_conn, init_db

LOAD_TEST_CREDENTIALS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "load_test_users.json"
)
PASSWORD = os.environ.get("LOAD_TEST_PASSWORD", "changeme")
N_USERS = 100


def clean_test_users(conn):
    deleted = conn.execute(
        "DELETE FROM users WHERE username LIKE 'loadtest_%'"
    ).rowcount
    print(f"  {deleted} comptes de test supprimés.")


def create_test_users(conn):
    password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=10)).decode()
    users = []
    for i in range(1, N_USERS + 1):
        username = f"loadtest_{i:03d}"
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, tokens) VALUES (?,?,?,?)",
                (username, password_hash, "player", 500),
            )
            users.append({"username": username, "password": PASSWORD})
        except Exception as e:
            print(f"  ⚠ {username} : {e}")

    return users


def main():
    clean = "--clean" in sys.argv

    print("=== Setup comptes de test pour montée en charge ===")

    # Ensure DB is initialized
    init_db()

    with db_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")

        if clean:
            print("Nettoyage des comptes existants...")
            clean_test_users(conn)

        # Check if already exist
        existing = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'loadtest_%'"
        ).fetchone()[0]
        if existing > 0 and not clean:
            print(f"  {existing} comptes de test déjà présents.")
            print("  Utilisez --clean pour les recréer.")
        else:
            print(f"Création de {N_USERS} comptes (mot de passe: {PASSWORD})...")
            users = create_test_users(conn)
            print(f"  {len(users)} comptes créés.")

        conn.execute("COMMIT")

    # Read back actual users
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT username FROM users WHERE username LIKE 'loadtest_%' ORDER BY username"
        ).fetchall()
    users = [{"username": r["username"], "password": PASSWORD} for r in rows]

    with open(LOAD_TEST_CREDENTIALS_FILE, "w") as f:
        json.dump(users, f, indent=2)

    print(f"\n{len(users)} comptes disponibles.")
    print(f"Credentials écrits dans : {LOAD_TEST_CREDENTIALS_FILE}")
    print("\nPour lancer le test :")
    print("  locust -f tests/locustfile.py --host=http://127.0.0.1:5000")
    print("  locust -f tests/locustfile.py --host=https://casino.kryptide.fr")


if __name__ == "__main__":
    main()
