#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Easy DLNA Server command line.

  easydlna run                    run the server in the foreground (the service uses this)
  easydlna setup                  guided install as a service (Linux and macOS)
  easydlna install-service        install the service: --user (default) or --system
  easydlna uninstall-service      remove the service (the data is kept)
  easydlna start|stop|restart|enable|disable
  easydlna status
  easydlna logs [-f]
  easydlna folders list|add PATH|remove PATH
  easydlna scan                   update the media library
  easydlna port [NUMBER]          show or change the HTTP port (default 8200)
  easydlna firewall open|close    ufw rules for UDP 1900 and the HTTP port (local network only)

On Linux, user mode runs as your own user, starts when you log in and keeps its
data in ~/.local/share/easydlna. System mode runs as the dedicated "easydlna"
user, starts at boot and keeps its data in /var/lib/easydlna; commands that
change anything must then be run with sudo.

On macOS the service is a user LaunchAgent that starts when you log in.
"""

import argparse
import asyncio
import errno
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import traceback

from pathlib import Path

import app_version
import platform_paths

from process_env import clean_environment

from platform_paths import SERVICE_USER, SYSTEM_DATA_DIR


UNIT_NAME = "easydlna.service"
SYSTEM_UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME

# Helper units that keep the computer awake for the system service (see power.py).
AWAKE_SERVICE_NAME = "easydlna-awake.service"
AWAKE_PATH_NAME = "easydlna-awake.path"
AWAKE_SERVICE_PATH = Path("/etc/systemd/system") / AWAKE_SERVICE_NAME
AWAKE_PATH_PATH = Path("/etc/systemd/system") / AWAKE_PATH_NAME
AWAKE_FLAG = "/run/easydlna/streaming"

PRIVATE_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
SSDP_PORT = 1900
FIREWALL_COMMENT = "Easy DLNA Server"

# "Address already in use" on Linux, macOS and Windows.
ADDRESS_IN_USE_ERRORS = {errno.EADDRINUSE, 10048}

# Configuration problem (for example a busy port): systemd must not keep
# restarting the service (see RestartPreventExitStatus in the unit).
EXIT_CONFIGURATION_ERROR = 78

CASE_INSENSITIVE_FILESYSTEM = (
    os.name == "nt" or sys.platform == "darwin"
)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def run_process(command, **options):
    """subprocess.run with the original library paths (see process_env.py)."""

    options.setdefault("env", clean_environment())

    return subprocess.run(command, **options)


def run_command(command, dry_run=False, check=False):

    printable = " ".join(shlex.quote(str(part)) for part in command)

    if dry_run:
        print(f"[dry run] {printable}")
        return 0

    result = run_process(command)

    if check and result.returncode != 0:
        raise SystemExit(
            f"Command failed ({result.returncode}): {printable}"
        )

    return result.returncode


def exec_command():
    """The command that starts this program (a script or a frozen binary)."""

    if getattr(sys, "frozen", False):
        return [sys.executable]

    return [sys.executable, str(Path(__file__).resolve())]


def invocation(*arguments, sudo=False):
    """
    The command line the user must type to run this program, for messages.
    It is "easydlna" only if that command exists; otherwise the full path of
    the interpreter and script (for example inside a virtual environment).
    """

    found = shutil.which("easydlna")

    if found:
        # sudo does not use the PATH of the user, so give it the full path.
        base = [str(Path(found).resolve())] if sudo else ["easydlna"]
    else:
        base = exec_command()

    command = (["sudo"] if sudo else []) + base + list(arguments)

    return " ".join(shlex.quote(part) for part in command)


def port_is_free(port):

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", port))
        return True

    except OSError as error:
        return error.errno not in ADDRESS_IN_USE_ERRORS

    finally:
        probe.close()


def path_exists(path):
    """Path.exists() that treats an unreadable location as 'does not exist'."""

    try:
        return path.exists()
    except OSError:
        return False


def user_unit_path():

    base = os.environ.get("XDG_CONFIG_HOME")

    base = Path(base) if base else Path.home() / ".config"

    return base / "systemd" / "user" / UNIT_NAME


def unit_text(system):

    command = " ".join(
        shlex.quote(part) for part in exec_command() + ["run"]
    )

    lines = [
        "[Unit]",
        "Description=Easy DLNA Server",
        "StartLimitIntervalSec=60",
        "StartLimitBurst=5"
    ]

    if system:
        lines += [
            "After=network-online.target",
            "Wants=network-online.target"
        ]

    lines += [
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={command}",
        "Restart=on-failure",
        "RestartSec=5",
        f"RestartPreventExitStatus={EXIT_CONFIGURATION_ERROR}"
    ]

    if system:
        lines += [
            f"User={SERVICE_USER}",
            f"Group={SERVICE_USER}",
            "StateDirectory=easydlna",
            "RuntimeDirectory=easydlna",
            f"Environment=EASYDLNA_DATA_DIR={SYSTEM_DATA_DIR}",
            f"Environment=EASYDLNA_AWAKE_FLAG={AWAKE_FLAG}",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=read-only"
        ]

    lines += [
        "",
        "[Install]",
        "WantedBy=multi-user.target" if system else "WantedBy=default.target",
        ""
    ]

    return "\n".join(lines)


def awake_service_text():
    """Root helper: holds the sleep lock while the flag file exists."""

    inhibit = shutil.which("systemd-inhibit") or "/usr/bin/systemd-inhibit"

    return "\n".join([
        "[Unit]",
        "Description=Keep the computer awake while Easy DLNA Server streams",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={inhibit} --what=sleep:idle --who=\"Easy DLNA Server\" "
        "--why=\"Streaming media\" --mode=block "
        f"/bin/sh -c 'while [ -e {AWAKE_FLAG} ]; do sleep 5; done'",
        ""
    ])


def awake_path_text():
    """Starts the helper when the service creates the flag file."""

    return "\n".join([
        "[Unit]",
        "Description=Watch for Easy DLNA Server streaming",
        "",
        "[Path]",
        f"PathExists={AWAKE_FLAG}",
        f"Unit={AWAKE_SERVICE_NAME}",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        ""
    ])


def selected_mode(args, required=True):
    """'user', 'system' or None, from the flags or from the installed unit."""

    if sys.platform == "darwin":

        import launchd_service

        if getattr(args, "system", False):
            raise SystemExit(
                "System-wide services are not supported on macOS yet. "
                "Use the user service (the default)."
            )

        if launchd_service.is_installed() or getattr(args, "user", False):
            return "user"

        if required:
            raise SystemExit(
                f"The service is not installed. Run: {invocation('setup')}"
            )

        return None

    if getattr(args, "system", False):
        return "system"

    if getattr(args, "user", False):
        return "user"

    if path_exists(user_unit_path()):
        return "user"

    if path_exists(SYSTEM_UNIT_PATH):
        return "system"

    if required:
        raise SystemExit(
            f"The service is not installed. Run: {invocation('setup')}"
        )

    return None


def service_control_command(mode, action):
    """The command that starts, stops or restarts the service on this system."""

    if sys.platform == "darwin":

        import launchd_service

        return launchd_service.control_command(action)

    return systemctl(mode) + [action, UNIT_NAME]


def systemctl(mode):

    command = ["systemctl"]

    if mode == "user":
        command.append("--user")

    return command


def ensure_service_identity():
    """
    System mode: the data belongs to the service user. When run as root, drop
    to that user; other users are told to use sudo.
    """

    if not hasattr(os, "geteuid"):
        return

    import pwd

    try:
        entry = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        raise SystemExit(
            f"The system user '{SERVICE_USER}' does not exist. "
            f"Run: {invocation('install-service', '--system', sudo=True)}"
        )

    if os.geteuid() == entry.pw_uid:
        return

    if os.geteuid() != 0:
        raise SystemExit(
            f"In system mode this command must run as '{SERVICE_USER}'. "
            f"Use: {invocation(*sys.argv[1:], sudo=True)}"
        )

    os.initgroups(SERVICE_USER, entry.pw_gid)
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)

    os.environ["HOME"] = entry.pw_dir


def prepare_data_access(args, writes):

    mode = selected_mode(args, required=False)

    if mode == "system":

        os.environ.setdefault(
            "EASYDLNA_DATA_DIR",
            str(SYSTEM_DATA_DIR)
        )

        if writes:
            ensure_service_identity()

    return mode


# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

def cmd_run(args):

    # Under systemd the output goes to a pipe and would be buffered until the
    # service stops: write every line at once so the log is up to date.
    for stream in (sys.stdout, sys.stderr):

        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass

    from main import main as server_main

    async def runner():

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):

            try:
                loop.add_signal_handler(sig, stop.set)

            except NotImplementedError:
                signal.signal(
                    sig,
                    lambda signum, frame: loop.call_soon_threadsafe(stop.set)
                )

        await server_main(stop)

    try:
        asyncio.run(runner())

    except KeyboardInterrupt:
        pass

    except OSError as error:

        if error.errno not in ADDRESS_IN_USE_ERRORS:
            raise

        print(
            "[ERROR] A network port that Easy DLNA Server needs is already "
            f"in use (HTTP port {current_http_port()}). Another DLNA server "
            "(for example MiniDLNA) or another copy of this program is "
            f"probably running. Find it with: ss -ltnp | grep -E "
            f"':({current_http_port()}|{SSDP_PORT})'. You can also choose "
            f"another port with: {invocation('port')} NUMBER",
            flush=True
        )

        return EXIT_CONFIGURATION_ERROR

    return 0


# ---------------------------------------------------------
# SCAN AND FOLDERS
# ---------------------------------------------------------

def run_scan():

    import scan_library

    try:
        return scan_library.main()

    except Exception:
        print("[SCANNER] Fatal error:", flush=True)
        traceback.print_exc()
        return 1


def cmd_scan(args):

    prepare_data_access(args, writes=True)

    return run_scan()


def config_path():

    return platform_paths.get_data_dir() / "config" / "shared_folders.json"


def load_folders():

    path = config_path()

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot read {path}: {error}")

    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a list of folders.")

    return [item for item in data if isinstance(item, str)]


def save_folders(folders):

    path = config_path()

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_file = path.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(folders, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )

    os.replace(temporary_file, path)


def same_path(first, second):

    first = os.path.normcase(str(first))
    second = os.path.normcase(str(second))

    if CASE_INSENSITIVE_FILESYSTEM:
        return first.casefold() == second.casefold()

    return first == second


def folder_access(path):
    """Returns (is_folder, problem). problem is None unless the folder cannot be read."""

    try:
        return path.is_dir(), None

    except PermissionError:
        return False, "permission denied"

    except OSError as error:
        return False, str(error)


def permission_help(folder):
    """Explain how to give the service user access to a folder."""

    folder = Path(folder)

    lines = [f"Permission denied: {folder}"]

    if platform_paths.current_user_name() != SERVICE_USER:
        return "\n".join(lines)

    blocked = [
        parent for parent in reversed(folder.parents)
        if str(parent) != "/" and not os.access(parent, os.X_OK)
    ]

    lines += [
        f"The service runs as the '{SERVICE_USER}' user, which cannot read it.",
        "Give it access without opening the folder to everybody (needs the 'acl' package):"
    ]

    if not shutil.which("setfacl"):
        lines.append("  sudo apt install acl")

    for parent in blocked:
        lines.append(f"  sudo setfacl -m u:{SERVICE_USER}:x {shlex.quote(str(parent))}")

    lines.append(f"  sudo setfacl -R -m u:{SERVICE_USER}:rX {shlex.quote(str(folder))}")
    lines.append("Or share a folder outside your home, for example /srv/media.")

    return "\n".join(lines)


def cmd_folders(args):

    writes = args.action in ("add", "remove")

    prepare_data_access(args, writes=writes)

    folders = load_folders()

    if args.action == "list":

        if not folders:
            print(f"No shared folders. Add one with: {invocation('folders', 'add')} PATH")

        for folder in folders:

            is_folder, problem = folder_access(Path(folder))

            if is_folder:
                note = ""
            elif problem:
                note = f"  (not accessible: {problem})"
            else:
                note = "  (not found)"

            print(f"{folder}{note}")

        return 0

    folder = Path(args.path).expanduser().resolve()

    if args.action == "add":

        is_folder, problem = folder_access(folder)

        if problem == "permission denied":
            raise SystemExit(permission_help(folder))

        if problem:
            raise SystemExit(f"Cannot use {folder}: {problem}")

        if not is_folder:
            raise SystemExit(f"Not a folder: {folder}")

        if any(same_path(existing, folder) for existing in folders):
            print(f"Already shared: {folder}")
            return 0

        folders.append(str(folder))
        save_folders(folders)

        print(f"Added: {folder}")

    else:

        remaining = [
            existing for existing in folders
            if not same_path(existing, folder)
            and not same_path(existing, args.path)
        ]

        if len(remaining) == len(folders):
            raise SystemExit(f"Not in the shared folders: {args.path}")

        save_folders(remaining)
        folders = remaining

        print(f"Removed: {args.path}")

    if args.no_scan:
        print(f"Run '{invocation('scan')}' to update the library.")
        return 0

    if not folders:
        print("No shared folders left; the library was not updated.")
        return 0

    return run_scan()


# ---------------------------------------------------------
# HTTP PORT
# ---------------------------------------------------------

def require_system_privileges():

    if not hasattr(os, "geteuid"):
        return

    if os.geteuid() == 0:
        return

    if platform_paths.current_user_name() == SERVICE_USER:
        return

    raise SystemExit(
        "In system mode this command needs root. "
        f"Use: {invocation(*sys.argv[1:], sudo=True)}"
    )


def chown_to_service_user(path):

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return

    import pwd

    try:
        entry = pwd.getpwnam(SERVICE_USER)
        os.chown(path, entry.pw_uid, entry.pw_gid)
    except (KeyError, OSError):
        pass


def update_firewall_for_port(old_port, new_port):
    """
    After the HTTP port changes, delete the ufw rules of the old port and
    create them again for the new one. This needs root; without it, the
    commands to run are shown.
    """

    ufw = shutil.which("ufw")

    if not ufw:
        return

    close_hint = invocation('firewall', 'close', '--port', str(old_port), sudo=True)
    open_hint = invocation('firewall', 'open', '--port', str(new_port), sudo=True)

    if not hasattr(os, "geteuid") or os.geteuid() != 0:

        print(
            "\nIf you opened the firewall with ufw, update the rules:\n"
            f"  {close_hint}\n"
            f"  {open_hint}"
        )

        return

    added = run_process(
        [ufw, "show", "added"],
        capture_output=True,
        text=True
    )

    if FIREWALL_COMMENT not in added.stdout:
        return

    print("\nUpdating the ufw rules for the new port...")

    for command in firewall_commands("close", old_port):
        run_command(command)

    for command in firewall_commands("open", new_port):
        run_command(command)


def cmd_port(args):

    mode = selected_mode(args, required=False)

    if mode == "system":
        os.environ.setdefault("EASYDLNA_DATA_DIR", str(SYSTEM_DATA_DIR))

    import app_settings

    current = app_settings.get_http_port()

    if args.number is None and not args.reset:

        print(
            f"HTTP port: {current} ({app_settings.http_port_source()})"
        )
        print(
            f"Discovery (SSDP) always uses UDP {SSDP_PORT}. "
            f"Change the HTTP port with: {invocation('port')} NUMBER"
        )

        return 0

    try:
        new = (
            app_settings.DEFAULT_HTTP_PORT if args.reset
            else app_settings.validate_port(args.number)
        )
    except ValueError as error:
        raise SystemExit(str(error))

    if new == current:
        print(f"The HTTP port is already {current}.")
        return 0

    if mode == "system":
        require_system_privileges()

    if not args.force and not port_is_free(new):
        raise SystemExit(
            f"TCP port {new} is already in use by another program. Choose "
            f"another port, or use --force to set it anyway."
        )

    app_settings.set_http_port(new)

    if mode == "system":
        chown_to_service_user(app_settings.settings_file())

    print(f"The HTTP port is now {new} (it was {current}).")

    if mode and not args.no_restart:
        run_command(service_control_command(mode, "restart"))

    elif mode:
        print(f"Restart the service to apply it: {invocation('restart')}")

    else:
        print("Restart the server (the service or 'run') to apply it.")

    update_firewall_for_port(current, new)

    return 0


# ---------------------------------------------------------
# STATUS, SERVICE CONTROL AND LOGS
# ---------------------------------------------------------

def cmd_status(args):

    mode = prepare_data_access(args, writes=False)

    state = "not installed"

    if mode and sys.platform == "darwin":

        import launchd_service

        result = run_process(
            ["launchctl", "print", launchd_service.target()],
            capture_output=True,
            text=True
        )

        state = launchd_service.state_from_print(
            result.returncode,
            result.stdout
        )

    elif mode:

        result = run_process(
            systemctl(mode) + ["is-active", UNIT_NAME],
            capture_output=True,
            text=True
        )

        state = (result.stdout or result.stderr).strip() or "unknown"

    data_dir = platform_paths.get_data_dir()

    print(f"Service mode:   {mode or 'not installed'}")
    print(f"Service state:  {state}")
    print(f"Data folder:    {data_dir}")
    print(f"HTTP port:      {current_http_port()}")
    print(f"Shared folders: {len(load_folders())}")

    uuid_file = data_dir / "config" / "device_uuid.txt"

    if uuid_file.is_file():
        print(f"Device ID:      {uuid_file.read_text().strip()}")

    return 0


def cmd_service(args):

    mode = selected_mode(args)

    return run_command(service_control_command(mode, args.command))


def cmd_logs(args):

    mode = selected_mode(args)

    if sys.platform == "darwin":

        import launchd_service

        return run_command(launchd_service.logs_command(args.follow))

    command = ["journalctl"]

    if mode == "user":
        command.append("--user")

    command += ["--no-pager", "-u", UNIT_NAME, "-n", "200"]

    if args.follow:
        command.append("-f")

    return run_command(command)


# ---------------------------------------------------------
# INSTALL AND UNINSTALL THE SERVICE
# ---------------------------------------------------------

def require_service_support():

    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        print(
            "Installing the service is only available on Linux (systemd) "
            "and macOS (launchd). 'run', 'scan' and 'folders' work on this "
            "system."
        )
        return False

    return True


def install_macos(args):
    """Install the LaunchAgent that starts the server when the user logs in."""

    import launchd_service

    if args.system:
        raise SystemExit(
            "System-wide services are not supported on macOS yet. "
            "Use the user service (the default)."
        )

    dry_run = args.dry_run

    if not dry_run and os.geteuid() == 0:
        raise SystemExit(
            "Install the service as a normal user, not with sudo."
        )

    start_now = not args.no_start
    http_port = current_http_port()

    if start_now and not dry_run and not port_is_free(http_port):

        print(
            f"\nWarning: TCP port {http_port} is already in use, so the "
            "service will not be started now. Find what uses it with: "
            f"lsof -nP -iTCP:{http_port} -sTCP:LISTEN. Once it is stopped, "
            f"run: {invocation('start')}. You can also choose another port "
            f"with: {invocation('port')} NUMBER"
        )

        start_now = False

    launchd_service.write_plist(exec_command() + ["run"], dry_run)

    if start_now:
        run_command(launchd_service.bootout_command(), dry_run)
        run_command(launchd_service.bootstrap_command(), dry_run, check=True)
    else:
        print("The service will start the next time you log in.")

    if args.firewall:
        print(
            "\nThe macOS firewall asks for permission the first time: "
            "choose 'Allow' so TVs can connect."
        )

    print(f"\nDone. Check it with: {invocation('status')}")

    return 0


def uninstall_macos(args):

    import launchd_service

    dry_run = args.dry_run

    run_command(launchd_service.bootout_command(), dry_run)

    if dry_run:
        print(f"[dry run] remove {launchd_service.plist_path()}")
    elif launchd_service.plist_path().exists():
        launchd_service.plist_path().unlink()
        print(f"Removed {launchd_service.plist_path()}")

    print(f"\nThe data was kept: {platform_paths.get_data_dir()}")

    return 0


def write_unit(path, text, dry_run):

    if dry_run:
        print(f"[dry run] write {path}:")
        print(text)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)

    print(f"Wrote {path}")


def ensure_service_user(dry_run):

    import pwd

    try:
        pwd.getpwnam(SERVICE_USER)
        return
    except KeyError:
        pass

    run_command(
        [
            "useradd", "--system", "--user-group", "--no-create-home",
            "--home-dir", str(SYSTEM_DATA_DIR),
            "--shell", "/usr/sbin/nologin", SERVICE_USER
        ],
        dry_run,
        check=True
    )


def current_http_port():

    import app_settings

    return app_settings.get_http_port()


def firewall_commands(action, http_port):

    ufw = shutil.which("ufw") or "ufw"

    commands = []

    for network in PRIVATE_NETWORKS:

        for protocol, port in (("udp", SSDP_PORT), ("tcp", http_port)):

            rule = [
                "from", network, "to", "any",
                "port", str(port), "proto", protocol
            ]

            if action == "open":
                commands.append(
                    [ufw, "allow"] + rule + ["comment", FIREWALL_COMMENT]
                )
            else:
                commands.append([ufw, "--force", "delete", "allow"] + rule)

    return commands


def cmd_firewall(args):

    if not shutil.which("ufw") and not args.dry_run:

        print(
            "ufw was not found. If you use another firewall, allow UDP 1900 "
            "and TCP 8200 from your local network."
        )

        return 0

    http_port = args.port or current_http_port()

    for command in firewall_commands(args.action, http_port):
        run_command(command, args.dry_run)

    return 0


def cmd_install_service(args):

    if not require_service_support():
        return 2

    if sys.platform == "darwin":
        return install_macos(args)

    mode = "system" if args.system else "user"
    dry_run = args.dry_run

    if not dry_run and hasattr(os, "geteuid"):

        if mode == "system" and os.geteuid() != 0:
            raise SystemExit(
                "System mode needs root. "
                f"Run: {invocation('install-service', '--system', sudo=True)}"
            )

        if mode == "user" and os.geteuid() == 0:
            raise SystemExit(
                "User mode must be installed by a normal user, not root. "
                "Use --system for a service that starts at boot."
            )

    text = unit_text(mode == "system")

    if mode == "system":
        ensure_service_user(dry_run)
        write_unit(SYSTEM_UNIT_PATH, text, dry_run)
        write_unit(AWAKE_SERVICE_PATH, awake_service_text(), dry_run)
        write_unit(AWAKE_PATH_PATH, awake_path_text(), dry_run)
    else:
        write_unit(user_unit_path(), text, dry_run)

    control = systemctl(mode)

    start_now = not args.no_start

    http_port = current_http_port()

    if start_now and not dry_run and not port_is_free(http_port):

        print(
            f"\nWarning: TCP port {http_port} is already in use, so the "
            "service will not be started now. Find what uses it with: "
            f"ss -ltnp | grep -E ':({http_port}|{SSDP_PORT})'. Once it is "
            f"stopped, run: {invocation('start')}. You can also choose "
            f"another port with: {invocation('port')} NUMBER"
        )

        start_now = False

    run_command(control + ["daemon-reload"], dry_run)

    if mode == "system":
        run_command(control + ["enable", "--now", AWAKE_PATH_NAME], dry_run)

    if start_now:
        run_command(control + ["enable", "--now", UNIT_NAME], dry_run)
    else:
        run_command(control + ["enable", UNIT_NAME], dry_run)

    if args.firewall:

        if hasattr(os, "geteuid") and os.geteuid() != 0 and not dry_run:
            print(
                "Opening the firewall needs root. Run: "
                f"{invocation('firewall', 'open', '--port', str(current_http_port()), sudo=True)}"
            )
        else:
            cmd_firewall(
                argparse.Namespace(action="open", dry_run=dry_run, port=None)
            )

    if mode == "system":
        print(
            f"\nThe service runs as the '{SERVICE_USER}' user, so it must be "
            "able to read your media folders. Folders outside your home, "
            "such as /srv/media, work directly. For a folder in your home, "
            "when you add it the command shows how to give access."
        )

    if mode == "user":
        print(
            "\nA user service runs while you are logged in. To keep it "
            "running after you log out, run: loginctl enable-linger "
            f"{os.environ.get('USER', '$USER')}"
        )

    print(f"\nDone. Check it with: {invocation('status')}")

    return 0


def cmd_uninstall_service(args):

    if not require_service_support():
        return 2

    if sys.platform == "darwin":
        selected_mode(args)
        return uninstall_macos(args)

    mode = selected_mode(args)
    dry_run = args.dry_run

    path = SYSTEM_UNIT_PATH if mode == "system" else user_unit_path()
    control = systemctl(mode)

    run_command(control + ["disable", "--now", UNIT_NAME], dry_run)

    paths = [path]

    if mode == "system":

        run_command(control + ["disable", "--now", AWAKE_PATH_NAME], dry_run)
        run_command(control + ["stop", AWAKE_SERVICE_NAME], dry_run)

        paths += [AWAKE_PATH_PATH, AWAKE_SERVICE_PATH]

    for unit_path in paths:

        if dry_run:
            print(f"[dry run] remove {unit_path}")
        elif path_exists(unit_path):
            unit_path.unlink()
            print(f"Removed {unit_path}")

    run_command(control + ["daemon-reload"], dry_run)

    if mode == "system" and shutil.which("ufw"):
        cmd_firewall(
            argparse.Namespace(action="close", dry_run=dry_run, port=None)
        )

    data_dir = (
        SYSTEM_DATA_DIR if mode == "system"
        else platform_paths.get_data_dir()
    )

    print(f"\nThe data was kept: {data_dir}")

    return 0


def ask(question, default):

    if not sys.stdin.isatty():
        return default

    answer = input(f"{question} ").strip().lower()

    return answer or default


def cmd_setup(args):

    if not require_service_support():
        return 2

    print("Easy DLNA Server setup\n")

    if sys.platform == "darwin":

        print("Installing the service for your user (it starts when you log in).")

        return cmd_install_service(
            argparse.Namespace(
                system=False,
                user=True,
                firewall=bool(args.firewall),
                no_start=False,
                dry_run=args.dry_run
            )
        )

    if args.system:
        mode = "system"

    elif args.user:
        mode = "user"

    elif args.yes:
        mode = "user"

    else:
        print("Install as:")
        print("  1) User service   - runs as you, starts when you log in (simplest on a desktop)")
        print("  2) System service - runs as the 'easydlna' user, starts at boot (needs sudo)")

        mode = "system" if ask("Choose 1 or 2 [1]:", "1") == "2" else "user"

    if args.firewall is None:

        if args.yes or not shutil.which("ufw"):
            args.firewall = False

        else:
            args.firewall = ask(
                f"Open UDP 1900 and TCP {current_http_port()} in ufw for the local network? [y/N]:",
                "n"
            ).startswith("y")

    if (
        mode == "system"
        and not args.dry_run
        and hasattr(os, "geteuid")
        and os.geteuid() != 0
    ):

        flags = ["--system"] + (["--firewall"] if args.firewall else [])

        if not shutil.which("sudo"):
            raise SystemExit(
                "System mode needs root. Run: "
                + invocation("install-service", *flags)
            )

        print("\nSystem mode needs root: running the installation with sudo...")

        return run_process(
            ["sudo"] + exec_command() + ["install-service"] + flags
        ).returncode

    return cmd_install_service(
        argparse.Namespace(
            system=(mode == "system"),
            user=(mode == "user"),
            firewall=args.firewall,
            no_start=False,
            dry_run=args.dry_run
        )
    )


# ---------------------------------------------------------
# COMMAND LINE
# ---------------------------------------------------------

def build_parser():

    modes = argparse.ArgumentParser(add_help=False)

    group = modes.add_mutually_exclusive_group()
    group.add_argument("--user", action="store_true", help="the user service")
    group.add_argument("--system", action="store_true", help="the system service")

    parser = argparse.ArgumentParser(
        prog="easydlna",
        description="Easy DLNA Server command line."
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"easydlna {app_version.__version__}"
    )

    commands = parser.add_subparsers(dest="command", metavar="command")

    commands.add_parser(
        "run",
        help="run the server in the foreground"
    ).set_defaults(func=cmd_run)

    commands.add_parser(
        "scan", parents=[modes],
        help="update the media library"
    ).set_defaults(func=cmd_scan)

    folders = commands.add_parser(
        "folders", parents=[modes],
        help="list, add or remove shared folders"
    )
    folders.add_argument("action", choices=["list", "add", "remove"])
    folders.add_argument("path", nargs="?")
    folders.add_argument(
        "--no-scan", action="store_true",
        help="do not update the library after changing the folders"
    )
    folders.set_defaults(func=cmd_folders)

    commands.add_parser(
        "status", parents=[modes],
        help="show the service state and the configuration"
    ).set_defaults(func=cmd_status)

    for name in ("start", "stop", "restart", "enable", "disable"):
        commands.add_parser(
            name, parents=[modes],
            help=f"{name} the service"
        ).set_defaults(func=cmd_service)

    logs = commands.add_parser(
        "logs", parents=[modes],
        help="show the service log"
    )
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=cmd_logs)

    install = commands.add_parser(
        "install-service", parents=[modes],
        help="install the service (default: user)"
    )
    install.add_argument("--firewall", action="store_true", help="also open the ufw ports")
    install.add_argument("--no-start", action="store_true", help="enable it but do not start it now")
    install.add_argument("--dry-run", action="store_true", help="show what would be done")
    install.set_defaults(func=cmd_install_service)

    uninstall = commands.add_parser(
        "uninstall-service", parents=[modes],
        help="remove the service (the data is kept)"
    )
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.set_defaults(func=cmd_uninstall_service)

    setup = commands.add_parser(
        "setup", parents=[modes],
        help="guided installation of the service"
    )
    setup.add_argument("--yes", action="store_true", help="accept the defaults")
    setup.add_argument("--firewall", dest="firewall", action="store_true", default=None)
    setup.add_argument("--no-firewall", dest="firewall", action="store_false")
    setup.add_argument("--dry-run", action="store_true")
    setup.set_defaults(func=cmd_setup)

    firewall = commands.add_parser(
        "firewall",
        help="open or close the ufw ports for the local network"
    )
    firewall.add_argument("action", choices=["open", "close"])
    firewall.add_argument(
        "--port", type=int,
        help="HTTP port (default: the configured one)"
    )
    firewall.add_argument("--dry-run", action="store_true")
    firewall.set_defaults(func=cmd_firewall)

    port = commands.add_parser(
        "port", parents=[modes],
        help="show or change the HTTP port (default 8200)"
    )
    port.add_argument("number", nargs="?", help="the new port (1024-65535)")
    port.add_argument("--reset", action="store_true", help="back to the default port")
    port.add_argument("--no-restart", action="store_true", help="do not restart the service")
    port.add_argument("--force", action="store_true", help="set it even if the port is busy now")
    port.set_defaults(func=cmd_port)

    return parser


def main(argv=None):

    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    if args.command == "folders" and args.action in ("add", "remove") and not args.path:
        parser.error(f"'{args.action}' needs a PATH")

    try:
        return args.func(args) or 0

    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
