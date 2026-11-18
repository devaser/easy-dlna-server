# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['service.py'],
    pathex=['D:\\scripts\\pydlna'],
    binaries=[],
    datas=[
        ('database/schema.sql', 'database'),
    ],
    hiddenimports=[
        'win32timezone',
        'servicemanager',
        'win32service',
        'win32serviceutil',
        'pywintypes',
        'pythoncom',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EasyDLNAService',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
