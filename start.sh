#!/usr/bin/env bash
# ============================================
# FoundryVTT AI Gamemaster Engine - Start
# ============================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/ai-engine"

source venv/bin/activate

echo "🎲 Starting FoundryVTT AI Gamemaster Engine..."
echo "   Admin Panel: http://localhost:18080"
echo "   AI Engine API: http://localhost:18080/api"
echo ""

# Disable the relay's 10-minute headless session inactivity timeout
export RELAY_ENV_HEADLESS_SESSION_TIMEOUT=0

exec python main.py
