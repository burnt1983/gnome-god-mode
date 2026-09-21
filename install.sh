#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="${HOME}/.local/bin"
SHARE="${HOME}/.local/share/gnome-god-mode"
APPS="${HOME}/.local/share/applications"
ICONS="${HOME}/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$BIN" "$SHARE" "$APPS" "$ICONS"
install -m 0755 "$ROOT/god_mode.py" "$SHARE/god_mode.py"
install -m 0755 "$ROOT/search.py" "$SHARE/search.py"
ln -sfn "$SHARE/god_mode.py" "$BIN/god-mode"
ln -sfn "$SHARE/search.py" "$BIN/gnome-god-mode-search"

# Keep the original command names too.
ln -sfn "$SHARE/god_mode.py" "$BIN/lee-god-mode-widget"

sed "s|^Exec=god-mode$|Exec=${BIN}/god-mode|" \
    "$ROOT/data/god-mode.desktop" > "$APPS/god-mode.desktop"
chmod 0755 "$APPS/god-mode.desktop"

if [[ -f /usr/share/icons/Yaru/256x256/apps/org.gnome.Settings.png ]]; then
  cp /usr/share/icons/Yaru/256x256/apps/org.gnome.Settings.png "$ICONS/lee-god-mode.png"
fi

if [ -d "$ROOT/cinnamon" ]; then
  mkdir -p "${HOME}/.local/share/cinnamon/desklets" "${HOME}/.local/share/cinnamon/applets"
  cp -a "$ROOT/cinnamon/desklets/." "${HOME}/.local/share/cinnamon/desklets/"
  cp -a "$ROOT/cinnamon/applets/." "${HOME}/.local/share/cinnamon/applets/"
fi

echo "Installed God Mode."
echo "  Window:   god-mode"
echo "  Desklet:  god-mode --desklet"
echo "  Search:   gnome-god-mode-search"
echo "Cinnamon: Applets → God Mode. Other desktops: add the launcher to the panel."
