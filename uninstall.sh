#!/usr/bin/env bash
# PodSight uninstaller — removes everything install.sh created.
# Settings (~/.config/podsight) and logs (~/.local/state/podsight) are kept;
# the commands to remove those too are printed at the end.
set -euo pipefail

say() { printf '[podsight-uninstall] %s\n' "$*"; }

rm -rf "$HOME/.local/share/podsight"
rm -f  "$HOME/.local/bin/podsight"
rm -f  "$HOME/.local/share/applications/podsight.desktop"
rm -f  "$HOME/.local/share/icons/hicolor/scalable/apps/podsight.svg"

gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true

say "Removed application, launcher, icon, and menu entry."
say "Kept your settings and logs. To remove those as well:"
say "  rm -rf ~/.config/podsight ~/.local/state/podsight"
