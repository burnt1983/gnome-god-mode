#!/usr/bin/env python3
"""Quick search box that opens GNOME Settings on a query."""
import subprocess
import sys

r = subprocess.run(
    [
        "zenity",
        "--entry",
        "--title",
        "GNOME God Mode",
        "--text",
        "Search settings:",
        "--width",
        "420",
    ],
    capture_output=True,
    text=True,
)
if r.returncode != 0:
    sys.exit(0)
q = r.stdout.strip()
if q:
    subprocess.Popen(["gnome-control-center", f"--search={q}"])
else:
    subprocess.Popen(["gnome-control-center"])
