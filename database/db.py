# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import json
import shutil
import sqlite3

from pathlib import Path

from platform_paths import get_data_dir


# Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Application data directory (see platform_paths.py).
# EASYDLNA_DATA_DIR can override the default location.
DATA_DIR = get_data_dir()


# Database
DB_PATH = DATA_DIR / "database" / "library.db"

# Shared folders configuration
CONFIG_FILE = DATA_DIR / "config" / "shared_folders.json"

# Database schema remains part of the application files.
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"

# Compatibility alias for older application modules.
MEDIA_DIR = DATA_DIR


def migrate_legacy_file(source, destination):
    """
    Copy an existing application data file when the
    destination does not already exist.

    Original files are never deleted or overwritten.
    """

    source = Path(source)
    destination = Path(destination)

    if not source.is_file():
        return

    if source.resolve() == destination.resolve():
        return

    if destination.exists():
        return

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    try:
        shutil.copy2(
            source,
            destination
        )

        print(
            f"[DATA] Migrated file: {destination}"
        )

    except OSError as error:

        print(
            f"[DATA] Migration failed for {source}: {error}"
        )

        raise


def migrate_legacy_data():
    """
    Migrate the existing database and shared folders
    configuration to the application data directory.

    The original files remain untouched.
    """

    legacy_database = (
        BASE_DIR / "database" / "library.db"
    )

    legacy_config = (
        BASE_DIR / "config" / "shared_folders.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Migrate the SQLite database.
    migrate_legacy_file(
        legacy_database,
        DB_PATH
    )

    # Migrate SQLite WAL files if they exist.
    # The service must be stopped during migration.
    for suffix in ("-wal", "-shm"):

        source = Path(
            str(legacy_database) + suffix
        )

        destination = Path(
            str(DB_PATH) + suffix
        )

        migrate_legacy_file(
            source,
            destination
        )

    # Migrate shared folders configuration.
    migrate_legacy_file(
        legacy_config,
        CONFIG_FILE
    )


# Run migration before accessing application data.
migrate_legacy_data()


def get_shared_folders():
    """
    Load shared folders from the configuration file.
    """

    if not CONFIG_FILE.exists():
        return []

    try:

        with open(
            CONFIG_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError(
                "Shared folders configuration must be a list."
            )

        folders = []
        seen = set()

        for entry in data:

            if not isinstance(entry, str):
                continue

            folder = Path(entry).expanduser()

            # A folder that cannot be read (for example, no permission) must
            # not remove the other shared folders.
            try:
                is_folder = folder.is_dir()

            except OSError as error:

                print(
                    f"[SHARED] Folder not accessible: {folder} ({error})"
                )

                continue

            if not is_folder:

                print(
                    f"[SHARED] Folder not found: {folder}"
                )

                continue

            resolved_folder = folder.resolve()
            normalized = str(resolved_folder).casefold()

            if normalized in seen:
                continue

            seen.add(normalized)
            folders.append(resolved_folder)

        return folders

    except (OSError, json.JSONDecodeError, ValueError) as error:

        print(
            f"[SHARED] Configuration error: {error}"
        )

        return []


def connect():

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30
    )

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def initialize():

    conn = connect()

    try:

        with open(
            SCHEMA_PATH,
            "r",
            encoding="utf-8"
        ) as file:

            schema = file.read()

        conn.executescript(schema)
        conn.commit()

        print("[SQLite] Database initialized.")

    finally:
        conn.close()


def add_media(title, path, size, mime_type):

    conn = connect()

    try:

        with conn:

            conn.execute(
                """
                INSERT INTO media
                (title, file_path, file_size, mime_type)
                VALUES (?, ?, ?, ?)

                ON CONFLICT(file_path) DO UPDATE SET
                    title = excluded.title,
                    file_size = excluded.file_size,
                    mime_type = excluded.mime_type
                """,
                (
                    title,
                    str(path),
                    size,
                    mime_type
                )
            )

    finally:
        conn.close()


def get_media(media_id):

    conn = connect()

    try:

        return conn.execute(
            """
            SELECT id, title, file_path,
                   file_size, duration, mime_type
            FROM media
            WHERE id = ?
            """,
            (media_id,)
        ).fetchone()

    finally:
        conn.close()


def save_playback_bookmark(media_id, position_seconds):
    """
    Save or update a media playback position.
    """

    try:

        media_id = int(media_id)
        position_seconds = int(position_seconds)

    except (TypeError, ValueError):
        raise ValueError("Invalid playback bookmark values")

    if media_id <= 0 or position_seconds < 0:
        raise ValueError("Invalid playback bookmark values")

    conn = connect()

    try:

        with conn:

            conn.execute(
                """
                INSERT INTO playback_bookmarks
                (media_id, position_seconds, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)

                ON CONFLICT(media_id) DO UPDATE SET
                    position_seconds = excluded.position_seconds,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    media_id,
                    position_seconds
                )
            )

    finally:
        conn.close()


def get_playback_bookmark(media_id):
    """
    Return the saved playback position in seconds.
    Return zero when no bookmark exists.
    """

    conn = connect()

    try:

        row = conn.execute(
            """
            SELECT position_seconds
            FROM playback_bookmarks
            WHERE media_id = ?
            """,
            (media_id,)
        ).fetchone()

        if row is None:
            return 0

        return row[0]

    finally:
        conn.close()


def delete_playback_bookmark(media_id):
    """
    Delete the saved playback position for a media item.
    """

    conn = connect()

    try:

        with conn:

            conn.execute(
                """
                DELETE FROM playback_bookmarks
                WHERE media_id = ?
                """,
                (media_id,)
            )

    finally:
        conn.close()
