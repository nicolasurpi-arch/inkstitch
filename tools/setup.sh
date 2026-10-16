#!/bin/bash
# One-shot setup for this repo's custom image/text -> .jef tooling (tools/).
# Run from anywhere after cloning:
#   tools/setup.sh
#
# What it does, in order: checks Python, installs Inkscape via Homebrew if
# missing, creates/updates the venv, installs all Python dependencies, pulls
# the embroidery-fonts submodule, and builds the Ink2Jef.app launcher.
# Safe to re-run any time (e.g. after a `git pull`) to pick up new deps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> Project: $PROJECT_DIR"

if ! command -v python3 >/dev/null; then
    echo "python3 not found. Install Python 3.11+ (e.g. from python.org or 'brew install python') and re-run this script." >&2
    exit 1
fi
PYVER="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
echo "==> Found python3 $PYVER"

if ! command -v inkscape >/dev/null && [ ! -x /opt/homebrew/bin/inkscape ] && [ ! -x /Applications/Inkscape.app/Contents/MacOS/inkscape ]; then
    echo "==> Inkscape not found."
    if command -v brew >/dev/null; then
        echo "==> Installing Inkscape via Homebrew (brew install --cask inkscape)..."
        brew install --cask inkscape
    else
        echo "Homebrew not found either. Install Inkscape 1.4+ manually from https://inkscape.org/release/ and re-run this script." >&2
        exit 1
    fi
else
    echo "==> Inkscape found: $(command -v inkscape || echo /opt/homebrew/bin/inkscape)"
fi

if [ ! -d venv ]; then
    echo "==> Creating venv/"
    python3 -m venv venv
fi

echo "==> Installing Python dependencies (this can take a few minutes, wxPython builds from source on some platforms)..."
# --use-deprecated=legacy-resolver works around a real bug, not a version
# pin we chose: inkex's own git-hosted metadata declares its lxml
# requirement as "lxml (>=4.5.0,<5.0.0 || >=5.0.0,<6.0.0)", using "||" to
# OR-combine two ranges - not valid PEP 440, and pip's modern resolver
# refuses to parse it (confirmed failing the same way on pip 24.0 through
# 26.2.1, so this isn't fixed by picking an older/newer pip). The legacy
# resolver doesn't require fully parsing every transitive dependency's
# metadata up front, so it installs fine despite the malformed specifier.
venv/bin/pip install --use-deprecated=legacy-resolver -r requirements.txt -r tools/requirements.txt

echo "==> Fetching the embroidery-fonts submodule..."
git submodule update --init --recursive

if [[ "$OSTYPE" == darwin* ]]; then
    echo "==> Building Ink2Jef.app launcher..."
    tools/build_app.sh
fi

cat <<EOF

==> Setup done.

Run the web app:
    venv/bin/python tools/webapp.py
then open http://127.0.0.1:5001

$( [[ "$OSTYPE" == darwin* ]] && echo "Or just double-click Ink2Jef.app (in this folder) - it starts the server for you." )
EOF
