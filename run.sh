#!/usr/bin/env bash
# ============================================
# Aethelwyrd AI Gamemaster Engine
# ============================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/ai-engine"

# --- Setup virtual environment ---
if [ ! -d "venv" ]; then
    echo "🔧 Setting up Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# --- Install dependencies ---
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt -q

# --- Check for .env ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "📝 Created .env from .env.example — edit it with your LLM and relay settings"
fi

# --- Done ---
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit ai-engine/.env and set LLM_API_KEY / LLM_BASE_URL / RELAY_API_KEY"
echo "  2. Make sure FoundryVTT is running and the Go relay is active"
echo "  3. Run: ./run.sh start"
echo ""
