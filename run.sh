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

# --- Check for .env ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "📝 Created .env from .env.example — edit it with your LLM and relay settings"
fi

# --- Build admin panel ---
echo "🎨 Building admin panel..."
cd admin-panel
npm install --silent
npm run build --silent
cd ..

# --- Build embedded relay (managed subprocess, source in relay/ submodule) ---
cd "$SCRIPT_DIR"

if [ ! -f "relay/go-relay/go.mod" ]; then
    echo "📡 Initializing relay submodule..."
    git submodule update --init relay
fi

if command -v go &>/dev/null; then
    echo "📡 Building relay server..."
    (cd relay/go-relay && go build -o "$SCRIPT_DIR/bin/relay" ./cmd/server)
else
    echo "❌ Go is not installed — the embedded relay cannot be built."
    echo "   Install it (brew install go), or set RELAY_MANAGED=false in ai-engine/.env"
    echo "   and run an external relay yourself."
fi

# Relay web UI (login/dashboard/pairing, served by the relay binary itself)
if [ ! -f "relay/public-dist/index.html" ] || [ "relay/frontend/src" -nt "relay/public-dist/index.html" ]; then
    echo "📡 Building relay web UI..."
    if command -v pnpm &>/dev/null; then
        (cd relay/frontend && pnpm install --ignore-scripts --silent && pnpm build)
    else
        (cd relay/frontend && npm install --ignore-scripts --silent && npm run build --silent)
    fi
fi

# --- Done ---
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit ai-engine/.env and set LLM_API_KEY / LLM_BASE_URL"
echo "  2. Run: ./start.sh (FoundryVTT and the relay launch automatically)"
echo "  4. Pair the Foundry module at http://localhost:13010 — log in with the"
echo "     credentials in data/relay/aigm-credentials.json"
echo ""
