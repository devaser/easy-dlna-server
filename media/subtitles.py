# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

from pathlib import Path


def find_subtitles(video_path):
    """
    Find SRT subtitle files associated with a movie.
    Returns a list of matching files.
    """

    video_path = Path(video_path)
    folder = video_path.parent
    stem = video_path.stem

    subtitles = []

    if not folder.exists():
        return subtitles

    for subtitle in folder.glob("*.srt"):

        name = subtitle.stem

        # Direct match or language variant.
        if name == stem or name.startswith(stem + "."):
            subtitles.append(subtitle)

    return sorted(subtitles, key=lambda p: p.name.lower())


def find_subtitle(video_path):
    """
    Return the first matching subtitle file.
    """

    subtitles = find_subtitles(video_path)

    return subtitles[0] if subtitles else None
