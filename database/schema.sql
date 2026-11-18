CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_size INTEGER NOT NULL,
    duration INTEGER DEFAULT 0,
    mime_type TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subtitles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL,
    subtitle_path TEXT NOT NULL,
    language TEXT DEFAULT 'und',
    format TEXT DEFAULT 'srt',

    FOREIGN KEY (media_id)
        REFERENCES media(id)
        ON DELETE CASCADE,

    UNIQUE(media_id, subtitle_path)
);

CREATE TABLE IF NOT EXISTS playback_bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL UNIQUE,
    position_seconds INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (media_id)
        REFERENCES media(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_media_title
ON media(title);

CREATE INDEX IF NOT EXISTS idx_subtitles_media
ON subtitles(media_id);

CREATE INDEX IF NOT EXISTS idx_bookmarks_media
ON playback_bookmarks(media_id);