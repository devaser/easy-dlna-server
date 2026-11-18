# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Unique identifier of this DLNA server (the UPnP UDN / SSDP USN).

Every installation needs its own identifier: if two servers on the same
network shared one, clients would confuse them. It is generated once, stored in
the data folder and reused, so it survives restarts, upgrades and reinstalls
(the data folder is never removed). TVs keep recognizing the same server.

If the identifier cannot be stored, a stable one is derived from the computer
name instead, so it never changes between runs either.
"""

import os
import socket
import uuid

from pathlib import Path

from platform_paths import get_data_dir


DATA_DIR = get_data_dir()

UUID_FILE = DATA_DIR / "config" / "device_uuid.txt"


def read_stored_uuid():

    try:
        text = UUID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if text.lower().startswith("uuid:"):
        text = text[5:]

    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def store_uuid(value):

    UUID_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_file = UUID_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        value + "\n",
        encoding="utf-8"
    )

    os.replace(
        temporary_file,
        UUID_FILE
    )


def load_or_create_device_uuid():

    stored = read_stored_uuid()

    if stored:
        return f"uuid:{stored}"

    value = str(uuid.uuid4())

    try:
        store_uuid(value)

    except OSError:
        # Could not save it: derive a stable identifier from this computer.
        value = str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"easydlna.{socket.gethostname().lower()}"
            )
        )

    return f"uuid:{value}"


DEVICE_UUID = load_or_create_device_uuid()
