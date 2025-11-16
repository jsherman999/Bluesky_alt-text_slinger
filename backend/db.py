import os
import sqlite3
from typing import List, Dict, Any

DB_PATH = os.getenv(
    "ALTTS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "alttext_slinger.db"),
)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            handle TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT NOT NULL,
            uri TEXT NOT NULL,
            cid TEXT,
            text TEXT,
            created_at TEXT,
            has_images INTEGER NOT NULL DEFAULT 1,
            UNIQUE(handle, uri)
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT NOT NULL,
            post_uri TEXT NOT NULL,
            image_index INTEGER NOT NULL,
            thumb_url TEXT,
            fullsize_url TEXT,
            current_alt TEXT,
            generated_alt TEXT,
            last_applied_alt TEXT,
            last_status TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(handle, post_uri, image_index)
        );
        """
    )

    conn.commit()
    conn.close()


def save_scan(handle: str, posts: List[Dict[str, Any]]) -> None:
    """
    Persist scan results to SQLite.

    posts: list of dicts shaped like PostInfo.model_dump()
    """
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("INSERT OR IGNORE INTO users(handle) VALUES (?)", (handle,))

    for post in posts:
        uri = post["uri"]
        cid = post.get("cid")
        text = post.get("text")
        created_at = post.get("created_at")

        cur.execute(
            """
            INSERT INTO posts (handle, uri, cid, text, created_at, has_images)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(handle, uri) DO UPDATE SET
                cid = excluded.cid,
                text = excluded.text,
                created_at = excluded.created_at,
                has_images = excluded.has_images;
            """,
            (handle, uri, cid, text, created_at),
        )

        for img in post.get("images", []):
            idx = img["index"]
            thumb_url = img.get("thumb_url")
            fullsize_url = img.get("fullsize_url")
            current_alt = img.get("alt")
            generated_alt = img.get("generated_alt")

            cur.execute(
                """
                INSERT INTO images (
                    handle, post_uri, image_index,
                    thumb_url, fullsize_url,
                    current_alt, generated_alt, last_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'scanned')
                ON CONFLICT(handle, post_uri, image_index) DO UPDATE SET
                    thumb_url = excluded.thumb_url,
                    fullsize_url = excluded.fullsize_url,
                    current_alt = excluded.current_alt,
                    generated_alt = excluded.generated_alt,
                    last_status = 'scanned',
                    updated_at = datetime('now');
                """,
                (
                    handle,
                    uri,
                    idx,
                    thumb_url,
                    fullsize_url,
                    current_alt,
                    generated_alt,
                ),
            )

    conn.commit()
    conn.close()


def record_image_update(
    handle: str,
    uri: str,
    image_index: int,
    new_alt: str,
    status: str,
) -> None:
    """
    Record that an image alt was applied (or failed).
    """
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE images
        SET
            current_alt = ?,
            last_applied_alt = ?,
            last_status = ?,
            updated_at = datetime('now')
        WHERE handle = ? AND post_uri = ? AND image_index = ?;
        """,
        (new_alt, new_alt, status, handle, uri, image_index),
    )

    # If no row existed (unlikely but possible), insert one
    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO images (
                handle, post_uri, image_index,
                current_alt, last_applied_alt, last_status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle, post_uri, image_index) DO UPDATE SET
                current_alt = excluded.current_alt,
                last_applied_alt = excluded.last_applied_alt,
                last_status = excluded.last_status,
                updated_at = datetime('now');
            """,
            (handle, uri, image_index, new_alt, new_alt, status),
        )

    conn.commit()
    conn.close()