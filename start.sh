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

exec python main.py
