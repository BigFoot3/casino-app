#!/usr/bin/env bash
# ============================================================
# Test de montée en charge — Casino Kryptide
# Usage : bash tests/run_load_test.sh [scenario] [users] [duration]
#
#   scenario : player (défaut) | betstorm | polling
#   users    : nombre d'utilisateurs simultanés (défaut: 100)
#   duration : durée du test (défaut: 3m)
#
# Exemples :
#   bash tests/run_load_test.sh                  # scénario complet, 100 users, 3min
#   bash tests/run_load_test.sh polling 100 2m   # baseline polling seul
#   bash tests/run_load_test.sh betstorm 100 2m  # pic de mises
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASINO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$CASINO_DIR/.env"

SCENARIO="${1:-player}"
USERS="${2:-100}"
DURATION="${3:-3m}"
HOST="http://127.0.0.1:5000"

# Map scenario → classe locust
case "$SCENARIO" in
  player)   CLASS="CasinoPlayer" ;;
  betstorm) CLASS="BetStorm" ;;
  polling)  CLASS="PollingOnly" ;;
  *)        echo "Scénario inconnu : $SCENARIO (player|betstorm|polling)"; exit 1 ;;
esac

SPAWN_RATE=$(( USERS / 10 ))
[ "$SPAWN_RATE" -lt 2 ] && SPAWN_RATE=2

echo "============================================"
echo " Casino — Test de montée en charge"
echo "  Scénario  : $SCENARIO ($CLASS)"
echo "  Utilisateurs : $USERS  |  Spawn : ${SPAWN_RATE}/s  |  Durée : $DURATION"
echo "============================================"

# Vérifier que le service casino tourne
if ! curl -sf "$HOST/" -o /dev/null 2>/dev/null; then
    echo "❌ Le service casino n'est pas accessible sur $HOST"
    echo "   Vérifiez : systemctl status casino"
    exit 1
fi
echo "✓ Service casino accessible"

# S'assurer que les comptes de test existent
if [ ! -f "$SCRIPT_DIR/load_test_users.json" ]; then
    echo "Création des comptes de test..."
    cd "$CASINO_DIR" && source venv/bin/activate
    python tests/setup_load_test.py
fi
echo "✓ Comptes de test prêts"

# Patcher le .env pour les tests locaux (HTTP plain) :
#   - RATELIMIT_ENABLED=false  → désactive le rate limit /login
#   - FLASK_ENV=development    → SESSION_COOKIE_SECURE=False (cookies sur HTTP plain)
echo ""
echo "▶ Configuration mode test (rate limiter off, cookies HTTP)..."

_patch_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s/^${key}=.*/${key}=${val}/" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

# Sauvegarder les valeurs originales
ORIG_RATELIMIT=$(grep "^RATELIMIT_ENABLED=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "true")
ORIG_FLASK_ENV=$(grep "^FLASK_ENV=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "production")

_patch_env "RATELIMIT_ENABLED" "false"
_patch_env "FLASK_ENV" "development"

systemctl restart casino
echo "  Attente redémarrage service..."
sleep 3

# Vérifier que le service est bien remonté
if ! curl -sf "$HOST/" -o /dev/null 2>/dev/null; then
    echo "❌ Le service n'est pas remonté après restart"
    exit 1
fi
echo "✓ Service redémarré (rate limiter off, SESSION_COOKIE_SECURE=False)"

# Cleanup garanti même en cas d'interruption
cleanup() {
    echo ""
    echo "▶ Restauration de la configuration production..."
    _patch_env "RATELIMIT_ENABLED" "$ORIG_RATELIMIT"
    _patch_env "FLASK_ENV" "$ORIG_FLASK_ENV"
    systemctl restart casino
    echo "✓ Configuration restaurée"
}
trap cleanup EXIT INT TERM

# Lancer le test
echo ""
echo "▶ Lancement du test ($DURATION)..."
echo "   Appuyez sur Ctrl+C pour arrêter proprement."
echo ""

cd "$CASINO_DIR" && source venv/bin/activate
locust -f tests/locustfile.py \
    --host="$HOST" \
    --users="$USERS" \
    --spawn-rate="$SPAWN_RATE" \
    --run-time="$DURATION" \
    --headless \
    --only-summary \
    --class-picker "$CLASS" \
    2>&1

echo ""
echo "✓ Test terminé."
