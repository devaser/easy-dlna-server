# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Where Easy DLNA Server keeps its data (database, configuration, logs).

  Windows            %ProgramData%\\Easy DLNA Server
  Linux, user mode   ~/.local/share/easydlna            ($XDG_DATA_HOME)
  Linux, system mode /var/lib/easydlna
  macOS, user mode   ~/Library/Application Support/Easy DLNA Server
  macOS, system mode /Library/Application Support/Easy DLNA Server

System mode is used when the program runs as root or as the "easydlna"
service user. The EASYDLNA_DATA_DIR environment variable overrides everything.
"""

import os
import sys

from pathlib import Path


WINDOWS_FOLDER = "Easy DLNA Server"
UNIX_FOLDER = "easydlna"

SERVICE_USER = "easydlna"
SYSTEM_DATA_DIR = Path("/var/lib/easydlna")


def current_user_name(euid=None):

    try:
        import pwd

        if euid is None:
            euid = os.geteuid()

        return pwd.getpwuid(euid).pw_name

    except Exception:
        return None


def is_system_mode(euid=None, user_name=None):
    """True when running as root or as the service user (Linux and macOS)."""

    if euid is None and hasattr(os, "geteuid"):
        euid = os.geteuid()

    if euid == 0:
        return True

    if user_name is None:
        user_name = current_user_name(euid)

    return user_name == SERVICE_USER


def get_data_dir(
    platform=None,
    environ=None,
    euid=None,
    user_name=None,
    home=None
):

    platform = platform or sys.platform
    environ = os.environ if environ is None else environ

    override = environ.get("EASYDLNA_DATA_DIR")

    if override:
        return Path(override).expanduser().resolve()

    if platform.startswith("win"):
        return (
            Path(environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / WINDOWS_FOLDER
        ).resolve()

    system_mode = is_system_mode(euid, user_name)
    home = Path(home) if home else Path.home()

    if platform == "darwin":

        if system_mode:
            return Path("/Library/Application Support") / WINDOWS_FOLDER

        return (
            home / "Library" / "Application Support" / WINDOWS_FOLDER
        )

    if system_mode:
        return SYSTEM_DATA_DIR

    base = environ.get("XDG_DATA_HOME")
    base = Path(base) if base else home / ".local" / "share"

    return base / UNIX_FOLDER
