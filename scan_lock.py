# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Prevents two library scans from running at the same time.

The scanner can be started from the tray and from Settings. The first one
takes a named Windows mutex; a second one finds it taken and exits with
SCAN_BUSY_EXIT_CODE, which the tray and Settings report as "already running".
The mutex disappears by itself when the scanner ends, even if it crashes.

The mutex is per user session (the "Local\\" namespace), which covers the tray
and Settings of the same user. If the lock cannot be created for any reason,
the scan runs without it.

On Linux and macOS the lock is an exclusive flock on a "scan.lock" file in the
data folder, which the system releases by itself when the process ends.
"""

import ctypes
import os

from pathlib import Path

from platform_paths import get_data_dir


MUTEX_NAME = "Local\\EasyDLNA_LibraryScan"

# Exit code of the scanner when another scan is already running.
SCAN_BUSY_EXIT_CODE = 3

ERROR_ALREADY_EXISTS = 183


class WindowsMutex:

    def __init__(self):

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._create = kernel32.CreateMutexW
        self._create.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_wchar_p
        ]
        self._create.restype = ctypes.c_void_p

        self._close = kernel32.CloseHandle
        self._close.argtypes = [ctypes.c_void_p]
        self._close.restype = ctypes.c_int

    def create(self, name):

        ctypes.set_last_error(0)

        handle = self._create(None, False, name)

        return handle, ctypes.get_last_error()

    def close(self, handle):
        self._close(handle)


class FileLock:

    """Linux and macOS: exclusive lock on a file (released by the system)."""

    def __init__(self, path=None):
        self._path = Path(path) if path else get_data_dir() / "scan.lock"

    def create(self, name):

        import fcntl

        self._path.parent.mkdir(parents=True, exist_ok=True)

        descriptor = os.open(
            str(self._path),
            os.O_RDWR | os.O_CREAT,
            0o666
        )

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        except OSError:
            # Held by another process: same meaning as "already exists".
            return descriptor, ERROR_ALREADY_EXISTS

        return descriptor, 0

    def close(self, handle):

        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass

        os.close(handle)


class ScanLock:

    def __init__(self, name=MUTEX_NAME, backend=None):

        self._name = name
        self._handle = None
        self._held = False

        if backend is not None:
            self._backend = backend
        elif os.name == "nt":
            self._backend = WindowsMutex()
        else:
            self._backend = FileLock()

    def acquire(self):

        """
        Returns True if this process may scan, or False if another scan is
        already running.
        """

        if self._held:
            return True

        if self._backend is None:
            self._held = True
            return True

        try:
            handle, error = self._backend.create(self._name)

            if handle is None or (handle == 0 and os.name == "nt"):
                raise OSError(error, "CreateMutexW failed.")

        except Exception as exception:

            print(
                f"[SCANNER] Could not create the scan lock "
                f"({exception}); continuing without it.",
                flush=True
            )

            self._held = True
            return True

        if error == ERROR_ALREADY_EXISTS:
            self._backend.close(handle)
            return False

        self._handle = handle
        self._held = True

        return True

    def release(self):

        if self._handle is not None:
            self._backend.close(self._handle)
            self._handle = None

        self._held = False
