#!/usr/bin/env bash
# PodSight installer — per-user, no sudo required.
# Installs to ~/.local (XDG paths). Run from the cloned repository:
#   ./install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/podsight"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"
LAUNCHER="$BIN_DIR/podsight"

say()  { printf '[podsight-install] %s\n' "$*"; }
fail() { printf '[podsight-install] ERROR: %s\n' "$*" >&2; exit 1; }

# --- sanity checks ----------------------------------------------------------
[ -f "$REPO_DIR/podsight.py" ] || fail "podsight.py not found — run this from the cloned repo."
[ -d "$REPO_DIR/podsight_pkg" ] || fail "podsight_pkg/ not found — run this from the cloned repo."
command -v python3 >/dev/null || fail "python3 not found."

# Required GTK introspection stack
if ! python3 -c 'import gi; gi.require_version("Gtk","3.0"); gi.require_version("Wnck","3.0")' 2>/dev/null; then
    say "Missing required system packages. Install them, then re-run:"
    say "  Fedora:  sudo dnf install python3-gobject gtk3 libwnck3"
    say "  Ubuntu:  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-wnck-3.0"
    exit 1
fi

# --- copy application files --------------------------------------------------
say "Installing application to $APP_DIR"
mkdir -p "$APP_DIR"
cp "$REPO_DIR/podsight.py" "$APP_DIR/"
rm -rf "$APP_DIR/podsight_pkg"
cp -r "$REPO_DIR/podsight_pkg" "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# --- launcher wrapper --------------------------------------------------------
say "Creating launcher $LAUNCHER"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<'EOF'
#!/usr/bin/env bash
# PodSight launcher — logs to ~/.local/state/podsight/podsight.log
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/podsight"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/podsight.log"
# Simple rotation: keep one previous log once it passes ~1 MB
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv -f "$LOG" "$LOG.old"
fi
exec python3 "$HOME/.local/share/podsight/podsight.py" "$@" >>"$LOG" 2>&1
EOF
chmod +x "$LAUNCHER"

# --- icon + desktop entry ----------------------------------------------------
say "Installing icon and desktop entry"
mkdir -p "$ICON_DIR" "$DESKTOP_DIR"
cp "$REPO_DIR/assets/podsight.svg" "$ICON_DIR/podsight.svg"
# .desktop Exec does not expand ~ or $HOME — substitute the absolute path.
sed "s|@LAUNCHER@|$LAUNCHER|" "$REPO_DIR/assets/podsight.desktop" \
    > "$DESKTOP_DIR/podsight.desktop"

# --- refresh menu/icon caches (best effort; harmless if tools missing) -------
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true

# --- optional dependency report ----------------------------------------------
missing=()
command -v wmctrl  >/dev/null || missing+=(wmctrl)
command -v xdotool >/dev/null || missing+=(xdotool)
python3 -c 'import gi; gi.require_version("GtkLayerShell","0.1")' 2>/dev/null \
    || missing+=(gtk-layer-shell)
if [ "${#missing[@]}" -gt 0 ]; then
    say "Optional (recommended) packages not found: ${missing[*]}"
    say "  Fedora:  sudo dnf install ${missing[*]}"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) : ;;
    *) say "NOTE: $BIN_DIR is not in your PATH; the menu entry still works." ;;
esac

say "Done. Launch 'PodSight' from your application menu, or run: podsight"
say "Logs: ~/.local/state/podsight/podsight.log"
