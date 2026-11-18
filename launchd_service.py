# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Easy DLNA Server as a macOS user service (a launchd LaunchAgent).

It starts when the user logs in and is restarted if it fails. System-wide
services (a LaunchDaemon) are not supported yet.

  plist  ~/Library/LaunchAgents/com.devaser.easydlna.plist
  log    ~/Library/Logs/EasyDLNAServer/easydlna.log
"""

import os
import plistlib

from pathlib import Path


LABEL = "com.devaser.easydlna"

# Seconds launchd waits before it starts the service again after a failure.
THROTTLE_INTERVAL = 60


def plist_path():

    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path():

    return Path.home() / "Library" / "Logs" / "EasyDLNAServer" / "easydlna.log"


def domain():

    return f"gui/{os.getuid()}"


def target():

    return f"{domain()}/{LABEL}"


def is_installed():

    try:
        return plist_path().exists()
    except OSError:
        return False


def plist_bytes(program_arguments):

    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(part) for part in program_arguments],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_INTERVAL,
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
        "ProcessType": "Background"
    })


def write_plist(program_arguments, dry_run=False):

    data = plist_bytes(program_arguments)

    if dry_run:
        print(f"[dry run] write {plist_path()}:")
        print(data.decode("utf-8"))
        return

    plist_path().parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)

    plist_path().write_bytes(data)
    plist_path().chmod(0o644)

    print(f"Wrote {plist_path()}")


def bootstrap_command():
    """Load the service (it starts at once because of RunAtLoad)."""

    return ["launchctl", "bootstrap", domain(), str(plist_path())]


def bootout_command():
    """Stop and unload the service."""

    return ["launchctl", "bootout", target()]


def control_command(action):
    """The launchctl command for start, stop, restart, enable or disable."""

    commands = {
        # SIGTERM makes the server exit cleanly, so launchd does not restart it.
        "stop": ["launchctl", "kill", "SIGTERM", target()],
        "start": ["launchctl", "kickstart", target()],
        "restart": ["launchctl", "kickstart", "-k", target()],
        "enable": ["launchctl", "enable", target()],
        "disable": ["launchctl", "disable", target()]
    }

    return commands[action]


def state_from_print(returncode, output):
    """'running', 'loaded (not running)' or 'not loaded', from launchctl print."""

    if returncode != 0:
        return "not loaded"

    for line in output.splitlines():

        parts = line.strip().split("=", 1)

        if len(parts) == 2 and parts[0].strip() == "state":
            return "running" if parts[1].strip() == "running" else "loaded (not running)"

    return "loaded (not running)"


def logs_command(follow):

    command = ["tail", "-n", "200"]

    if follow:
        command.append("-f")

    return command + [str(log_path())]
