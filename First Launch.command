#!/usr/bin/env bash
# First Launch.command — bypasses macOS Gatekeeper for See3D E57 Converter.
#
# Double-click this file in Finder to:
#   1. Remove the "quarantine" attribute macOS sets on every download
#   2. Launch See3D E57 Converter
#
# After running this once, you can launch the .app normally from then on.
# Drag the .app into /Applications to install it permanently.

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

APP_NAME="See3D_E57_Converter.app"

clear
cat <<'EOF'
┌────────────────────────────────────────────────────────┐
│            See3D E57 Converter — first launch          │
├────────────────────────────────────────────────────────┤
│  macOS Gatekeeper blocks unsigned apps by default.     │
│  This script removes the "quarantine" attribute that   │
│  macOS attached to the download, so the app can run.   │
└────────────────────────────────────────────────────────┘

EOF

if [ ! -d "$APP_NAME" ]; then
    echo "ERROR: '$APP_NAME' not found next to this script."
    echo ""
    echo "Make sure both files are in the same folder."
    echo "They ship together in the same ZIP from GitHub."
    echo ""
    read -p "Press Return to close this window..."
    exit 1
fi

echo "→ Unblocking $APP_NAME ..."
xattr -dr com.apple.quarantine "$APP_NAME" 2>/dev/null || true
echo "  Done."
echo ""

echo "→ Launching $APP_NAME ..."
open "$APP_NAME"
echo ""

cat <<'EOF'
You can now launch the app normally (double-click) from anywhere.

To install permanently, drag See3D_E57_Converter.app into your
/Applications folder.

EOF

sleep 3
