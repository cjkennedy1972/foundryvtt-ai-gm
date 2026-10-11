#!/usr/bin/env bash
# ============================================
# FoundryVTT AI Gamemaster Engine
# ============================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/ai-engine"

# --- Setup virtual environment ---
if [ ! -d ".venv" ] && [ ! -d "venv" ]; then
    echo "🔧 Setting up Python virtual environment..."
    python3 -m venv .venv
fi

if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    source venv/bin/activate
fi

# --- Install dependencies ---
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt -q

# --- Headless browser for the Foundry connection (no-op once installed) ---
echo "🌐 Installing Chromium for Playwright..."
python -m playwright install chromium

# --- Check for .env ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "📝 Created .env from .env.example — edit it with your LLM and Foundry login settings"
fi

# --- Build admin panel ---
echo "🎨 Building admin panel..."
cd admin-panel
npm install --silent
npm run build --silent
cd ..

# --- Done ---
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit ai-engine/.env: LLM_API_KEY, LLM_BASE_URL, MODEL, FOUNDRY_USERNAME, FOUNDRY_PASSWORD"
echo "  2. Run: ./start.sh"
echo "  3. Open the admin panel at http://localhost:18080 and start your campaign"
echo ""
