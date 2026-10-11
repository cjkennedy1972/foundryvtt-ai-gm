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

# The World CLI integration was removed. Its WORLD_CLI_* keys in an existing .env would stop the
# engine from starting (unknown keys are rejected), so drop them, keeping a backup of the file.
if [ -f .env ] && grep -q '^WORLD_CLI_' .env; then
    cp .env ".env.bak-world-cli-removal-$(date +%s)"
    sed -i.tmp '/^WORLD_CLI_/d' .env && rm -f .env.tmp
    echo "🧹 Removed WORLD_CLI_* settings from .env (World CLI was removed; backup kept)"
fi

echo "🎲 Starting FoundryVTT AI Gamemaster Engine..."
echo "   Admin Panel: http://localhost:18080"
echo "   AI Engine API: http://localhost:18080/api"
echo ""

exec python main.py
