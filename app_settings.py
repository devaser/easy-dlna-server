# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
User settings of Easy DLNA Server, stored in <data folder>/config/settings.json.

Today it holds one value, the TCP port of the HTTP media server (default 8200).
The SSDP discovery port (UDP 1900) is fixed by the UPnP standard.

The port is read when the server starts, so the service must be restarted to
apply a new one. The EASYDLNA_HTTP_PORT environment variable overrides the file.
"""

import json
import os

from platform_paths import get_data_dir


DEFAULT_HTTP_PORT = 8200

# Ports below 1024 need special privileges on Linux and macOS.
MIN_PORT = 1024
MAX_PORT = 65535

ENVIRONMENT_VARIABLE = "EASYDLNA_HTTP_PORT"


def settings_file():

    return get_data_dir() / "config" / "settings.json"


def validate_port(value):
    """Return the port as an int, or raise ValueError with a clear message."""

    try:
        port = int(str(value).strip())

    except ValueError:
        raise ValueError("The port must be a number.")

    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(
            f"The port must be between {MIN_PORT} and {MAX_PORT}."
        )

    return port


def load_settings():

    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))

    except (OSError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


def save_settings(settings):

    path = settings_file()

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_file = path.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(settings, indent=4) + "\n",
        encoding="utf-8"
    )

    os.replace(temporary_file, path)


def http_port_source():
    """Where the current port comes from: environment, file or default."""

    try:
        validate_port(os.environ[ENVIRONMENT_VARIABLE])
        return f"environment variable {ENVIRONMENT_VARIABLE}"
    except (KeyError, ValueError):
        pass

    try:
        validate_port(load_settings()["http_port"])
        return "settings file"
    except (KeyError, ValueError):
        pass

    return "default"


def get_http_port():

    try:
        return validate_port(os.environ[ENVIRONMENT_VARIABLE])
    except (KeyError, ValueError):
        pass

    try:
        return validate_port(load_settings()["http_port"])
    except (KeyError, ValueError):
        pass

    return DEFAULT_HTTP_PORT


def set_http_port(port):

    port = validate_port(port)

    settings = load_settings()
    settings["http_port"] = port

    save_settings(settings)

    return port
