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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS apply_jobs (
            job_id TEXT PRIMARY KEY,
            handle TEXT NOT NULL,
            status TEXT NOT NULL,
            total_items INTEGER NOT NULL DEFAULT 0,
            processed_items INTEGER NOT NULL DEFAULT 0,
            success_items INTEGER NOT NULL DEFAULT 0,
            failed_items INTEGER NOT NULL DEFAULT 0,
            rate_limit_reset_at INTEGER,
            pause_reason TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS apply_job_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            uri TEXT NOT NULL,
            image_index INTEGER NOT NULL,
            new_alt TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(job_id, uri, image_index)
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
                    generated_alt = COALESCE(excluded.generated_alt, generated_alt),
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


def get_generated_alt_map(handle: str) -> Dict[tuple[str, int], str]:
    """
    Return cached generated alt text keyed by (post_uri, image_index).
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT post_uri, image_index, generated_alt
        FROM images
        WHERE handle = ?
          AND generated_alt IS NOT NULL
          AND TRIM(generated_alt) != '';
        """,
        (handle,),
    )
    out: Dict[tuple[str, int], str] = {}
    for row in cur.fetchall():
        out[(row["post_uri"], row["image_index"])] = row["generated_alt"]
    conn.close()
    return out


def get_image_status_map(handle: str) -> Dict[tuple[str, int], str]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT post_uri, image_index, last_status
        FROM images
        WHERE handle = ?;
        """,
        (handle,),
    )
    out: Dict[tuple[str, int], str] = {}
    for row in cur.fetchall():
        if row["last_status"]:
            out[(row["post_uri"], row["image_index"])] = row["last_status"]
    conn.close()
    return out


def record_generated_alt(
    handle: str,
    uri: str,
    image_index: int,
    generated_alt: str,
    status: str = "generated",
) -> None:
    """
    Persist generated alt text for later reuse across scans.
    """
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE images
        SET
            generated_alt = ?,
            last_status = ?,
            updated_at = datetime('now')
        WHERE handle = ? AND post_uri = ? AND image_index = ?;
        """,
        (generated_alt, status, handle, uri, image_index),
    )

    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO images (
                handle, post_uri, image_index, generated_alt, last_status
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle, post_uri, image_index) DO UPDATE SET
                generated_alt = excluded.generated_alt,
                last_status = excluded.last_status,
                updated_at = datetime('now');
            """,
            (handle, uri, image_index, generated_alt, status),
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
            current_alt = CASE WHEN ? = 'applied' THEN ? ELSE current_alt END,
            last_applied_alt = ?,
            last_status = ?,
            updated_at = datetime('now')
        WHERE handle = ? AND post_uri = ? AND image_index = ?;
        """,
        (status, new_alt, new_alt, status, handle, uri, image_index),
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
                current_alt = CASE
                    WHEN excluded.last_status = 'applied' THEN excluded.current_alt
                    ELSE images.current_alt
                END,
                last_applied_alt = excluded.last_applied_alt,
                last_status = excluded.last_status,
                updated_at = datetime('now');
            """,
            (handle, uri, image_index, new_alt, new_alt, status),
        )

    conn.commit()
    conn.close()


def create_apply_job(job_id: str, handle: str, total_items: int) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO apply_jobs (
            job_id, handle, status, total_items, processed_items, success_items, failed_items
        )
        VALUES (?, ?, 'running', ?, 0, 0, 0)
        ON CONFLICT(job_id) DO UPDATE SET
            handle = excluded.handle,
            status = excluded.status,
            total_items = excluded.total_items,
            updated_at = datetime('now');
        """,
        (job_id, handle, total_items),
    )
    conn.commit()
    conn.close()


def insert_apply_job_items(job_id: str, updates: List[Dict[str, Any]]) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    for upd in updates:
        cur.execute(
            """
            INSERT INTO apply_job_items(job_id, uri, image_index, new_alt, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(job_id, uri, image_index) DO UPDATE SET
                new_alt = excluded.new_alt,
                status = 'pending',
                error = NULL,
                updated_at = datetime('now');
            """,
            (job_id, upd["uri"], upd["image_index"], upd["new_alt"]),
        )
    conn.commit()
    conn.close()


def claim_next_pending_uri_group(job_id: str) -> List[Dict[str, Any]]:
    """
    Claim the next pending URI group atomically and mark them as running.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT uri
        FROM apply_job_items
        WHERE job_id = ? AND status = 'pending'
        ORDER BY id ASC
        LIMIT 1;
        """,
        (job_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return []
    uri = row["uri"]
    cur.execute(
        """
        UPDATE apply_job_items
        SET status = 'running', updated_at = datetime('now')
        WHERE job_id = ? AND uri = ? AND status = 'pending';
        """,
        (job_id, uri),
    )
    cur.execute(
        """
        SELECT uri, image_index, new_alt, attempts
        FROM apply_job_items
        WHERE job_id = ? AND uri = ? AND status = 'running'
        ORDER BY image_index ASC;
        """,
        (job_id, uri),
    )
    items = [dict(r) for r in cur.fetchall()]
    conn.commit()
    conn.close()
    return items


def claim_next_propagating_uri_group(job_id: str) -> List[Dict[str, Any]]:
    """
    Fetch one URI group currently in propagating state.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT uri
        FROM apply_job_items
        WHERE job_id = ? AND status = 'propagating'
        ORDER BY attempts ASC, id ASC
        LIMIT 1;
        """,
        (job_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return []
    uri = row["uri"]
    cur.execute(
        """
        SELECT uri, image_index, new_alt, attempts, error
        FROM apply_job_items
        WHERE job_id = ? AND uri = ? AND status = 'propagating'
        ORDER BY image_index ASC;
        """,
        (job_id, uri),
    )
    items = [dict(r) for r in cur.fetchall()]
    conn.close()
    return items


def mark_apply_items(
    job_id: str,
    uri: str,
    item_results: List[Dict[str, Any]],
) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    success = 0
    failed = 0
    for res in item_results:
        status = res["status"]
        error = res.get("error")
        idx = res["image_index"]
        attempts_inc = 1 if status in {"failed", "pending", "propagating"} else 0
        cur.execute(
            """
            UPDATE apply_job_items
            SET
                status = ?,
                error = ?,
                attempts = attempts + ?,
                updated_at = datetime('now')
            WHERE job_id = ? AND uri = ? AND image_index = ?;
            """,
            (status, error, attempts_inc, job_id, uri, idx),
        )
        if status == "applied":
            success += 1
        elif status == "failed":
            failed += 1

    # recompute counters from source of truth rows
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN status IN ('applied','failed') THEN 1 ELSE 0 END) AS processed_items,
            SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) AS success_items,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_items
        FROM apply_job_items
        WHERE job_id = ?;
        """,
        (job_id,),
    )
    counts = cur.fetchone()
    cur.execute(
        """
        UPDATE apply_jobs
        SET
            processed_items = COALESCE(?, 0),
            success_items = COALESCE(?, 0),
            failed_items = COALESCE(?, 0),
            updated_at = datetime('now')
        WHERE job_id = ?;
        """,
        (
            counts["processed_items"],
            counts["success_items"],
            counts["failed_items"],
            job_id,
        ),
    )
    conn.commit()
    conn.close()


def set_apply_job_status(
    job_id: str,
    status: str,
    pause_reason: str | None = None,
    rate_limit_reset_at: int | None = None,
) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE apply_jobs
        SET
            status = ?,
            pause_reason = ?,
            rate_limit_reset_at = ?,
            updated_at = datetime('now')
        WHERE job_id = ?;
        """,
        (status, pause_reason, rate_limit_reset_at, job_id),
    )
    conn.commit()
    conn.close()


def get_apply_job(job_id: str) -> Dict[str, Any] | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            job_id, handle, status, total_items, processed_items, success_items, failed_items,
            rate_limit_reset_at, pause_reason, created_at, updated_at
        FROM apply_jobs
        WHERE job_id = ?;
        """,
        (job_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_apply_job_item_statuses(job_id: str) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT uri, image_index, status, error, attempts, updated_at
        FROM apply_job_items
        WHERE job_id = ?
        ORDER BY id ASC;
        """,
        (job_id,),
    )
    out = [dict(r) for r in cur.fetchall()]
    conn.close()
    return out


def has_pending_or_running_apply_items(job_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM apply_job_items
        WHERE job_id = ? AND status IN ('pending', 'running')
        LIMIT 1;
        """,
        (job_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def has_propagating_apply_items(job_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM apply_job_items
        WHERE job_id = ? AND status = 'propagating'
        LIMIT 1;
        """,
        (job_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def requeue_running_items(job_id: str, error: str | None = None) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE apply_job_items
        SET
            status = 'pending',
            error = COALESCE(?, error),
            updated_at = datetime('now')
        WHERE job_id = ? AND status = 'running';
        """,
        (error, job_id),
    )
    conn.commit()
    conn.close()


def mark_stale_running_jobs_paused() -> None:
    """
    On backend startup, runtime workers are empty. Persisted jobs that were "running"
    must be marked paused so UI/status remains truthful.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE apply_jobs
        SET
            status = 'paused',
            pause_reason = 'Backend restarted; queue paused',
            updated_at = datetime('now')
        WHERE status = 'running';
        """
    )
    cur.execute(
        """
        UPDATE apply_job_items
        SET
            status = 'pending',
            error = COALESCE(error, 'Requeued after backend restart'),
            updated_at = datetime('now')
        WHERE status = 'running';
        """
    )
    conn.commit()
    conn.close()
