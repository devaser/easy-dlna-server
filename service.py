# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

import asyncio
import os
import sys
import threading
import traceback
from pathlib import Path

import servicemanager
import win32service
import win32serviceutil

from app_settings import get_http_port
from main import main
from windows_firewall import sync_http_rule


BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


if os.environ.get("EASYDLNA_DATA_DIR"):
    DATA_DIR = Path(
        os.environ["EASYDLNA_DATA_DIR"]
    ).expanduser().resolve()

elif os.name == "nt":
    DATA_DIR = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Easy DLNA Server"
    ).resolve()

else:
    DATA_DIR = BASE_DIR


LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "service.log"


class EasyDLNAService(win32serviceutil.ServiceFramework):

    _svc_name_ = "EasyDLNAServer"
    _svc_display_name_ = "Easy DLNA Server"

    _svc_description_ = (
        "Provides DLNA media streaming, subtitles "
        "and playback bookmarks to network devices."
    )

    def __init__(self, args):
        super().__init__(args)

        self.loop = None
        self.async_stop_event = None

        self.stop_requested = threading.Event()
        self.stop_lock = threading.Lock()

    def SvcStop(self):
        with self.stop_lock:
            if self.stop_requested.is_set():
                return

            self.stop_requested.set()

        self.ReportServiceStatus(
            win32service.SERVICE_STOP_PENDING
        )

        if self.loop is not None and self.async_stop_event is not None:
            try:
                self.loop.call_soon_threadsafe(
                    self.async_stop_event.set
                )
            except RuntimeError:
                pass

        servicemanager.LogInfoMsg(
            "Easy DLNA Server stop requested."
        )

    def SvcShutdown(self):
        self.SvcStop()

    async def run_server(self):
        self.loop = asyncio.get_running_loop()
        self.async_stop_event = asyncio.Event()

        if self.stop_requested.is_set():
            self.async_stop_event.set()

        await main(self.async_stop_event)

    def SvcDoRun(self):
        os.chdir(BASE_DIR)

        LOG_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        servicemanager.LogInfoMsg(
            "Easy DLNA Server service starting."
        )

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        try:
            with open(
                LOG_FILE,
                "a",
                encoding="utf-8",
                buffering=1
            ) as log_file:

                sys.stdout = log_file
                sys.stderr = log_file

                print()
                print("=" * 50)
                print("[SERVICE] Easy DLNA Server starting")
                print("=" * 50)

                # Keep the firewall rule in step with the configured HTTP port.
                try:
                    print(
                        "[FIREWALL] "
                        + sync_http_rule(
                            get_http_port(),
                            program=(
                                sys.executable
                                if getattr(sys, "frozen", False)
                                else None
                            )
                        )
                    )
                except Exception as error:
                    print(f"[FIREWALL] Could not update the rule: {error}")

                try:
                    asyncio.run(
                        self.run_server()
                    )

                except Exception:
                    traceback.print_exc()

                    servicemanager.LogErrorMsg(
                        "Easy DLNA Server encountered an error. "
                        "Check service.log."
                    )

        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            servicemanager.LogInfoMsg(
                "Easy DLNA Server service stopped."
            )


if __name__ == "__main__":

    if len(sys.argv) == 1:

        servicemanager.Initialize()

        servicemanager.PrepareToHostSingle(
            EasyDLNAService
        )

        servicemanager.StartServiceCtrlDispatcher()

    else:

        win32serviceutil.HandleCommandLine(
            EasyDLNAService
        )
