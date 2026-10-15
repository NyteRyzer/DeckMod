#!/usr/bin/env bash
# Installs DeckMod for the current user. No root, no pip, works on SteamOS's read-only system.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/deckmod/app"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
rm -rf "$APP_DIR/deckmod"
cp -r "$SRC/deckmod" "$APP_DIR/"

cat > "$BIN_DIR/deckmod" <<WRAP
#!/usr/bin/env bash
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m deckmod "\$@"
WRAP
chmod +x "$BIN_DIR/deckmod"

cat > "$DESKTOP_DIR/deckmod.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=DeckMod
Comment=Mod any game installed on this device
Exec=$BIN_DIR/deckmod ui
Icon=applications-games
Terminal=false
Categories=Game;Utility;
DESK

echo "DeckMod installed."
echo "  - Run 'deckmod ui' (or launch 'DeckMod' from the app menu) for the graphical interface."
echo "  - Run 'deckmod --help' for the command line."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "  - Note: add $BIN_DIR to your PATH, or run $BIN_DIR/deckmod";; esac
