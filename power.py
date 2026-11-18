# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Keeps Windows from going to sleep while media is being streamed.

It uses the standard Windows power request API (PowerCreateRequest,
PowerSetRequest, PowerClearRequest), the same mechanism media players use.
While a transfer is active the service appears in:

    powercfg /requests

under SYSTEM, with the reason "Easy DLNA Server is streaming media".

The HTTP server reports every media transfer with stream_started() and
stream_finished() (or with the streaming() context manager). The system is kept
awake while at least one transfer is active and for GRACE_SECONDS after the last
one ends. The display is still allowed to turn off.

If the power request cannot be created, it falls back to
SetThreadExecutionState.

On Linux it uses systemd-inhibit (visible with: systemd-inhibit --list) and on
macOS caffeinate, started only while media is being streamed.

A system service runs as an unprivileged user without a login session, and
logind refuses it a sleep lock ("Access denied"). In that case the unit sets
EASYDLNA_AWAKE_FLAG to a file path: the service creates that file while it
streams and removes it afterwards, and a small root helper unit
(easydlna-awake.service, started by easydlna-awake.path) holds the lock for as
long as the file exists.
"""

import ctypes
import os
import subprocess
import sys
import threading
import time

from contextlib import contextmanager

from process_env import clean_environment


REASON_TEXT = "Easy DLNA Server is streaming media"

# Players often pause or buffer without closing the connection, or reopen it
# for every range request. Keep the system awake for a while after the last
# transfer ends so short gaps do not allow sleep.
GRACE_SECONDS = 300

# After a failure to start sleep prevention, wait before trying again, so a TV
# that opens many connections does not cause a flood of attempts and messages.
RETRY_SECONDS = 60

AWAKE_FLAG_VARIABLE = "EASYDLNA_AWAKE_FLAG"

POWER_REQUEST_CONTEXT_VERSION = 0
POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
POWER_REQUEST_SYSTEM_REQUIRED = 1

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


# ---------------------------------------------------------
# WINDOWS STRUCTURES
# ---------------------------------------------------------

class _ReasonDetailed(ctypes.Structure):
    _fields_ = [
        ("LocalizedReasonModule", ctypes.c_void_p),
        ("LocalizedReasonId", ctypes.c_uint32),
        ("ReasonStringCount", ctypes.c_uint32),
        ("ReasonStrings", ctypes.c_void_p),
    ]


class _ReasonUnion(ctypes.Union):
    _fields_ = [
        ("Detailed", _ReasonDetailed),
        ("SimpleReasonString", ctypes.c_wchar_p),
    ]


class ReasonContext(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("Reason", _ReasonUnion),
    ]


# ---------------------------------------------------------
# BACKENDS
# ---------------------------------------------------------

class NullBackend:

    """Used on systems without a sleep prevention mechanism."""

    def set_active(self, active):
        pass

    def close(self):
        pass


class ProcessBackend:

    """
    Linux and macOS: keeps a helper process alive while sleep must be
    prevented (systemd-inhibit or caffeinate) and stops it afterwards.
    """

    def __init__(self, command):

        self._command = list(command)
        self._process = None

    def set_active(self, active):

        if active:

            if self._process is not None and self._process.poll() is None:
                return

            try:
                self._process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=clean_environment()
                )
            except OSError as error:
                self._process = None
                raise OSError(
                    f"{self._command[0]} is not available: {error}"
                ) from error

            # A helper that exits at once was refused (for example by policy).
            time.sleep(0.3)

            code = self._process.poll()

            if code is not None:

                reason = (
                    self._process.stderr.read() or b""
                ).decode("utf-8", errors="replace").strip()

                self._process.stderr.close()
                self._process = None

                if "access denied" in reason.lower():
                    reason += (
                        " (the service user may need permission to inhibit "
                        "sleep: a polkit rule for the actions "
                        "org.freedesktop.login1.inhibit-block-sleep and "
                        "inhibit-block-idle)"
                    )

                raise OSError(
                    f"{self._command[0]} exited with code {code}"
                    + (f": {reason}" if reason else ".")
                )

            return

        self._stop_process()

    def _stop_process(self):

        process, self._process = self._process, None

        if process is None:
            return

        process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

        if process.stderr is not None:
            process.stderr.close()

    def close(self):
        self._stop_process()


class FlagFileBackend:

    """
    Linux system service: a file that exists while media is streaming. A root
    helper unit holds the sleep lock for as long as the file is there.
    """

    def __init__(self, path):

        self._path = path

    def set_active(self, active):

        if active:

            os.makedirs(os.path.dirname(self._path), exist_ok=True)

            with open(self._path, "a"):
                os.utime(self._path, None)

            return

        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def close(self):
        self.set_active(False)


LINUX_COMMAND = [
    "systemd-inhibit",
    "--what=sleep:idle",
    "--who=Easy DLNA Server",
    "--why=Streaming media",
    "--mode=block",
    "sleep",
    "infinity"
]

MACOS_COMMAND = ["caffeinate", "-i", "-s"]


class PowerRequestBackend:

    """Windows power request: shown by powercfg /requests with a reason."""

    def __init__(self, reason):

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._create = kernel32.PowerCreateRequest
        self._create.argtypes = [ctypes.POINTER(ReasonContext)]
        self._create.restype = ctypes.c_void_p

        self._set = kernel32.PowerSetRequest
        self._set.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._set.restype = ctypes.c_int

        self._clear = kernel32.PowerClearRequest
        self._clear.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._clear.restype = ctypes.c_int

        self._close = kernel32.CloseHandle
        self._close.argtypes = [ctypes.c_void_p]
        self._close.restype = ctypes.c_int

        self._context = ReasonContext()
        self._context.Version = POWER_REQUEST_CONTEXT_VERSION
        self._context.Flags = POWER_REQUEST_CONTEXT_SIMPLE_STRING
        self._context.Reason.SimpleReasonString = reason

        invalid = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1
        handle = self._create(ctypes.byref(self._context))

        if handle is None or handle == 0 or handle == invalid:
            raise OSError(
                ctypes.get_last_error(),
                "PowerCreateRequest failed."
            )

        self._handle = handle
        self._active = False

    def set_active(self, active):

        if active == self._active:
            return

        function = self._set if active else self._clear

        if not function(self._handle, POWER_REQUEST_SYSTEM_REQUIRED):
            raise OSError(
                ctypes.get_last_error(),
                "Could not change the power request."
            )

        self._active = active

    def close(self):

        try:
            self.set_active(False)
        finally:
            if self._handle is not None:
                self._close(self._handle)
                self._handle = None


class ExecutionStateBackend:

    """
    Fallback: SetThreadExecutionState applies to the calling thread, so it must
    always be called from the same thread (the inhibitor's worker thread).
    """

    def __init__(self):

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._function = kernel32.SetThreadExecutionState
        self._function.argtypes = [ctypes.c_uint32]
        self._function.restype = ctypes.c_uint32

        self._active = False

    def set_active(self, active):

        if active == self._active:
            return

        flags = ES_CONTINUOUS

        if active:
            flags |= ES_SYSTEM_REQUIRED

        if self._function(flags) == 0:
            raise OSError(
                ctypes.get_last_error(),
                "SetThreadExecutionState failed."
            )

        self._active = active

    def close(self):
        self.set_active(False)


def make_backend():

    if sys.platform == "darwin":
        return ProcessBackend(MACOS_COMMAND)

    if sys.platform.startswith("linux"):

        flag = os.environ.get(AWAKE_FLAG_VARIABLE)

        if flag:
            return FlagFileBackend(flag)

        return ProcessBackend(LINUX_COMMAND)

    if os.name != "nt":
        return NullBackend()

    try:
        return PowerRequestBackend(REASON_TEXT)

    except Exception as error:
        print(
            f"[POWER] Power request unavailable ({error}); "
            "using SetThreadExecutionState instead."
        )
        return ExecutionStateBackend()


# ---------------------------------------------------------
# INHIBITOR
# ---------------------------------------------------------

class SleepInhibitor:

    def __init__(
        self,
        grace_seconds=GRACE_SECONDS,
        backend_factory=make_backend
    ):

        self._grace = grace_seconds
        self._backend_factory = backend_factory

        self._lock = threading.Lock()
        self._active = 0
        self._idle_since = None

        self._changed = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    # -----------------------------------------------------
    # LIFE CYCLE
    # -----------------------------------------------------

    def start(self):

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop.clear()
        self._changed.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="SleepInhibitor",
            daemon=True
        )
        self._thread.start()

    def stop(self):

        self._stop.set()
        self._changed.set()

        if self._thread is not None:
            self._thread.join(timeout=5)

        self._thread = None

    # -----------------------------------------------------
    # STREAM REPORTING
    # -----------------------------------------------------

    def stream_started(self):

        with self._lock:
            self._active += 1
            self._idle_since = None

        self._changed.set()

    def stream_finished(self):

        with self._lock:

            if self._active > 0:
                self._active -= 1

            if self._active == 0:
                self._idle_since = time.monotonic()

        self._changed.set()

    @contextmanager
    def streaming(self):

        self.stream_started()

        try:
            yield
        finally:
            self.stream_finished()

    # -----------------------------------------------------
    # WORKER THREAD
    # -----------------------------------------------------

    def _run(self):

        try:
            backend = self._backend_factory()
        except Exception as error:
            print(f"[POWER] Could not start sleep prevention: {error}")
            return

        inhibiting = False
        retry_after = 0.0

        try:

            while not self._stop.is_set():

                with self._lock:
                    active = self._active
                    idle_since = self._idle_since

                timeout = None

                if active > 0:
                    wanted = True

                elif idle_since is not None:
                    remaining = self._grace - (
                        time.monotonic() - idle_since
                    )
                    wanted = remaining > 0

                    if wanted:
                        timeout = remaining

                else:
                    wanted = False

                if wanted != inhibiting:

                    now = time.monotonic()

                    if now >= retry_after:

                        try:
                            backend.set_active(wanted)
                        except Exception as error:
                            retry_after = now + RETRY_SECONDS
                            print(
                                "[POWER] Could not change sleep prevention "
                                f"(trying again in {RETRY_SECONDS} s if still "
                                f"needed): {error}"
                            )
                        else:
                            inhibiting = wanted
                            retry_after = 0.0

                            if wanted:
                                print(
                                    "[POWER] Streaming active: "
                                    "preventing system sleep."
                                )
                            else:
                                print(
                                    "[POWER] Streaming idle: "
                                    "system sleep allowed again."
                                )

                    if wanted != inhibiting:

                        pause = max(0.0, retry_after - time.monotonic())
                        timeout = pause if timeout is None else min(timeout, pause)

                self._changed.wait(timeout)
                self._changed.clear()

        finally:

            try:
                backend.close()
            except Exception:
                pass


# One shared instance for the whole server.
inhibitor = SleepInhibitor()


def start():
    inhibitor.start()


def stop():
    inhibitor.stop()


def stream_started():
    inhibitor.stream_started()


def stream_finished():
    inhibitor.stream_finished()


def streaming():
    return inhibitor.streaming()
