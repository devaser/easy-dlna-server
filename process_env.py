# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Environment for the programs that Easy DLNA Server starts (systemctl,
systemd-inhibit, caffeinate, ufw, journalctl...).

A program frozen with PyInstaller runs with LD_LIBRARY_PATH (Linux) or
DYLD_LIBRARY_PATH (macOS) pointing at its own bundled libraries, and keeps the
original value in a variable ending in _ORIG. Programs of the system must get
the original value back, or they may load the wrong libraries and fail.
"""

import os
import sys


LIBRARY_PATH_VARIABLES = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH"
)


def clean_environment(environ=None, frozen=None):

    environment = dict(os.environ if environ is None else environ)

    if frozen is None:
        frozen = getattr(sys, "frozen", False)

    for name in LIBRARY_PATH_VARIABLES:

        original = environment.pop(name + "_ORIG", None)

        if original is not None:
            environment[name] = original

        elif frozen:
            environment.pop(name, None)

    return environment
