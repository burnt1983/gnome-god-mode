#!/usr/bin/env python3
"""GNOME God Mode widget — searchable full gsettings dump with live controls.

Home view is category dropdowns (expanders). Each setting gets a switch,
slider, combo, spin, or text field. Writes go through Gio.Settings instantly.
Lists are not rebuilt on GSettings ``changed`` (that stole focus on toggles).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

HOME = Path.home()
CFG_DIR = HOME / ".config" / "lee-god-mode"
FAV_FILE = CFG_DIR / "favorites.json"
ICON = HOME / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "lee-god-mode.png"
APP_ID = "uk.lee.godmode"
PRGNAME = "lee-god-mode-widget"
MAX_ROWS = 180
SEARCH_DELAY_MS = 120

SIMPLE = {"b", "s", "i", "u", "d", "n", "q", "y", "x", "t", "as"}

QUICK = (
    ("org.gnome.desktop.interface", "color-scheme"),
    ("org.gnome.desktop.interface", "enable-animations"),
    ("org.gnome.desktop.interface", "text-scaling-factor"),
    ("org.gnome.desktop.interface", "clock-show-seconds"),
    ("org.gnome.desktop.interface", "clock-show-weekday"),
    ("org.gnome.desktop.interface", "show-battery-percentage"),
    ("org.gnome.desktop.interface", "locate-pointer"),
    ("org.gnome.desktop.interface", "enable-hot-corners"),
    ("org.gnome.desktop.interface", "font-name"),
    ("org.gnome.desktop.wm.preferences", "button-layout"),
    ("org.gnome.desktop.wm.preferences", "num-workspaces"),
    ("org.gnome.desktop.privacy", "remember-recent-files"),
    ("org.gnome.desktop.sound", "allow-volume-above-100-percent"),
    ("org.gnome.desktop.sound", "event-sounds"),
    ("org.gnome.desktop.peripherals.touchpad", "tap-to-click"),
    ("org.gnome.desktop.peripherals.touchpad", "natural-scroll"),
    ("org.gnome.desktop.peripherals.touchpad", "disable-while-typing"),
    ("org.gnome.desktop.screensaver", "lock-enabled"),
    ("org.gnome.desktop.session", "idle-delay"),
    ("org.gnome.settings-daemon.plugins.color", "night-light-enabled"),
    ("org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type"),
    ("org.gnome.mutter", "dynamic-workspaces"),
    ("org.gnome.mutter", "edge-tiling"),
    ("org.gnome.shell.extensions.dash-to-dock", "dock-fixed"),
    ("org.gnome.shell.extensions.dash-to-dock", "extend-height"),
    ("org.gnome.shell.extensions.dash-to-dock", "click-action"),
    ("org.gnome.nautilus.preferences", "default-folder-viewer"),
    ("org.gnome.desktop.background", "picture-uri"),
)

CSS = b"""
window { background: #14181c; }
label.heading { font-size: 18px; font-weight: 600; color: #7fdcff; }
label.status { color: #c8c8c8; }
label.hint { color: #8a8a8a; font-size: 11px; }
label.key { font-weight: 600; color: #e8eef2; }
label.summary { color: #c8c8c8; font-size: 12px; }
label.schema { color: #8aa0b0; font-size: 11px; }
label.section { font-weight: 600; color: #7fdcff; font-size: 12px; padding-top: 6px; }
label.exp { font-weight: 600; color: #7fdcff; }
entry, entry.search {
  background: #1b2228;
  color: #e8eef2;
  border-radius: 8px;
  min-height: 28px;
}
button.reset, button.star, button.raw, button.open {
  background-image: none;
  background-color: #24303a;
  color: #c8d4dc;
  border-radius: 8px;
  min-height: 28px;
  padding: 0 8px;
}
button.reset:hover, button.star:hover, button.raw:hover, button.open:hover, button.drop:hover {
  background-color: #2e3d4a;
}
button.star:checked { color: #ffd166; background-color: #3a2e1c; }
button.drop {
  background-image: none;
  background-color: #1b2228;
  color: #7fdcff;
  border-radius: 8px;
  min-height: 32px;
  padding: 4px 10px;
}
list { background: #14181c; }
row { border-radius: 8px; padding: 4px; }
row:selected { background: #24303a; }
row.header { padding-top: 8px; }
combobox { min-height: 28px; }
spinbutton { min-height: 28px; }
eventbox.setting {
  background-color: #1b2228;
  border-radius: 8px;
  margin-bottom: 4px;
}
"""


@dataclass(frozen=True)
class KeyRec:
    schema: str
    key: str
    summary: str
    description: str
    type_str: str
    enum_values: tuple[str, ...] | None
    range_min: float | None
    range_max: float | None
    haystack: str

    @property
    def fid(self) -> str:
        return f"{self.schema} {self.key}"

    @property
    def title(self) -> str:
        return self.summary or self.key.replace("-", " ")


def _range_of(schema_key: Gio.SettingsSchemaKey) -> tuple[str, object]:
    try:
        kind, payload = schema_key.get_range().unpack()
        return str(kind), payload
    except Exception:
        return "", None


def build_catalog() -> list[KeyRec]:
    source = Gio.SettingsSchemaSource.get_default()
    non_reloc, _reloc = source.list_schemas(True)
    out: list[KeyRec] = []
    for schema_id in non_reloc:
        schema = source.lookup(schema_id, True)
        if schema is None:
            continue
        for key in schema.list_keys():
            sk = schema.get_key(key)
            type_str = sk.get_value_type().dup_string()
            summary = (sk.get_summary() or "").strip()
            description = (sk.get_description() or "").strip()
            kind, payload = _range_of(sk)
            enum_values = None
            lo = hi = None
            if kind == "enum" and isinstance(payload, (list, tuple)):
                enum_values = tuple(str(x) for x in payload)
            elif kind == "range" and isinstance(payload, (list, tuple)) and len(payload) >= 2:
                try:
                    lo = float(payload[0])
                    hi = float(payload[1])
                except (TypeError, ValueError):
                    lo = hi = None
            hay = " ".join((schema_id, key, summary, description, type_str)).lower()
            out.append(
                KeyRec(
                    schema=schema_id,
                    key=key,
                    summary=summary,
                    description=description,
                    type_str=type_str,
                    enum_values=enum_values,
                    range_min=lo,
                    range_max=hi,
                    haystack=hay,
                )
            )
    out.sort(key=lambda r: (r.schema, r.key))
    return out


def type_matches(rec: KeyRec, type_filter: str) -> bool:
    if type_filter == "switches":
        return rec.type_str == "b"
    if type_filter == "numbers":
        return rec.type_str in {"i", "u", "d", "n", "q", "y", "x", "t"}
    if type_filter == "text":
        return rec.type_str in {"s", "as"}
    if type_filter == "choices":
        return bool(rec.enum_values)
    return True


def match_keys(
    catalog: list[KeyRec],
    query: str,
    type_filter: str,
    schema_filter: str,
    limit: int | None,
) -> tuple[list[KeyRec], int]:
    terms = " ".join(query.lower().split()).split()
    hits: list[KeyRec] = []
    for rec in catalog:
        if schema_filter and rec.schema != schema_filter:
            continue
        if not type_matches(rec, type_filter):
            continue
        if terms and not all(t in rec.haystack for t in terms):
            continue
        hits.append(rec)
    total = len(hits)
    if limit is not None:
        hits = hits[:limit]
    return hits, total


def group_for(schema: str) -> str:
    s = schema.lower()
    if "a11y" in s or "accessibility" in s:
        return "Accessibility"
    if "dash-to-dock" in s or s.endswith("ubuntu-dock"):
        return "Dock"
    if ".extensions.ding" in s:
        return "Desktop"
    if "tiling" in s:
        return "Windows"
    if "shell.extensions" in s:
        return "Extensions"
    if s.startswith("org.gnome.shell"):
        return "Shell"
    if s.startswith("org.gnome.mutter") or s.startswith("org.gnome.desktop.wm"):
        return "Windows"
    if "plugins.color" in s:
        return "Displays"
    if "plugins.power" in s or "power-manager" in s or s.startswith("org.gnome.desktop.session"):
        return "Power & session"
    if "media-keys" in s or "libgnomekbd" in s or s.startswith("org.gnome.desktop.input-sources"):
        return "Keyboard"
    if "peripherals.keyboard" in s:
        return "Keyboard"
    if "peripherals" in s or "touchpad" in s or s.endswith(".mouse"):
        return "Mouse & keyboard"
    if "privacy" in s or "lockdown" in s or "screensaver" in s:
        return "Privacy & lock"
    if s.endswith(".sound") or ".sound." in s or s.startswith("org.gnome.desktop.sound"):
        return "Sound"
    if "notification" in s:
        return "Notifications"
    if s.startswith("org.gnome.desktop.background") or s.startswith("org.gnome.desktop.interface"):
        return "Look"
    if s.startswith("org.gnome.desktop.calendar") or s.startswith("org.gnome.desktop.datetime"):
        return "Date & time"
    if "search-providers" in s:
        return "Search"
    if "remote-desktop" in s:
        return "Sharing"
    if "bluetooth" in s or "nm-applet" in s or "wwan" in s:
        return "Network"
    if "nautilus" in s or "filechooser" in s:
        return "Files"
    if s.startswith("org.freedesktop.ibus"):
        return "Input method"
    if "system-monitor" in s or "baobab" in s or s.startswith("org.gnome.disks"):
        return "System"
    if s.startswith("org.gnome.desktop"):
        return "Desktop"
    if s.startswith("org.gnome.settings-daemon"):
        return "Power & session"
    if s.startswith("org.gnome"):
        return "GNOME apps"
    return "Other"


GROUP_ORDER = (
    "Look",
    "Accessibility",
    "Desktop",
    "Dock",
    "Windows",
    "Mouse & keyboard",
    "Keyboard",
    "Sound",
    "Privacy & lock",
    "Power & session",
    "Notifications",
    "Displays",
    "Network",
    "Search",
    "Sharing",
    "Date & time",
    "Files",
    "Input method",
    "Shell",
    "Extensions",
    "GNOME apps",
    "System",
    "Other",
)

GROUP_LAUNCH = {
    "Look": ("Appearance", ["gnome-control-center", "ubuntu"]),
    "Accessibility": ("Accessibility", ["gnome-control-center", "universal-access"]),
    "Desktop": ("Ubuntu Desktop", ["gnome-control-center", "ubuntu"]),
    "Dock": ("Dock prefs", ["gnome-extensions", "prefs", "ubuntu-dock@ubuntu.com"]),
    "Windows": ("Multitasking", ["gnome-control-center", "multitasking"]),
    "Mouse & keyboard": ("Mouse", ["gnome-control-center", "mouse"]),
    "Keyboard": ("Keyboard", ["gnome-control-center", "keyboard"]),
    "Sound": ("Sound", ["gnome-control-center", "sound"]),
    "Privacy & lock": ("Privacy", ["gnome-control-center", "privacy"]),
    "Power & session": ("Power", ["gnome-control-center", "power"]),
    "Notifications": ("Notifications", ["gnome-control-center", "notifications"]),
    "Displays": ("Displays", ["gnome-control-center", "display"]),
    "Network": ("Network", ["gnome-control-center", "network"]),
    "Search": ("Search", ["gnome-control-center", "search"]),
    "Sharing": ("Sharing", ["gnome-control-center", "sharing"]),
    "Date & time": ("Date & Time", ["gnome-control-center", "system"]),
    "Files": ("Files", ["nautilus"]),
    "Input method": ("IBus", ["ibus-setup"]),
    "Shell": ("Tweaks", ["gnome-tweaks"]),
    "Extensions": ("Extensions", ["extension-manager"]),
    "GNOME apps": ("Applications", ["gnome-control-center", "applications"]),
    "System": ("System Monitor", ["gnome-system-monitor"]),
}


def wants_slider(rec: KeyRec) -> bool:
    if rec.type_str not in {"i", "u", "d", "n", "q", "y", "x", "t"}:
        return False
    if rec.range_min is None or rec.range_max is None:
        return False
    span = rec.range_max - rec.range_min
    if span <= 0:
        return False
    if rec.type_str == "d":
        return span <= 20
    return span <= 1000


def load_favorites() -> set[str]:
    try:
        data = json.loads(FAV_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(x) for x in data}


def save_favorites(favs: set[str]) -> None:
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    FAV_FILE.write_text(json.dumps(sorted(favs), indent=2) + "\n", encoding="utf-8")


def _fmt(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value)
    return str(value)


class GodModeWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, catalog: list[KeyRec]):
        super().__init__(application=app, title="God Mode")
        self.set_default_size(700, 820)
        self.set_resizable(True)
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        if ICON.exists():
            self.set_icon_from_file(str(ICON))

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.catalog = catalog
        self.by_id = {r.fid: r for r in catalog}
        self.favorites = load_favorites()
        self._settings: dict[str, Gio.Settings] = {}
        self._building = False
        self._writing = False
        self._search_timeout = 0
        self._schema_filter = ""
        self._rows: dict[str, Gtk.Widget] = {}
        self._end_build_id = 0
        self._grouped: dict[str, dict[str, list[KeyRec]]] = defaultdict(dict)
        for rec in catalog:
            bucket = self._grouped[group_for(rec.schema)]
            bucket.setdefault(rec.schema, []).append(rec)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(14)
        root.set_margin_bottom(14)
        root.set_margin_start(14)
        root.set_margin_end(14)
        self.add(root)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="God Mode")
        title.get_style_context().add_class("heading")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        head.pack_start(title, True, True, 0)
        pin = Gtk.CheckButton(label="Keep on top")
        pin.set_active(True)
        pin.connect("toggled", lambda b: self.set_keep_above(b.get_active()))
        head.pack_start(pin, False, False, 0)
        root.pack_start(head, False, False, 0)

        self.status = Gtk.Label(
            label=f"{len(catalog)} settings · open a category dropdown or search"
        )
        self.status.set_line_wrap(True)
        self.status.set_xalign(0)
        self.status.get_style_context().add_class("status")
        root.pack_start(self.status, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search settings — animations, dock, dark…")
        self.search.connect("search-changed", self._on_search)
        self.search.connect("activate", lambda *_: self._apply_search())
        root.pack_start(self.search, False, False, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.group_combo = Gtk.ComboBoxText()
        self.group_combo.append("", "All categories")
        for name in GROUP_ORDER:
            if name in self._grouped:
                self.group_combo.append(name, name)
        self.group_combo.set_active_id("")
        self.group_combo.connect("changed", lambda *_: self._apply_search())
        tools.pack_start(self.group_combo, False, False, 0)

        self.type_combo = Gtk.ComboBoxText()
        for ident, label in (
            ("all", "All types"),
            ("switches", "Switches"),
            ("numbers", "Sliders / numbers"),
            ("choices", "Dropdowns"),
            ("text", "Text"),
        ):
            self.type_combo.append(ident, label)
        self.type_combo.set_active_id("all")
        self.type_combo.connect("changed", lambda *_: self._apply_search())
        tools.pack_start(self.type_combo, False, False, 0)

        self.back_btn = Gtk.Button(label="All groups")
        self.back_btn.get_style_context().add_class("reset")
        self.back_btn.connect("clicked", self._clear_filters)
        self.back_btn.set_sensitive(False)
        tools.pack_start(self.back_btn, False, False, 0)
        hint = Gtk.Label(label="Changes save instantly")
        hint.get_style_context().add_class("hint")
        hint.set_xalign(1)
        hint.set_hexpand(True)
        tools.pack_start(hint, True, True, 0)
        root.pack_start(tools, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.add(self.page)
        root.pack_start(scroll, True, True, 0)

        foot = Gtk.Label(
            label="Open a category dropdown for live switches, sliders and menus. "
            "Right-click a row: reset, copy, or open in dconf Editor. Star keeps it on Quick."
        )
        foot.set_line_wrap(True)
        foot.set_xalign(0)
        foot.get_style_context().add_class("hint")
        root.pack_start(foot, False, False, 0)

        self.connect("key-press-event", self._on_key)
        self._rebuild_home()

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)

    def _settings_obj(self, schema: str) -> Gio.Settings | None:
        if schema in self._settings:
            return self._settings[schema]
        source = Gio.SettingsSchemaSource.get_default()
        if source.lookup(schema, True) is None:
            return None
        settings = Gio.Settings.new(schema)
        self._settings[schema] = settings
        return settings

    def _on_search(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_timeout:
            GLib.source_remove(self._search_timeout)
            self._search_timeout = 0
        self._search_timeout = GLib.timeout_add(SEARCH_DELAY_MS, self._apply_search)

    def _apply_search(self) -> bool:
        self._search_timeout = 0
        if self._building:
            return False
        q = (self.search.get_text() or "").strip()
        type_filter = self.type_combo.get_active_id() or "all"
        group = self.group_combo.get_active_id() or ""
        if q or type_filter != "all" or group:
            self._rebuild_keys()
        else:
            self._rebuild_home()
        return False

    def _clear_filters(self, *_args) -> None:
        self._schema_filter = ""
        self._building = True
        self.group_combo.set_active_id("")
        self.type_combo.set_active_id("all")
        self._building = False
        if (self.search.get_text() or "").strip():
            self.search.set_text("")
            return
        self._rebuild_home()

    def _on_key(self, _w, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
                return True
            if self._schema_filter or (self.group_combo.get_active_id() or ""):
                self._clear_filters()
                return True
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and event.keyval in (Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_l, Gdk.KEY_L):
            self.search.grab_focus()
            return True
        if ctrl and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_w, Gdk.KEY_W):
            self.close()
            return True
        return False

    def _clear_page(self) -> None:
        for child in list(self.page.get_children()):
            self.page.remove(child)
        self._rows.clear()

    def _section(self, text: str) -> Gtk.Widget:
        lab = Gtk.Label(label=text)
        lab.set_xalign(0)
        lab.get_style_context().add_class("section")
        return lab

    def _rebuild_home(self) -> None:
        self._building = True
        self._clear_page()
        fav_recs = [self.by_id[f] for f in sorted(self.favorites) if f in self.by_id]
        if fav_recs:
            self.page.pack_start(self._keys_expander("Starred", fav_recs, expanded=True), False, False, 0)
        quick = [self.by_id[f"{s} {k}"] for s, k in QUICK if f"{s} {k}" in self.by_id]
        self.page.pack_start(
            self._keys_expander("Quick controls", quick, expanded=True), False, False, 0
        )
        self.page.pack_start(self._section("All categories"), False, False, 0)
        for group in GROUP_ORDER:
            schemas = self._grouped.get(group) or {}
            if not schemas:
                continue
            total = sum(len(v) for v in schemas.values())
            self.page.pack_start(
                self._category_expander(group, schemas, total, expanded=False),
                False,
                False,
                0,
            )
        self.page.show_all()
        self.back_btn.set_sensitive(False)
        self._set_status(
            f"{len(self.catalog)} settings · open a category dropdown for switches and sliders"
        )
        self._finish_build()

    def _dropdown(self, title: str, extra: str = "") -> Gtk.Box:
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header = Gtk.Button()
        header.get_style_context().add_class("drop")
        lab = Gtk.Label()
        lab.set_xalign(0)
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        lab.get_style_context().add_class("exp")
        header.add(lab)
        reveal = Gtk.Revealer()
        reveal.set_transition_type(Gtk.RevealerTransitionType.NONE)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_start(10)
        inner.set_margin_top(4)
        inner.set_margin_bottom(4)
        reveal.add(inner)

        def caption(open_: bool) -> str:
            arrow = "▾" if open_ else "▸"
            body = title if not extra else f"{title}  ·  {extra}"
            return f"{arrow}  {body}"

        def set_open(open_: bool) -> None:
            reveal.set_reveal_child(open_)
            lab.set_text(caption(open_))
            fill = getattr(shell, "_fill", None)
            if open_ and callable(fill):
                fill()

        def toggle(_btn: Gtk.Button) -> None:
            set_open(not reveal.get_reveal_child())

        lab.set_text(caption(False))
        header.connect("clicked", toggle)
        shell.pack_start(header, False, False, 0)
        shell.pack_start(reveal, False, False, 0)
        shell._inner = inner
        shell._set_open = set_open
        shell._fill = None
        return shell

    def _keys_expander(self, title: str, recs: list[KeyRec], expanded: bool) -> Gtk.Widget:
        drop = self._dropdown(title, str(len(recs)))
        for rec in recs:
            drop._inner.pack_start(self._key_row(rec), False, False, 0)
        if expanded:
            drop._set_open(True)
        return drop

    def _category_expander(
        self,
        group: str,
        schemas: dict[str, list[KeyRec]],
        total: int,
        expanded: bool,
        prefill: dict[str, list[KeyRec]] | None = None,
    ) -> Gtk.Widget:
        extra = f"{total} settings  ·  {len(schemas)} menus"
        drop = self._dropdown(group, extra)
        inner = drop._inner
        launch = GROUP_LAUNCH.get(group)
        if launch:
            open_btn = Gtk.Button(label=f"Open {launch[0]}")
            open_btn.get_style_context().add_class("open")
            open_btn.set_halign(Gtk.Align.START)
            open_btn.connect("clicked", lambda *_a, cmd=launch[1]: self._launch(cmd))
            inner.pack_start(open_btn, False, False, 0)
        if prefill is not None:
            for schema, recs in sorted(prefill.items()):
                inner.pack_start(
                    self._keys_expander(schema, recs, expanded=True), False, False, 0
                )
            drop._set_open(True)
            return drop

        def fill() -> None:
            if getattr(drop, "_built", False):
                return
            drop._built = True
            for schema, recs in sorted(schemas.items()):
                inner.pack_start(self._schema_expander(schema, recs), False, False, 0)
            inner.show_all()

        drop._fill = fill
        if expanded:
            drop._set_open(True)
        return drop

    def _schema_expander(self, schema: str, recs: list[KeyRec]) -> Gtk.Widget:
        drop = self._dropdown(schema, str(len(recs)))
        inner = drop._inner

        def fill() -> None:
            if getattr(drop, "_built", False):
                return
            drop._built = True
            type_filter = self.type_combo.get_active_id() or "all"
            q = (self.search.get_text() or "").strip().lower()
            terms = q.split()
            for rec in recs:
                if not type_matches(rec, type_filter):
                    continue
                if terms and not all(t in rec.haystack for t in terms):
                    continue
                inner.pack_start(self._key_row(rec), False, False, 0)
            if not inner.get_children():
                empty = Gtk.Label(label="No matching settings in this menu.")
                empty.get_style_context().add_class("hint")
                empty.set_xalign(0)
                inner.pack_start(empty, False, False, 0)
            inner.show_all()

        drop._fill = fill
        return drop

    def _launch(self, cmd: list[str]) -> None:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            self._set_status("Opened · " + " ".join(cmd))
        except OSError as exc:
            self._set_status(f"Could not open: {exc}")

    def _rebuild_keys(self) -> None:
        self._building = True
        self._clear_page()
        q = (self.search.get_text() or "").strip()
        type_filter = self.type_combo.get_active_id() or "all"
        group = self.group_combo.get_active_id() or ""
        schema_filter = self._schema_filter
        shown, total = match_keys(self.catalog, q, type_filter, schema_filter, None)
        if group:
            shown = [r for r in shown if group_for(r.schema) == group]
            total = len(shown)
        if not shown:
            empty = Gtk.Label(label="No matching settings.")
            empty.get_style_context().add_class("hint")
            empty.set_xalign(0)
            self.page.pack_start(empty, False, False, 0)
            self.page.show_all()
            self._set_status("No matches")
            self.back_btn.set_sensitive(True)
            self._finish_build()
            return

        capped = shown[:MAX_ROWS] if q else shown
        by_group: dict[str, dict[str, list[KeyRec]]] = defaultdict(dict)
        for rec in capped:
            g = group_for(rec.schema)
            by_group[g].setdefault(rec.schema, []).append(rec)
        for g in GROUP_ORDER:
            schemas = by_group.get(g)
            if not schemas:
                continue
            n = sum(len(v) for v in schemas.values())
            self.page.pack_start(
                self._category_expander(g, schemas, n, expanded=True, prefill=schemas),
                False,
                False,
                0,
            )
        self.page.show_all()
        extra = f" · showing {len(capped)} of {total}" if total > len(capped) else f" · {total}"
        where = schema_filter or group or "search"
        self._set_status(f"{where}{extra}")
        self.back_btn.set_sensitive(True)
        self._finish_build()

    def _finish_build(self) -> None:
        if self._end_build_id:
            GLib.source_remove(self._end_build_id)
        self._end_build_id = GLib.idle_add(self._end_build)

    def _end_build(self) -> bool:
        self._end_build_id = 0
        self._building = False
        return False

    def _key_row(self, rec: KeyRec) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_margin_start(6)
        outer.set_margin_end(6)
        outer.set_margin_top(4)
        outer.set_margin_bottom(4)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        star = Gtk.ToggleButton(label="★" if rec.fid in self.favorites else "☆")
        star.get_style_context().add_class("star")
        star.set_active(rec.fid in self.favorites)
        star.set_tooltip_text("Keep on the home list")
        star.connect("toggled", self._toggle_fav, rec)
        top.pack_start(star, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=rec.title)
        title.set_xalign(0)
        title.set_line_wrap(True)
        title.get_style_context().add_class("key")
        sub = Gtk.Label(label=f"{rec.schema}  ·  {rec.key}  ·  {rec.type_str}")
        sub.set_xalign(0)
        sub.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        sub.get_style_context().add_class("schema")
        text.pack_start(title, False, False, 0)
        text.pack_start(sub, False, False, 0)
        if rec.description:
            desc = Gtk.Label(label=rec.description)
            desc.set_xalign(0)
            desc.set_line_wrap(True)
            desc.get_style_context().add_class("summary")
            text.pack_start(desc, False, False, 0)
        top.pack_start(text, True, True, 0)

        control, extra = self._make_control(rec)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            top.pack_start(control, False, False, 0)

        reset = Gtk.Button(label="default")
        reset.get_style_context().add_class("reset")
        reset.set_tooltip_text("Reset to the GNOME default")
        reset.connect("clicked", lambda *_a, r=rec: self._reset(r))
        settings = self._settings_obj(rec.schema)
        if settings is not None:
            reset.set_sensitive(settings.get_user_value(rec.key) is not None)
        top.pack_start(reset, False, False, 0)
        outer.pack_start(top, False, False, 0)
        if extra is not None:
            extra.set_margin_start(36)
            extra.set_hexpand(True)
            outer.pack_start(extra, False, False, 0)

        wrap = Gtk.EventBox()
        wrap.get_style_context().add_class("setting")
        wrap.add(outer)
        wrap.connect("button-press-event", self._row_press, rec)
        self._rows[rec.fid] = reset
        return wrap

    def _make_control(self, rec: KeyRec) -> tuple[Gtk.Widget | None, Gtk.Widget | None]:
        settings = self._settings_obj(rec.schema)
        if settings is None:
            return None, None
        writable = settings.is_writable(rec.key)
        try:
            current = settings.get_value(rec.key).unpack()
        except Exception:
            current = None

        if rec.type_str == "b":
            sw = Gtk.Switch()
            sw.set_active(bool(current))
            sw.set_sensitive(writable)
            sw.connect("state-set", self._on_switch, rec)
            return sw, None

        if rec.enum_values:
            combo = Gtk.ComboBoxText()
            for val in rec.enum_values:
                combo.append(val, val)
            if current is not None:
                combo.set_active_id(str(current))
            combo.set_sensitive(writable)
            combo.connect("changed", self._on_combo, rec)
            return combo, None

        if rec.type_str in {"i", "u", "n", "q", "y", "x", "t", "d"}:
            is_float = rec.type_str == "d"
            if rec.range_min is not None:
                lo = rec.range_min
            elif rec.type_str in {"n"}:
                lo = -32768
            elif rec.type_str in {"i", "x"} or is_float:
                lo = -10_000 if not is_float else -1000.0
            else:
                lo = 0
            if rec.range_max is not None:
                hi = rec.range_max
            else:
                hi = 1000.0 if is_float else 10_000
            adj = Gtk.Adjustment(
                value=float(current or 0),
                lower=float(lo),
                upper=float(hi),
                step_increment=0.05 if is_float else 1,
                page_increment=0.5 if is_float else 10,
            )
            spin = Gtk.SpinButton(
                adjustment=adj, climb_rate=0.05 if is_float else 1, digits=3 if is_float else 0
            )
            spin.set_numeric(True)
            spin.set_sensitive(writable)
            spin.set_width_chars(7 if is_float else 6)
            if wants_slider(rec):
                scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
                scale.set_draw_value(False)
                scale.set_hexpand(True)
                scale.set_sensitive(writable)
                adj.connect("value-changed", self._on_adj, rec, is_float)
                return spin, scale
            spin.connect("value-changed", self._on_spin, rec, is_float)
            return spin, None

        entry = Gtk.Entry()
        entry.set_text(_fmt(current) if current is not None else "")
        entry.set_width_chars(16)
        entry.set_sensitive(writable)
        if rec.type_str not in SIMPLE:
            entry.set_tooltip_text("Raw value. Press Enter to save.")
        entry.connect("activate", self._on_entry, rec)
        entry.connect("focus-out-event", self._on_entry_focus, rec)
        return entry, None

    def _on_switch(self, switch: Gtk.Switch, state: bool, rec: KeyRec) -> bool:
        if self._building:
            return False
        err = self._write(rec, bool(state))
        if err:
            self._set_status(err)
            return True
        self._set_status(f"Saved · {rec.title} = {'on' if state else 'off'}")
        return False

    def _on_combo(self, combo: Gtk.ComboBoxText, rec: KeyRec) -> None:
        if self._building:
            return
        val = combo.get_active_id()
        if val is None:
            return
        err = self._write(rec, val)
        self._set_status(err or f"Saved · {rec.title} = {val}")

    def _on_spin(self, spin: Gtk.SpinButton, rec: KeyRec, is_float: bool) -> None:
        if self._building:
            return
        self._commit_number(spin.get_value(), rec, is_float)

    def _on_adj(self, adj: Gtk.Adjustment, rec: KeyRec, is_float: bool) -> None:
        if self._building:
            return
        self._commit_number(adj.get_value(), rec, is_float)

    def _commit_number(self, raw: float, rec: KeyRec, is_float: bool) -> None:
        val: int | float = raw if is_float else int(raw)
        if rec.type_str in {"u", "t", "q", "y"}:
            val = int(max(0, val))
        err = self._write(rec, val)
        self._set_status(err or f"Saved · {rec.title} = {val}")

    def _on_entry(self, entry: Gtk.Entry, rec: KeyRec) -> None:
        if self._building:
            return
        err = self._write_text(rec, entry.get_text())
        self._set_status(err or f"Saved · {rec.title}")

    def _on_entry_focus(self, entry: Gtk.Entry, _event, rec: KeyRec) -> bool:
        self._on_entry(entry, rec)
        return False

    def _write_text(self, rec: KeyRec, text: str) -> str | None:
        if rec.type_str == "as":
            parts = [p.strip() for p in text.split(",") if p.strip()]
            return self._write(rec, parts)
        if rec.type_str == "s":
            return self._write(rec, text)
        if rec.type_str in {"i", "n", "x"}:
            try:
                return self._write(rec, int(text.strip() or "0"))
            except ValueError:
                return "Need a whole number"
        if rec.type_str in {"u", "t", "q", "y"}:
            try:
                return self._write(rec, int(text.strip() or "0"))
            except ValueError:
                return "Need a whole number"
        if rec.type_str == "d":
            try:
                return self._write(rec, float(text.strip() or "0"))
            except ValueError:
                return "Need a number"
        if rec.type_str == "b":
            low = text.strip().lower()
            return self._write(rec, low in {"1", "true", "yes", "on"})
        settings = self._settings_obj(rec.schema)
        if settings is None:
            return "Schema missing"
        try:
            current = settings.get_value(rec.key)
            parsed = GLib.Variant.parse(current.get_type(), text.strip())
        except Exception as exc:
            return f"Could not parse value: {exc}"
        return self._write_variant(rec, parsed)

    def _write(self, rec: KeyRec, value: object) -> str | None:
        try:
            variant = GLib.Variant(rec.type_str, value)
        except Exception as exc:
            return f"Invalid value: {exc}"
        return self._write_variant(rec, variant)

    def _write_variant(self, rec: KeyRec, variant: GLib.Variant) -> str | None:
        settings = self._settings_obj(rec.schema)
        if settings is None:
            return "Schema missing"
        if not settings.is_writable(rec.key):
            return "This setting is locked"
        self._writing = True
        try:
            ok = settings.set_value(rec.key, variant)
            if not ok:
                return "Could not write this setting"
            reset = self._rows.get(rec.fid)
            if isinstance(reset, Gtk.Button):
                reset.set_sensitive(settings.get_user_value(rec.key) is not None)
            return None
        except Exception as exc:
            return str(exc)
        finally:
            self._writing = False

    def _reset(self, rec: KeyRec) -> None:
        settings = self._settings_obj(rec.schema)
        if settings is None:
            return
        self._writing = True
        try:
            settings.reset(rec.key)
        finally:
            self._writing = False
        self._set_status(f"Reset · {rec.title}")
        q = (self.search.get_text() or "").strip()
        type_filter = self.type_combo.get_active_id() or "all"
        group = self.group_combo.get_active_id() or ""
        if q or self._schema_filter or type_filter != "all" or group:
            self._rebuild_keys()
        else:
            self._rebuild_home()

    def _toggle_fav(self, btn: Gtk.ToggleButton, rec: KeyRec) -> None:
        if self._building:
            return
        if btn.get_active():
            self.favorites.add(rec.fid)
            btn.set_label("★")
        else:
            self.favorites.discard(rec.fid)
            btn.set_label("☆")
        save_favorites(self.favorites)

    def _row_press(self, _row, event, rec: KeyRec) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        items = [
            ("Reset to default", lambda *_: self._reset(rec)),
            ("Copy key", lambda *_: self._copy(f"{rec.schema} {rec.key}")),
            ("Open in dconf Editor", lambda *_: self._open_dconf(rec)),
        ]
        for label, fn in items:
            it = Gtk.MenuItem(label=label)
            it.connect("activate", fn)
            menu.append(it)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _copy(self, text: str) -> None:
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(text, -1)
        self._set_status(f"Copied · {text}")

    def _open_dconf(self, rec: KeyRec) -> None:
        settings = self._settings_obj(rec.schema)
        path = ""
        if settings is not None:
            try:
                path = str(settings.get_property("path") or "")
            except Exception:
                path = ""
        if not path:
            path = "/" + rec.schema.replace(".", "/") + "/"
        target = f"{path}{rec.key}"
        subprocess.Popen(["dconf-editor", target], start_new_session=True)
        self._set_status(f"Opened dconf Editor · {target}")


class GodModeApp(Gtk.Application):
    def __init__(self, catalog: list[KeyRec]):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None
        self.catalog = catalog

    def do_activate(self):
        if self.win is None:
            self.win = GodModeWindow(self, self.catalog)
            self.win.show_all()
            self.win.search.grab_focus()
        self.win.present()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    GLib.set_prgname(PRGNAME)
    catalog = build_catalog()
    if "--self-test" in argv:
        return _self_test(catalog)
    app = GodModeApp(catalog)
    return app.run(argv)


def _self_test(catalog: list[KeyRec]) -> int:
    assert len(catalog) > 800, len(catalog)
    hits, total = match_keys(catalog, "enable-animations", "all", "", 50)
    assert any(r.key == "enable-animations" for r in hits), [r.key for r in hits]
    rec = next(
        r
        for r in catalog
        if r.schema == "org.gnome.desktop.interface" and r.key == "enable-animations"
    )
    s = Gio.Settings.new(rec.schema)
    cur = s.get_value(rec.key)
    ok = s.set_value(rec.key, cur)
    assert ok
    assert rec.type_str == "b"
    unknown = {group_for(r.schema) for r in catalog} - set(GROUP_ORDER)
    assert not unknown, unknown
    assert group_for("org.gnome.desktop.interface") == "Look"
    assert group_for("org.gnome.shell.extensions.dash-to-dock") == "Dock"
    assert group_for("org.gnome.desktop.a11y.keyboard") == "Accessibility"
    scale = next(
        r
        for r in catalog
        if r.schema == "org.gnome.desktop.interface" and r.key == "text-scaling-factor"
    )
    assert wants_slider(scale), (scale.range_min, scale.range_max, scale.type_str)
    assert not wants_slider(rec)
    switches, n_sw = match_keys(catalog, "", "switches", "", None)
    assert n_sw > 100, n_sw
    assert all(r.type_str == "b" for r in switches)
    print(
        f"ok catalog={len(catalog)} animations_hits={total} "
        f"groups={len({group_for(r.schema) for r in catalog})} switches={n_sw}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
