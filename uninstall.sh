#!/usr/bin/env bash
set -euo pipefail
rm -f \
  "${HOME}/.local/bin/god-mode" \
  "${HOME}/.local/bin/gnome-god-mode-search" \
  "${HOME}/.local/bin/lee-god-mode-widget" \
  "${HOME}/.local/share/applications/god-mode.desktop"
rm -rf "${HOME}/.local/share/gnome-god-mode"
echo "Removed God Mode. Favorites in ~/.config/lee-god-mode were left in place."
