#!/usr/bin/env python3
"""
Install or remove D.A.E.M.O.N. as a per-user autostart service.

Windows  -> places a shortcut to scripts\\start_daemon.vbs in
            %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
Linux    -> installs scripts/daemon.service into
            ~/.config/systemd/user/daemon.service and enables it

Usage:
    python scripts/install_autostart.py install
    python scripts/install_autostart.py uninstall
    python scripts/install_autostart.py status
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SERVICE_NAME = "daemon.service"


# ---------- Windows ----------------------------------------------------------

def _windows_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA env var not set; cannot locate Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _windows_shortcut_path() -> Path:
    return _windows_startup_dir() / "DAEMON.lnk"


def _windows_install() -> int:
    vbs = SCRIPTS_DIR / "start_daemon.vbs"
    if not vbs.exists():
        print(f"ERROR: launcher not found: {vbs}")
        return 1

    shortcut = _windows_shortcut_path()
    shortcut.parent.mkdir(parents=True, exist_ok=True)

    # Use PowerShell to create a .lnk shortcut without extra dependencies
    ps = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut}'); "
        f"$s.TargetPath = '{vbs}'; "
        f"$s.WorkingDirectory = '{PROJECT_ROOT}'; "
        f"$s.IconLocation = 'wscript.exe,0'; "
        f"$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Shortcut creation failed:", result.stderr)
        return 1
    print(f"Installed autostart shortcut: {shortcut}")
    print("D.A.E.M.O.N. will launch silently next time you log in.")
    return 0


def _windows_uninstall() -> int:
    shortcut = _windows_shortcut_path()
    if shortcut.exists():
        shortcut.unlink()
        print(f"Removed: {shortcut}")
    else:
        print(f"Nothing to remove (shortcut not found): {shortcut}")
    return 0


def _windows_status() -> int:
    shortcut = _windows_shortcut_path()
    print(f"Autostart shortcut: {shortcut}")
    print("Installed:", shortcut.exists())
    return 0


# ---------- Linux ------------------------------------------------------------

def _linux_unit_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "systemd" / "user"


def _linux_unit_path() -> Path:
    return _linux_unit_dir() / SERVICE_NAME


def _linux_install() -> int:
    template = SCRIPTS_DIR / SERVICE_NAME
    if not template.exists():
        print(f"ERROR: template not found: {template}")
        return 1

    unit_dir = _linux_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = _linux_unit_path()

    rendered = template.read_text().replace("__PROJECT_ROOT__", str(PROJECT_ROOT))
    unit_path.write_text(rendered)
    print(f"Installed unit: {unit_path}")

    if not shutil.which("systemctl"):
        print("WARNING: systemctl not found; unit written but not enabled.")
        return 0

    for args in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", SERVICE_NAME],
        ["systemctl", "--user", "start", SERVICE_NAME],
    ):
        rc = subprocess.call(args)
        if rc != 0:
            print(f"WARNING: command failed: {' '.join(args)}")

    print("D.A.E.M.O.N. enabled. Check with: systemctl --user status daemon")
    return 0


def _linux_uninstall() -> int:
    if shutil.which("systemctl"):
        subprocess.call(["systemctl", "--user", "stop", SERVICE_NAME])
        subprocess.call(["systemctl", "--user", "disable", SERVICE_NAME])
    unit_path = _linux_unit_path()
    if unit_path.exists():
        unit_path.unlink()
        print(f"Removed: {unit_path}")
    if shutil.which("systemctl"):
        subprocess.call(["systemctl", "--user", "daemon-reload"])
    return 0


def _linux_status() -> int:
    print(f"Unit file: {_linux_unit_path()}")
    print("Installed:", _linux_unit_path().exists())
    if shutil.which("systemctl"):
        subprocess.call(["systemctl", "--user", "status", SERVICE_NAME])
    return 0


# ---------- entry ------------------------------------------------------------

ACTIONS = {
    "Windows": {
        "install":   _windows_install,
        "uninstall": _windows_uninstall,
        "status":    _windows_status,
    },
    "Linux": {
        "install":   _linux_install,
        "uninstall": _linux_uninstall,
        "status":    _linux_status,
    },
}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("install", "uninstall", "status"):
        print(__doc__)
        return 2
    system = platform.system()
    if system not in ACTIONS:
        print(f"Unsupported OS: {system}")
        return 2
    return ACTIONS[system][argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
