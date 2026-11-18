# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import os
import mimetypes
import sqlite3
from pathlib import Path

from database.db import connect, get_shared_folders


MIME_TYPES = {
    ".mkv": "video/x-mkv",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".mpe": "video/mpeg",
    ".m2v": "video/mpeg",
    ".vob": "video/mpeg",
    ".ts": "video/mp2t",
    ".mts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".wmv": "video/x-ms-wmv",
    ".webm": "video/webm",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
}

VIDEO_EXTENSIONS = set(MIME_TYPES)


def get_subtitles(video_name, filenames, folder):
    video_stem = Path(video_name).stem.casefold()
    result = []

    for filename in filenames:
        subtitle = Path(filename)

        if subtitle.suffix.casefold() != ".srt":
            continue

        subtitle_stem = subtitle.stem.casefold()

        if (
            subtitle_stem == video_stem
            or subtitle_stem.startswith(video_stem + ".")
        ):
            result.append(str(folder / filename))

    return result


def get_mime_type(file_path):
    extension = Path(file_path).suffix.lower()

    if extension in MIME_TYPES:
        return MIME_TYPES[extension]

    return (
        mimetypes.guess_type(file_path)[0]
        or "application/octet-stream"
    )


def normalize_path(file_path):
    return os.path.normcase(
        os.path.abspath(str(file_path))
    ).casefold()


def scan_library():
    shared_folders = get_shared_folders()

    if not shared_folders:
        raise FileNotFoundError(
            "No valid shared folders configured. "
            "Library cleanup cancelled."
        )

    conn = connect()

    videos_found = 0
    subtitles_found = 0
    errors = 0
    pending = 0
    removed = 0

    scanned_paths = set()

    print("[SCANNER] Starting library scan...")

    try:
        for media_root in shared_folders:
            print(f"[SCANNER] Exploring: {media_root}")

            def on_walk_error(error):
                nonlocal errors
                errors += 1
                print(f"[SCANNER] Cannot access: {error}")

            for root, directories, filenames in os.walk(
                media_root,
                onerror=on_walk_error,
                followlinks=False
            ):
                folder = Path(root)

                for filename in filenames:
                    file_path = folder / filename

                    if (
                        file_path.suffix.casefold()
                        not in VIDEO_EXTENSIONS
                    ):
                        continue

                    try:
                        stat = file_path.stat()

                        if not file_path.is_file():
                            continue

                        mime_type = get_mime_type(file_path)

                        conn.execute(
                            """
                            INSERT INTO media (
                                title,
                                file_path,
                                file_size,
                                mime_type
                            )
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(file_path) DO UPDATE SET
                                title = excluded.title,
                                file_size = excluded.file_size,
                                mime_type = excluded.mime_type
                            """,
                            (
                                file_path.stem,
                                str(file_path),
                                stat.st_size,
                                mime_type
                            )
                        )

                        scanned_paths.add(
                            normalize_path(file_path)
                        )

                        media = conn.execute(
                            """
                            SELECT id
                            FROM media
                            WHERE file_path = ?
                            """,
                            (str(file_path),)
                        ).fetchone()

                        media_id = media[0]

                        subtitle_paths = get_subtitles(
                            filename,
                            filenames,
                            folder
                        )

                        for subtitle_path in subtitle_paths:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO subtitles (
                                    media_id,
                                    subtitle_path,
                                    format
                                )
                                VALUES (?, ?, 'srt')
                                """,
                                (media_id, subtitle_path)
                            )

                        existing = conn.execute(
                            """
                            SELECT id, subtitle_path
                            FROM subtitles
                            WHERE media_id = ?
                            """,
                            (media_id,)
                        ).fetchall()

                        current_paths = set(subtitle_paths)

                        for subtitle_id, old_path in existing:
                            if old_path not in current_paths:
                                conn.execute(
                                    """
                                    DELETE FROM subtitles
                                    WHERE id = ?
                                    """,
                                    (subtitle_id,)
                                )

                        videos_found += 1
                        subtitles_found += len(subtitle_paths)
                        pending += 1

                        if pending >= 200:
                            conn.commit()
                            pending = 0

                        if videos_found % 50 == 0:
                            print(
                                "[SCANNER] Videos processed: "
                                f"{videos_found}"
                            )

                    except (OSError, sqlite3.Error) as error:
                        errors += 1
                        print(
                            f"[SCANNER] Error with {file_path}: "
                            f"{error}"
                        )

        conn.commit()

        # Remove obsolete database entries only after
        # a completely successful scan.

        if errors == 0:
            print("[SCANNER] Checking obsolete library entries...")

            with conn:
                existing_media = conn.execute(
                    """
                    SELECT id, file_path
                    FROM media
                    """
                ).fetchall()

                for media_id, old_path in existing_media:
                    if normalize_path(old_path) not in scanned_paths:
                        conn.execute(
                            """
                            DELETE FROM media
                            WHERE id = ?
                            """,
                            (media_id,)
                        )

                        removed += 1

            print(
                f"[SCANNER] Removed obsolete entries: {removed}"
            )

        else:
            print(
                "[SCANNER] Cleanup skipped because "
                f"{errors} scanning errors occurred."
            )

    finally:
        conn.close()

    print(
        "[SCANNER] Scan completed: "
        f"{videos_found} videos, "
        f"{subtitles_found} subtitles, "
        f"{errors} errors."
    )

    return videos_found, subtitles_found, errors
