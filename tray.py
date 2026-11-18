# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)
#
# Easy DLNA Server - system tray application.
#
# NOTE: SERVICE_NAME, the data directory name and the EASYDLNA_DATA_DIR
# environment variable must match the ones used by service.py.

import os
import re
import signal
import subprocess
import sys
import threading
import time

from pathlib import Path

import pystray
import win32service

from PIL import Image, ImageDraw

from scan_lock import SCAN_BUSY_EXIT_CODE


# ---------------------------------------------------------
# APPLICATION PATHS
# ---------------------------------------------------------

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)

EXE_DIR = (
    BASE_DIR
    if getattr(sys, "frozen", False)
    else BASE_DIR / "dist"
)

SETTINGS_SCRIPT = BASE_DIR / "shared_folders.py"
SETTINGS_EXECUTABLE = EXE_DIR / "EasyDLNASettings.exe"

SCAN_SCRIPT = BASE_DIR / "scan_library.py"
SCAN_EXECUTABLE = EXE_DIR / "EasyDLNAScanner.exe"


# ---------------------------------------------------------
# DATA PATHS
# ---------------------------------------------------------

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
SCAN_LOG_FILE = LOG_DIR / "scan.log"


# ---------------------------------------------------------
# WINDOWS SERVICE
# ---------------------------------------------------------

SERVICE_NAME = "EasyDLNAServer"

SERVICE_TIMEOUT = 30
MONITOR_INTERVAL = 3


# ---------------------------------------------------------
# CHILD APPLICATIONS
# ---------------------------------------------------------

# Seconds to wait for the Settings window to close by itself
# before it is forced to close.
SETTINGS_CLOSE_TIMEOUT = 5

CREATE_NO_WINDOW = getattr(
    subprocess,
    "CREATE_NO_WINDOW",
    0
)


# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------

EXIT_EVENT = threading.Event()

SERVICE_ACTION_LOCK = threading.Lock()
SCAN_ACTION_LOCK = threading.Lock()
SCAN_PROCESS_LOCK = threading.Lock()
SETTINGS_PROCESS_LOCK = threading.Lock()

SCAN_PROCESS = None
SETTINGS_PROCESS = None


# ---------------------------------------------------------
# PROCESS HELPERS
# ---------------------------------------------------------

def run_hidden(command, timeout=10):

    try:

        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW
        )

    except Exception:

        return None


def kill_process_tree(pid):

    # /T also ends the child processes. This matters for executables
    # built as a single file, where the process we started is only a
    # launcher and the real application is its child.
    run_hidden(
        ["taskkill", "/PID", str(pid), "/T", "/F"]
    )


def is_image_running(image_name):

    result = run_hidden(
        [
            "tasklist",
            "/FI",
            f"IMAGENAME eq {image_name}",
            "/NH"
        ]
    )

    return (
        result is not None
        and image_name.lower() in result.stdout.lower()
    )


# ---------------------------------------------------------
# WINDOWS SERVICE CONTROL
# ---------------------------------------------------------

def open_service(access):

    manager_handle = win32service.OpenSCManager(
        None,
        None,
        win32service.SC_MANAGER_CONNECT
    )

    try:

        service_handle = win32service.OpenService(
            manager_handle,
            SERVICE_NAME,
            access
        )

        return manager_handle, service_handle

    except Exception:

        win32service.CloseServiceHandle(
            manager_handle
        )

        raise


def close_service(manager_handle, service_handle):

    if service_handle is not None:

        try:
            win32service.CloseServiceHandle(
                service_handle
            )
        except Exception:
            pass

    if manager_handle is not None:

        try:
            win32service.CloseServiceHandle(
                manager_handle
            )
        except Exception:
            pass


def get_service_state():

    manager_handle = None
    service_handle = None

    try:

        manager_handle, service_handle = open_service(
            win32service.SERVICE_QUERY_STATUS
        )

        status = win32service.QueryServiceStatus(
            service_handle
        )

        states = {
            win32service.SERVICE_STOPPED: "Stopped",
            win32service.SERVICE_START_PENDING: "Starting",
            win32service.SERVICE_STOP_PENDING: "Stopping",
            win32service.SERVICE_RUNNING: "Running",
            win32service.SERVICE_CONTINUE_PENDING: "Starting",
            win32service.SERVICE_PAUSE_PENDING: "Stopping",
            win32service.SERVICE_PAUSED: "Paused"
        }

        return states.get(status[1], "Unknown")

    except Exception as error:

        if (
            getattr(error, "winerror", None) == 1060
            or (
                getattr(error, "args", None)
                and error.args[0] == 1060
            )
        ):
            return "Not installed"

        return "Unavailable"

    finally:

        close_service(
            manager_handle,
            service_handle
        )


def control_service(action):

    manager_handle = None
    service_handle = None

    try:

        if action == "start":

            access = (
                win32service.SERVICE_QUERY_STATUS
                | win32service.SERVICE_START
            )

            expected_state = win32service.SERVICE_RUNNING

        elif action == "stop":

            access = (
                win32service.SERVICE_QUERY_STATUS
                | win32service.SERVICE_STOP
            )

            expected_state = win32service.SERVICE_STOPPED

        else:

            return False, "Unknown service operation."

        manager_handle, service_handle = open_service(
            access
        )

        status = win32service.QueryServiceStatus(
            service_handle
        )

        current_state = status[1]

        if action == "start":

            if current_state == win32service.SERVICE_RUNNING:
                return True, "The server is already running."

            if current_state != win32service.SERVICE_STOPPED:
                return False, "The service is not stopped."

            win32service.StartService(
                service_handle,
                []
            )

        else:

            if current_state == win32service.SERVICE_STOPPED:
                return True, "The server is already stopped."

            if current_state != win32service.SERVICE_RUNNING:
                return False, "The service is not running."

            win32service.ControlService(
                service_handle,
                win32service.SERVICE_CONTROL_STOP
            )

        for _ in range(SERVICE_TIMEOUT * 2):

            status = win32service.QueryServiceStatus(
                service_handle
            )

            if status[1] == expected_state:

                if action == "start":
                    return True, "Easy DLNA Server started successfully."

                return True, "Easy DLNA Server stopped successfully."

            threading.Event().wait(0.5)

        return False, "The service operation timed out."

    except Exception as error:

        return False, str(error)

    finally:

        close_service(
            manager_handle,
            service_handle
        )


# ---------------------------------------------------------
# NOTIFICATIONS AND REFRESH
# ---------------------------------------------------------

def notify(icon, message):

    try:

        icon.notify(
            message,
            "Easy DLNA Server"
        )

    except Exception:
        pass


def refresh_icon(icon):

    if EXIT_EVENT.is_set():
        return

    try:

        icon.title = (
            "Easy DLNA Server - "
            + get_service_state()
        )

        icon.update_menu()

    except Exception:
        pass


# ---------------------------------------------------------
# SERVICE START / STOP ACTIONS
# ---------------------------------------------------------

def run_service_action(icon, action):

    if EXIT_EVENT.is_set():
        return

    if not SERVICE_ACTION_LOCK.acquire(blocking=False):

        notify(
            icon,
            "Another service operation is already in progress."
        )

        return

    def worker():

        try:

            success, message = control_service(action)

        except Exception as error:

            success = False
            message = str(error)

        finally:

            SERVICE_ACTION_LOCK.release()

        if EXIT_EVENT.is_set():
            return

        notify(icon, message)
        refresh_icon(icon)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


def start_server(icon, item):

    run_service_action(
        icon,
        "start"
    )


def stop_server(icon, item):

    run_service_action(
        icon,
        "stop"
    )


# ---------------------------------------------------------
# AUTOMATIC SERVICE START WHEN TRAY OPENS
# ---------------------------------------------------------

def auto_start_service(icon):

    if EXIT_EVENT.is_set():
        return

    if not SERVICE_ACTION_LOCK.acquire(blocking=False):
        return

    def worker():

        should_notify = False

        try:

            state = get_service_state()

            if state == "Stopped":

                should_notify = True

                success, message = control_service(
                    "start"
                )

            elif state == "Not installed":

                should_notify = True
                success = False
                message = (
                    "Easy DLNA Server service is not installed."
                )

            else:

                return

        except Exception as error:

            should_notify = True
            success = False
            message = str(error)

        finally:

            SERVICE_ACTION_LOCK.release()

        if EXIT_EVENT.is_set():
            return

        if should_notify:

            notify(
                icon,
                message
            )

            refresh_icon(icon)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


# ---------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------

def launch_settings():

    """
    Starts the Settings application and keeps a reference to it so it
    can be closed when the tray exits.

    Returns True if a new instance was started, or False if Settings
    is already open (started from this tray) or the tray is exiting.
    """

    global SETTINGS_PROCESS

    with SETTINGS_PROCESS_LOCK:

        if EXIT_EVENT.is_set():
            return False

        if (
            SETTINGS_PROCESS is not None
            and SETTINGS_PROCESS.poll() is None
        ):
            return False

        if getattr(sys, "frozen", False):

            if not SETTINGS_EXECUTABLE.is_file():

                raise FileNotFoundError(
                    f"Settings application not found: "
                    f"{SETTINGS_EXECUTABLE}"
                )

            command = [
                str(SETTINGS_EXECUTABLE)
            ]

        else:

            if not SETTINGS_SCRIPT.is_file():

                raise FileNotFoundError(
                    f"Settings script not found: "
                    f"{SETTINGS_SCRIPT}"
                )

            python_executable = Path(sys.executable)

            pythonw_executable = (
                python_executable.with_name("pythonw.exe")
            )

            executable = (
                str(pythonw_executable)
                if pythonw_executable.is_file()
                else sys.executable
            )

            command = [
                executable,
                str(SETTINGS_SCRIPT)
            ]

        SETTINGS_PROCESS = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            close_fds=True
        )

        return True


def open_settings(icon, item):

    if EXIT_EVENT.is_set():
        return

    try:

        if not launch_settings():

            notify(
                icon,
                "Settings is already open."
            )

    except Exception as error:

        notify(
            icon,
            f"Could not open settings: {error}"
        )


def settings_is_running(process):

    if process is not None and process.poll() is None:
        return True

    # Settings may also have been opened from the Start menu, in which
    # case this tray has no process object for it.
    if getattr(sys, "frozen", False):
        return is_image_running(SETTINGS_EXECUTABLE.name)

    return False


def stop_settings_process():

    """
    Closes Settings if it is open: first by asking its window to close
    (so it can shut down cleanly), and by force if it does not close
    within SETTINGS_CLOSE_TIMEOUT seconds.
    """

    global SETTINGS_PROCESS

    with SETTINGS_PROCESS_LOCK:

        process = SETTINGS_PROCESS
        SETTINGS_PROCESS = None

    frozen = getattr(sys, "frozen", False)

    if not settings_is_running(process):
        return

    # 1. Ask the windows to close (no /F, so the application can exit
    #    normally).

    if process is not None and process.poll() is None:

        run_hidden(
            ["taskkill", "/PID", str(process.pid), "/T"]
        )

    if frozen:

        run_hidden(
            ["taskkill", "/IM", SETTINGS_EXECUTABLE.name, "/T"]
        )

    # 2. Give it some time to close.

    deadline = time.monotonic() + SETTINGS_CLOSE_TIMEOUT

    while time.monotonic() < deadline:

        if not settings_is_running(process):
            return

        time.sleep(0.5)

    # 3. Force it.

    if process is not None and process.poll() is None:
        kill_process_tree(process.pid)

    if frozen:

        run_hidden(
            [
                "taskkill",
                "/IM",
                SETTINGS_EXECUTABLE.name,
                "/T",
                "/F"
            ]
        )


# ---------------------------------------------------------
# SERVICE LOG
# ---------------------------------------------------------

def open_log(icon, item):

    try:

        if not LOG_FILE.is_file():

            notify(
                icon,
                "The service log does not exist yet."
            )

            return

        os.startfile(str(LOG_FILE))

    except Exception as error:

        notify(
            icon,
            f"Could not open service log: {error}"
        )


# ---------------------------------------------------------
# LIBRARY SCANNER
# ---------------------------------------------------------

def append_scan_log(output):

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        SCAN_LOG_FILE,
        "a",
        encoding="utf-8"
    ) as log:

        log.write(output)

        if not output.endswith("\n"):
            log.write("\n")

        log.write("\n")


def execute_scan():

    if getattr(sys, "frozen", False):

        if not SCAN_EXECUTABLE.is_file():

            raise FileNotFoundError(
                f"Scanner application not found: "
                f"{SCAN_EXECUTABLE}"
            )

        command = [
            str(SCAN_EXECUTABLE)
        ]

    else:

        if not SCAN_SCRIPT.is_file():

            raise FileNotFoundError(
                f"Scanner script not found: "
                f"{SCAN_SCRIPT}"
            )

        command = [
            sys.executable,
            str(SCAN_SCRIPT)
        ]

    global SCAN_PROCESS

    with SCAN_PROCESS_LOCK:

        if EXIT_EVENT.is_set():
            return None, "Scan cancelled."

        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW
        )

        SCAN_PROCESS = process

    try:

        output, _ = process.communicate()

        append_scan_log(output)

        if process.returncode == SCAN_BUSY_EXIT_CODE:

            return None, "another library scan is already running."

        if process.returncode != 0:

            return None, (
                f"Scanner exited with code {process.returncode}."
            )

        match = re.search(
            r"Scan completed:\s*(\d+)\s*videos,\s*"
            r"(\d+)\s*subtitles,\s*(\d+)\s*errors",
            output,
            re.IGNORECASE
        )

        if match is None:

            return None, (
                "The scanner finished, but its results "
                "could not be identified. Check scan.log."
            )

        videos = int(match.group(1))
        subtitles = int(match.group(2))
        errors = int(match.group(3))

        return (
            (videos, subtitles, errors),
            None
        )

    finally:

        with SCAN_PROCESS_LOCK:

            if SCAN_PROCESS is process:
                SCAN_PROCESS = None


def scan_library(icon, item):

    if EXIT_EVENT.is_set():
        return

    if not SCAN_ACTION_LOCK.acquire(blocking=False):

        notify(
            icon,
            "A library scan is already in progress."
        )

        return

    notify(
        icon,
        "Library scan started. This may take some time."
    )

    def worker():

        try:

            result, error = execute_scan()

            if EXIT_EVENT.is_set():
                return

            if error:

                notify(
                    icon,
                    f"Library scan failed: {error}"
                )

            elif result is not None:

                videos, subtitles, errors = result

                notify(
                    icon,
                    f"Scan completed: {videos} videos, "
                    f"{subtitles} subtitles, "
                    f"{errors} errors."
                )

        except Exception as error:

            if not EXIT_EVENT.is_set():

                notify(
                    icon,
                    f"Library scan failed: {error}"
                )

        finally:

            SCAN_ACTION_LOCK.release()

        refresh_icon(icon)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


# ---------------------------------------------------------
# EXIT
# ---------------------------------------------------------

def exit_application(icon, item):

    EXIT_EVENT.set()


def stop_scan_process():

    global SCAN_PROCESS

    with SCAN_PROCESS_LOCK:

        process = SCAN_PROCESS

        if process is None:
            return

        try:

            if process.poll() is None:
                kill_process_tree(process.pid)

        except Exception:
            pass

    try:

        process.wait(timeout=5)

    except subprocess.TimeoutExpired:

        try:

            process.kill()
            process.wait(timeout=5)

        except Exception:
            pass

    except Exception:
        pass


def watch_for_shutdown(icon):

    EXIT_EVENT.wait()

    # Remove the tray icon right away; the child applications may take
    # a few seconds to close.
    try:
        icon.visible = False
    except Exception:
        pass

    stop_scan_process()
    stop_settings_process()

    try:
        icon.stop()
    except Exception:
        pass

    threading.Event().wait(0.5)

    os._exit(0)


# ---------------------------------------------------------
# TRAY ICON
# ---------------------------------------------------------

def create_icon_image():

    image = Image.new(
        "RGBA",
        (64, 64),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (3, 3, 61, 61),
        radius=13,
        fill=(30, 90, 160, 255)
    )

    draw.rounded_rectangle(
        (12, 13, 52, 42),
        radius=4,
        fill=(255, 255, 255, 255)
    )

    draw.rounded_rectangle(
        (16, 17, 48, 38),
        radius=2,
        fill=(35, 55, 75, 255)
    )

    draw.rectangle(
        (29, 42, 35, 48),
        fill=(255, 255, 255, 255)
    )

    draw.rounded_rectangle(
        (22, 48, 42, 52),
        radius=2,
        fill=(255, 255, 255, 255)
    )

    draw.ellipse(
        (43, 43, 57, 57),
        fill=(40, 190, 100, 255)
    )

    return image


def get_status_text(item):

    return (
        f"Service status: {get_service_state()}"
    )


def start_enabled(item):

    return (
        get_service_state() == "Stopped"
        and not SERVICE_ACTION_LOCK.locked()
        and not EXIT_EVENT.is_set()
    )


def stop_enabled(item):

    return (
        get_service_state() == "Running"
        and not SERVICE_ACTION_LOCK.locked()
        and not EXIT_EVENT.is_set()
    )


def scan_enabled(item):

    return (
        not SCAN_ACTION_LOCK.locked()
        and not EXIT_EVENT.is_set()
    )


# ---------------------------------------------------------
# MONITORING
# ---------------------------------------------------------

def monitor_server(icon):

    while not EXIT_EVENT.wait(MONITOR_INTERVAL):

        try:

            icon.title = (
                "Easy DLNA Server - "
                + get_service_state()
            )

        except Exception:
            pass


def setup_icon(icon):

    icon.visible = True

    threading.Thread(
        target=monitor_server,
        args=(icon,),
        daemon=True
    ).start()

    threading.Thread(
        target=watch_for_shutdown,
        args=(icon,),
        daemon=True
    ).start()

    # Start the Windows service automatically when the tray opens.
    auto_start_service(icon)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    if os.name != "nt":

        raise RuntimeError(
            "Easy DLNA Server tray application requires Windows."
        )

    EXIT_EVENT.clear()

    signal.signal(
        signal.SIGINT,
        lambda signum, frame: EXIT_EVENT.set()
    )

    menu = pystray.Menu(

        pystray.MenuItem(
            get_status_text,
            lambda icon, item: None,
            enabled=False
        ),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(
            "Start server",
            start_server,
            enabled=start_enabled
        ),

        pystray.MenuItem(
            "Stop server",
            stop_server,
            enabled=stop_enabled
        ),

        pystray.MenuItem(
            "Scan and update library",
            scan_library,
            enabled=scan_enabled
        ),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(
            "Settings",
            open_settings
        ),

        pystray.MenuItem(
            "Open service log",
            open_log
        ),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(
            "Exit",
            exit_application
        )
    )

    icon = pystray.Icon(
        "EasyDLNAServer",
        create_icon_image(),
        "Easy DLNA Server",
        menu
    )

    try:

        icon.run(
            setup=setup_icon
        )

    finally:

        EXIT_EVENT.set()


if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        EXIT_EVENT.set()

        raise RuntimeError(
            f"Easy DLNA Server tray error: {error}"
        ) from error
