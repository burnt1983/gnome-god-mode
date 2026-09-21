#!/usr/bin/env python3
"""Portable Linux desklet / panel-widget helpers for GTK 3.

The same window works as:
  --desklet   skip-taskbar gadget stuck on the desktop (all workspaces)
  --panel     compact chip you can place by the panel / in a corner

Desktops: GNOME, Cinnamon, MATE, XFCE, Budgie, LXQt, KDE Plasma (as a window).
Cinnamon users can also drop the included spice into Desklets or Applets.
"""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk


def argv_has(*flags: str) -> bool:
    return any(flag in sys.argv for flag in flags)


def wants_desklet() -> bool:
    return argv_has("--desklet") or os.environ.get("LINUX_WIDGET") == "desklet"


def wants_panel() -> bool:
    return argv_has("--panel") or os.environ.get("LINUX_WIDGET") == "panel"


def desktop_name() -> str:
    raw = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or ""
    )
    return raw.lower()


def apply_rgba(win: Gtk.Window) -> bool:
    """True if the compositor gave us a real alpha visual."""
    screen = win.get_screen()
    visual = screen.get_rgba_visual() if screen is not None else None
    if visual is None:
        return False
    win.set_visual(visual)
    win.set_app_paintable(True)
    return True


def apply_desklet(
    win: Gtk.Window,
    *,
    decorated: bool = False,
    keep_above: bool = True,
    skip_taskbar: bool = True,
    utility: bool = True,
) -> None:
    win.set_decorated(decorated)
    if skip_taskbar:
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
    win.set_keep_above(keep_above)
    try:
        win.stick()
    except Exception:
        pass
    if utility:
        try:
            win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        except Exception:
            pass
    try:
        win.set_accept_focus(True)
    except Exception:
        pass


def apply_panel(win: Gtk.Window) -> None:
    apply_desklet(win, decorated=False, keep_above=True, skip_taskbar=True)
    try:
        win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    except Exception:
        pass


def enable_drag_move(win: Gtk.Window, handle: Gtk.Widget | None = None) -> None:
    """Click-drag the gadget (no title bar)."""
    target = handle or win

    def on_press(_w, event):
        if event.button != 1:
            return False
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        try:
            win.begin_move_drag(
                int(event.button), int(event.x_root), int(event.y_root), int(event.time)
            )
            return True
        except Exception:
            return False

    target.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    target.connect("button-press-event", on_press)


def load_css(css: bytes) -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
