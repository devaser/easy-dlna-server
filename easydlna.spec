# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)
#
# PyInstaller spec of the "easydlna" command line (Linux and macOS).
# One folder (not one file): it starts faster and does not unpack to /tmp on
# every start of the service. Build it on the same system it is meant for.
#
#   pyinstaller --noconfirm --clean packaging/easydlna.spec

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "easydlna.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "database", "schema.sql"), "database"),
    ],
    hiddenimports=[
        "main",
        "scan_library",
        "app_settings",
        "launchd_service",
        "database.db",
        "media.scanner",
        "media.subtitles",
        "server.http_server",
        "server.ssdp",
        "server.upnp",
        "server.didl",
        "server.device",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PIL",
        "pystray",
        "servicemanager",
        "win32service",
        "win32serviceutil",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="easydlna",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="easydlna",
)
