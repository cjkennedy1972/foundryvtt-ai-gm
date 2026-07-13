#!/usr/bin/env bash
# ============================================
# FoundryVTT AI Gamemaster Engine - Start
# ============================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/ai-engine"

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f venv/bin/activate ]; then
    source venv/bin/activate
else
    echo "❌ Python environment not found. Run ./run.sh first."
    exit 1
fi

echo "🎲 Starting FoundryVTT AI Gamemaster Engine..."
echo "   Admin Panel: http://localhost:18080"
echo "   AI Engine API: http://localhost:18080/api"
echo ""

# Disable the relay's 10-minute headless session inactivity timeout
export RELAY_ENV_HEADLESS_SESSION_TIMEOUT=0

exec python main.py
