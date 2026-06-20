#!/usr/bin/env bash
# Creates "AI GM.app" in the project root.
# The .app uses the project's existing venv — no extra Python installation needed.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="AI GM"
APP_BUNDLE="$PROJECT_ROOT/$APP_NAME.app"
VENV_PYTHON="$PROJECT_ROOT/ai-engine/venv/bin/python"

# ── prereqs ──────────────────────────────────────────────────────────────────

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌  Python venv not found at $VENV_PYTHON"
    echo "    Run ./run.sh first to set up the project."
    exit 1
fi

echo "📦 Installing rumps into project venv…"
"$VENV_PYTHON" -m pip install rumps --quiet

# ── bundle structure ─────────────────────────────────────────────────────────

echo "🏗  Building $APP_NAME.app…"

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# Info.plist — LSUIElement hides the Dock icon (menu-bar-only app)
cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>AI GM</string>
  <key>CFBundleDisplayName</key>       <string>AI GM</string>
  <key>CFBundleIdentifier</key>        <string>com.foundryvtt.aigm</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleExecutable</key>        <string>AI GM</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>LSUIElement</key>               <true/>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

# Launcher shell script inside the bundle.
# Paths are resolved relative to this script so the bundle is portable
# as long as it stays inside the project directory.
LAUNCHER_SCRIPT="$APP_BUNDLE/Contents/MacOS/$APP_NAME"

cat > "$LAUNCHER_SCRIPT" << LAUNCHER
#!/usr/bin/env bash
# Resolve project root: this script lives at <project>/AI GM.app/Contents/MacOS/
MACOS_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
PROJECT_ROOT="\$(cd "\$MACOS_DIR/../../.." && pwd)"

VENV_PYTHON="\$PROJECT_ROOT/ai-engine/venv/bin/python"
LAUNCHER_PY="\$PROJECT_ROOT/launcher/app.py"

if [ ! -f "\$VENV_PYTHON" ]; then
    osascript -e "display dialog \"AI GM: venv not found.\\n\\nRun ./run.sh to set up the project first.\" buttons {\"OK\"} default button \"OK\" with icon stop"
    exit 1
fi

exec "\$VENV_PYTHON" "\$LAUNCHER_PY"
LAUNCHER

chmod +x "$LAUNCHER_SCRIPT"

echo ""
echo "✅  Built: $APP_BUNDLE"
echo ""
echo "To launch: double-click 'AI GM.app' in Finder, or:"
echo "  open '$APP_BUNDLE'"
echo ""
echo "To add to Dock: drag the .app to your Dock."
echo "To auto-start at login: System Settings → General → Login Items → add AI GM.app"
