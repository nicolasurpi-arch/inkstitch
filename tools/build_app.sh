#!/bin/bash
# Builds (or rebuilds) Ink2Jef.app at the repo root: a double-clickable macOS
# app that starts tools/webapp.py (if it isn't already running) and opens it
# in the browser. Safe to re-run any time, e.g. after cloning on a new
# machine or moving the repo - it always resolves paths relative to its own
# location, nothing is hardcoded.
#
# Usage: tools/build_app.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP="$PROJECT_DIR/Ink2Jef.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SCRIPT_DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key>
	<string>Ink2Jef</string>
	<key>CFBundleDisplayName</key>
	<string>Ink2Jef</string>
	<key>CFBundleIdentifier</key>
	<string>com.inkstitch-tools.ink2jef-launcher</string>
	<key>CFBundleVersion</key>
	<string>1.0</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleExecutable</key>
	<string>launcher</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>LSMinimumSystemVersion</key>
	<string>10.13</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>LSUIElement</key>
	<true/>
</dict>
</plist>
PLIST

# PROJECT_DIR is baked in here (at build time, not launch time) so the app
# keeps working if it's later dragged to the Desktop, /Applications, or the
# Dock - it no longer needs to sit next to the repo once built.
cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# Launches the Ink/Stitch -> .jef local web app (starting the server if it
# isn't already running) and opens it in the default browser.

PROJECT_DIR="$PROJECT_DIR"
PORT=5001
URL="http://127.0.0.1:\$PORT"
LOG_DIR="\$HOME/Library/Logs/Ink2Jef"
LOG_FILE="\$LOG_DIR/webapp.log"

mkdir -p "\$LOG_DIR"

is_server_up() {
    lsof -nP -iTCP:"\$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

if ! is_server_up; then
    cd "\$PROJECT_DIR" || exit 1
    nohup "\$PROJECT_DIR/venv/bin/python" "\$PROJECT_DIR/tools/webapp.py" >>"\$LOG_FILE" 2>&1 &
    disown

    for _ in \$(seq 1 30); do
        if is_server_up; then
            break
        fi
        sleep 0.5
    done
fi

open "\$URL"
LAUNCHER

chmod +x "$APP/Contents/MacOS/launcher"
xattr -cr "$APP" 2>/dev/null || true

echo "Built $APP (points at $PROJECT_DIR)"
