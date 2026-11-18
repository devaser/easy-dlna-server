# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

import asyncio

import power

from app_settings import get_http_port
from database.db import initialize, get_shared_folders
from media.scanner import scan_library
from server.http_server import start_http_server
from server.device import DEVICE_UUID
from server.ssdp import start_ssdp


SERVER_NAME = "Easy DLNA Server"
SERVER_VERSION = "0.3"


async def main(stop_event=None):

    print("=" * 50)
    print(f"          {SERVER_NAME} {SERVER_VERSION}")
    print("=" * 50)
    print(f"[INFO] Device ID: {DEVICE_UUID}")

    # 1. Initialize SQLite
    print("[SQLite] Initializing database...")

    try:
        initialize()
    except Exception as error:
        print(f"[ERROR] Database initialization failed: {error}")
        raise RuntimeError("Database initialization failed.") from error

    # 2. Display shared folders
    shared_folders = get_shared_folders()

    print()
    print("[MEDIA] Configured shared folders:")

    if shared_folders:
        for folder in shared_folders:
            print(f"[MEDIA]   {folder}")
    else:
        print("[WARNING] No valid shared folders configured.")

    # 3. Scan media library
    #
    # A missing or failed scan must not stop the server: on a fresh install
    # there are no shared folders yet, and a shared drive can be offline when
    # the service starts. The server keeps running with the existing library.
    print()
    scan_completed = False

    if not shared_folders:
        print("[SCANNER] Skipped: no valid shared folders configured.")
        print(
            "[INFO] Add folders in Settings, then use "
            "'Scan and update library' from the tray."
        )
    else:
        print("[SCANNER] Scanning media library...")

        try:
            videos, subtitles, errors = await asyncio.to_thread(
                scan_library
            )

            print(f"[SCANNER] Videos processed: {videos}")
            print(f"[SCANNER] Subtitles processed: {subtitles}")
            print(f"[SCANNER] Errors: {errors}")

            scan_completed = True

        except Exception as error:
            print(f"[ERROR] Media scanner failed: {error}")
            print("[WARNING] Continuing with the existing library.")

    runner = None
    ssdp_transport = None
    notify_socket = None
    notify_task = None

    # Keeps Windows awake while media is being streamed (see power.py).
    power.start()

    try:

        # 4. Start HTTP server
        runner = await start_http_server()

        # 5. Start SSDP service
        (
            ssdp_transport,
            notify_socket,
            notify_task
        ) = await start_ssdp()

        print()
        print("=" * 50)
        print(f"          {SERVER_NAME} RUNNING")
        print("=" * 50)
        print("[OK] SQLite database")

        if scan_completed:
            print("[OK] Media scanner")
        else:
            print("[WARNING] Media scanner did not run")

        print("[OK] External SRT subtitles")
        print("[OK] Playback bookmarks")
        print(f"[OK] HTTP server: port {get_http_port()}")
        print("[OK] SSDP service: port 1900")
        print("[OK] SSDP NOTIFY announcements")
        print()
        print("[INFO] Waiting for DLNA client connections...")
        print("=" * 50)

        if stop_event is None:
            await asyncio.Event().wait()
        else:
            await stop_event.wait()

    finally:

        print(f"\n[INFO] Shutting down {SERVER_NAME}...")

        power.stop()

        if notify_task is not None:
            notify_task.cancel()

            try:
                await notify_task
            except asyncio.CancelledError:
                pass

        if ssdp_transport is not None:
            ssdp_transport.close()

        if notify_socket is not None:
            notify_socket.close()

        if runner is not None:
            await runner.cleanup()

        print("[OK] Server stopped.")


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\n[INFO] Shutdown requested by user.")

    except OSError as error:
        print(f"\n[ERROR] Network error: {error}")

    except Exception as error:
        print(f"\n[ERROR] Unexpected server error: {error}")
