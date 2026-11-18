# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import sys
import traceback

from database.db import initialize
from media.scanner import scan_library
from scan_lock import ScanLock, SCAN_BUSY_EXIT_CODE


def main():
    """Initialize the database and update the media library."""

    # Only one scan at a time (the tray and Settings can both start one).
    lock = ScanLock()

    if not lock.acquire():

        print(
            "[SCANNER] Another library scan is already running.",
            flush=True
        )

        return SCAN_BUSY_EXIT_CODE

    try:

        print(
            "[SCANNER] Initializing database...",
            flush=True
        )

        initialize()

        print(
            "[SCANNER] Starting library update...",
            flush=True
        )

        videos, subtitles, errors = scan_library()

        # This exact format is also read by the tray application.
        print(
            f"Scan completed: {videos} videos, "
            f"{subtitles} subtitles, "
            f"{errors} errors",
            flush=True
        )

        return 0

    finally:

        lock.release()


if __name__ == "__main__":

    try:

        sys.exit(main())

    except Exception:

        print(
            "[SCANNER] Fatal error:",
            flush=True
        )

        traceback.print_exc()
        sys.exit(1)
