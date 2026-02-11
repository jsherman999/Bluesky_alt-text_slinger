import json
import queue
import threading
import uuid
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atproto import Client
from atproto_client.exceptions import RequestErrorBase

try:
    from .alt_text_gen import is_enabled as altgen_is_enabled, generate_alt_text
    from . import db
except ImportError:  # Allows running as a script from backend/ during dev
    import os
    import sys

    sys.path.append(os.path.dirname(__file__))
    from alt_text_gen import is_enabled as altgen_is_enabled, generate_alt_text
    import db


# ---------- Pydantic models ----------

class ScanRequest(BaseModel):
    handle: str
    app_password: str
    generate_alt: bool = True


class ImageInfo(BaseModel):
    index: int
    thumb_url: str
    fullsize_url: str
    alt: Optional[str] = None
    generated_alt: Optional[str] = None
    apply_status: Optional[str] = None


class PostInfo(BaseModel):
    uri: str
    cid: str
    text: str
    created_at: Optional[str]
    images: List[ImageInfo]


class ScanResponse(BaseModel):
    handle: str
    total_posts: int
    total_images: int
    posts: List[PostInfo]
    alt_generation_enabled: bool


class AltUpdate(BaseModel):
    uri: str
    image_index: int
    new_alt: str


class ApplyRequest(BaseModel):
    handle: str
    app_password: str
    updates: List[AltUpdate]


class ApplyResultItem(BaseModel):
    uri: str
    image_index: int
    success: bool
    error: Optional[str] = None


class ApplyResponse(BaseModel):
    updated: List[ApplyResultItem]


class ApplyQueueStartResponse(BaseModel):
    job_id: str
    total_items: int


class ApplyQueueStateResponse(BaseModel):
    job_id: str
    handle: str
    status: str
    total_items: int
    processed_items: int
    success_items: int
    failed_items: int
    propagating_items: int
    pending_items: int
    running_items: int
    rate_limit_reset_at: Optional[int] = None
    pause_reason: Optional[str] = None
    active_uri: Optional[str] = None
    active_image_indices: List[int]
    items: List[dict]


class PropagationPendingError(Exception):
    pass


class GenerateAltItem(BaseModel):
    uri: str
    image_index: int
    fullsize_url: str
    post_text: str = ""
    current_alt: Optional[str] = None


class GenerateStartRequest(BaseModel):
    handle: str
    items: List[GenerateAltItem]


class GenerateStartResponse(BaseModel):
    job_id: str
    total_items: int


class GenerateOneRequest(BaseModel):
    handle: str
    item: GenerateAltItem


class GenerateOneResponse(BaseModel):
    generated_alt: Optional[str] = None
    error: Optional[str] = None


class GenerateEventsResponse(BaseModel):
    events: List[dict]
    done: bool
    stop_requested: bool
    total_items: int
    processed_items: int
    generated_items: int


# ---------- App setup ----------

app = FastAPI(title="Alt Text Slinger")

GEN_JOBS: Dict[str, Dict[str, Any]] = {}
GEN_JOBS_LOCK = threading.Lock()
APPLY_JOBS: Dict[str, Dict[str, Any]] = {}
APPLY_JOBS_LOCK = threading.Lock()

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://[^/]+:5173$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize SQLite tables at import time
db.init_db()
db.mark_stale_running_jobs_paused()


# ---------- Helpers ----------


@app.get("/")
def root_health() -> dict:
    """Lightweight health/ready check for curl or container monitors."""
    return {"status": "ok"}


def parse_at_uri(uri: str):
    """
    Parse an AT URI of the form:
      at://did:plc:.../app.bsky.feed.post/rkey
    into (did, collection, rkey).
    """
    if not uri.startswith("at://"):
        raise ValueError(f"Not a valid at:// URI: {uri}")
    parts = uri[5:].split("/")
    if len(parts) != 3:
        raise ValueError(f"Unexpected AT URI format: {uri}")
    did, collection, rkey = parts
    return did, collection, rkey


def verify_alts_via_public_api(
    uri: str,
    expected_by_index: Dict[int, str],
    retries: int = 12,
    delay_seconds: float = 2.0,
) -> Optional[str]:
    """
    Verify applied alt-text through the public app-view API.
    Returns None when verification passes, otherwise an error string.
    """
    encoded_uri = urllib.parse.quote(uri, safe="")
    did, collection, rkey = parse_at_uri(uri)
    repo_url = (
        "https://public.api.bsky.app/xrpc/com.atproto.repo.getRecord"
        f"?repo={urllib.parse.quote(did, safe='')}"
        f"&collection={urllib.parse.quote(collection, safe='')}"
        f"&rkey={urllib.parse.quote(rkey, safe='')}"
    )
    thread_url = (
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
        f"?uri={encoded_uri}&depth=0&parentHeight=0"
    )
    last_error: Optional[str] = None

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(repo_url, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            record = payload.get("value") or {}
            embed = record.get("embed") or {}
            embed_type = embed.get("$type") or embed.get("py_type") or ""
            images: List[dict] = []
            if embed_type == "app.bsky.embed.images":
                images = embed.get("images") or []
            elif embed_type == "app.bsky.embed.recordWithMedia":
                media = embed.get("media") or {}
                media_type = media.get("$type") or media.get("py_type") or ""
                if media_type == "app.bsky.embed.images":
                    images = media.get("images") or []

            # Fallback to appview thread path if repo.getRecord did not return image embeds.
            if not images:
                with urllib.request.urlopen(thread_url, timeout=10) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                thread = payload.get("thread") or {}
                post = thread.get("post") or {}
                record = post.get("record") or {}
                embed = record.get("embed") or {}
                embed_type = embed.get("$type") or embed.get("py_type") or ""
                if embed_type == "app.bsky.embed.images":
                    images = embed.get("images") or []
                elif embed_type == "app.bsky.embed.recordWithMedia":
                    media = embed.get("media") or {}
                    media_type = media.get("$type") or media.get("py_type") or ""
                    if media_type == "app.bsky.embed.images":
                        images = media.get("images") or []

            mismatch = None
            for idx, expected_alt in expected_by_index.items():
                if idx < 0 or idx >= len(images):
                    mismatch = f"Public verify: image index {idx} not found."
                    break
                live_alt = ((images[idx] or {}).get("alt") or "").strip()
                if live_alt != expected_alt.strip():
                    mismatch = (
                        f"Public verify mismatch at image {idx}. "
                        f"Expected '{expected_alt.strip()}', got '{live_alt}'."
                    )
                    break

            if mismatch is None:
                return None
            last_error = mismatch
        except urllib.error.HTTPError as e:
            last_error = f"Public verify HTTP {e.code}"
        except Exception as e:
            last_error = f"Public verify error: {e}"

        if attempt < retries - 1:
            time.sleep(delay_seconds)

    return last_error or "Public verification failed."


def _fetch_json_url(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_record_alt_from_payload(payload: dict) -> List[dict]:
    value = payload.get("value") or payload.get("record") or {}
    embed = value.get("embed") or {}
    embed_type = embed.get("$type") or embed.get("py_type") or ""
    images = []
    if embed_type == "app.bsky.embed.images":
        images = embed.get("images") or []
    elif embed_type == "app.bsky.embed.recordWithMedia":
        media = embed.get("media") or {}
        if (media.get("$type") or media.get("py_type") or "") == "app.bsky.embed.images":
            images = media.get("images") or []
    out = []
    for idx, img in enumerate(images):
        out.append({"index": idx, "alt": (img or {}).get("alt") or ""})
    return out


def _extract_postview_alt_from_payload(payload: dict) -> List[dict]:
    posts = payload.get("posts") or []
    if not posts:
        return []
    post = posts[0] or {}
    embed = post.get("embed") or {}
    embed_type = embed.get("$type") or embed.get("py_type") or ""
    images = []
    if embed_type == "app.bsky.embed.images#view":
        images = embed.get("images") or []
    elif embed_type == "app.bsky.embed.recordWithMedia#view":
        media = embed.get("media") or {}
        if (media.get("$type") or media.get("py_type") or "") == "app.bsky.embed.images#view":
            images = media.get("images") or []
    out = []
    for idx, img in enumerate(images):
        out.append({"index": idx, "alt": (img or {}).get("alt") or ""})
    return out


def _extract_type_debug(payload: dict) -> dict:
    value = payload.get("value") or payload.get("record") or {}
    embed = value.get("embed") or {}
    out: dict = {
        "record_type_dollar": value.get("$type"),
        "record_type_py": value.get("py_type"),
        "embed_type_dollar": embed.get("$type"),
        "embed_type_py": embed.get("py_type"),
        "embed_keys": list(embed.keys()) if isinstance(embed, dict) else [],
    }
    images = []
    if (embed.get("$type") or embed.get("py_type") or "") == "app.bsky.embed.images":
        images = embed.get("images") or []
    elif (embed.get("$type") or embed.get("py_type") or "") == "app.bsky.embed.recordWithMedia":
        media = embed.get("media") or {}
        out["media_type_dollar"] = media.get("$type")
        out["media_type_py"] = media.get("py_type")
        if (media.get("$type") or media.get("py_type") or "") == "app.bsky.embed.images":
            images = media.get("images") or []
    if images:
        first = images[0] or {}
        out["image0_keys"] = list(first.keys()) if isinstance(first, dict) else []
    return out


def debug_compare_alt_views(uri: str) -> dict:
    did, collection, rkey = parse_at_uri(uri)

    did_doc_url = f"https://plc.directory/{urllib.parse.quote(did, safe='')}"
    did_doc = _fetch_json_url(did_doc_url)
    services = did_doc.get("service") or []
    pds_endpoint = None
    for svc in services:
        if (svc or {}).get("type") == "AtprotoPersonalDataServer":
            pds_endpoint = (svc or {}).get("serviceEndpoint")
            break

    pds_record_url = None
    pds_record = {}
    if pds_endpoint:
        pds_record_url = (
            f"{pds_endpoint}/xrpc/com.atproto.repo.getRecord"
            f"?repo={urllib.parse.quote(did, safe='')}"
            f"&collection={urllib.parse.quote(collection, safe='')}"
            f"&rkey={urllib.parse.quote(rkey, safe='')}"
        )
        pds_record = _fetch_json_url(pds_record_url)

    public_repo_url = (
        "https://public.api.bsky.app/xrpc/com.atproto.repo.getRecord"
        f"?repo={urllib.parse.quote(did, safe='')}"
        f"&collection={urllib.parse.quote(collection, safe='')}"
        f"&rkey={urllib.parse.quote(rkey, safe='')}"
    )
    public_repo_record = _fetch_json_url(public_repo_url)

    public_thread_url = (
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
        f"?uri={urllib.parse.quote(uri, safe='')}&depth=0&parentHeight=0"
    )
    public_thread = _fetch_json_url(public_thread_url)
    thread_post = ((public_thread.get("thread") or {}).get("post") or {})
    thread_record = {"record": thread_post.get("record") or {}}
    public_posts_url = (
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts"
        f"?uris={urllib.parse.quote(uri, safe='')}"
    )
    public_posts_payload = _fetch_json_url(public_posts_url)

    return {
        "uri": uri,
        "did": did,
        "pds_endpoint": pds_endpoint,
        "pds_cid": (pds_record or {}).get("cid"),
        "public_repo_cid": (public_repo_record or {}).get("cid"),
        "pds_record_url": pds_record_url,
        "public_repo_url": public_repo_url,
        "public_thread_url": public_thread_url,
        "public_posts_url": public_posts_url,
        "pds_record_alts": _extract_record_alt_from_payload(pds_record) if pds_record else [],
        "public_repo_alts": _extract_record_alt_from_payload(public_repo_record),
        "public_thread_record_alts": _extract_record_alt_from_payload(thread_record),
        "public_posts_view_alts": _extract_postview_alt_from_payload(public_posts_payload),
        "pds_type_debug": _extract_type_debug(pds_record) if pds_record else {},
        "public_repo_type_debug": _extract_type_debug(public_repo_record),
        "public_thread_type_debug": _extract_type_debug(thread_record),
    }


def _rate_limit_error_message(exc: Exception) -> Optional[str]:
    """
    Build a concise, user-friendly message for Bluesky 429 responses.
    """
    if not isinstance(exc, RequestErrorBase):
        return None
    resp = getattr(exc, "response", None)
    if not resp or getattr(resp, "status_code", None) != 429:
        return None

    headers = getattr(resp, "headers", {}) or {}
    reset_raw = headers.get("ratelimit-reset") or headers.get("x-ratelimit-reset")
    remaining = headers.get("ratelimit-remaining")
    limit = headers.get("ratelimit-limit")

    if reset_raw:
        try:
            reset_ts = int(str(reset_raw).strip())
            now_ts = int(time.time())
            wait_s = max(0, reset_ts - now_ts)
            reset_local = datetime.fromtimestamp(reset_ts).strftime("%Y-%m-%d %H:%M:%S")
            return (
                f"Rate limit exceeded on Bluesky API. Retry after {reset_local} local time "
                f"(about {wait_s // 60}m {wait_s % 60}s). "
                f"remaining={remaining or '?'} limit={limit or '?'}"
            )
        except Exception:
            pass

    return (
        "Rate limit exceeded on Bluesky API. Retry later. "
        f"remaining={remaining or '?'} limit={limit or '?'}"
    )


def _rate_limit_reset_ts(exc: Exception) -> Optional[int]:
    if not isinstance(exc, RequestErrorBase):
        return None
    resp = getattr(exc, "response", None)
    if not resp or getattr(resp, "status_code", None) != 429:
        return None
    headers = getattr(resp, "headers", {}) or {}
    reset_raw = headers.get("ratelimit-reset") or headers.get("x-ratelimit-reset")
    if not reset_raw:
        return None
    try:
        return int(str(reset_raw).strip())
    except Exception:
        return None


def normalize_handle(handle: str) -> str:
    """Accept either '@user.bsky.social' or 'user.bsky.social'."""
    return handle.strip().lstrip("@")


def get_field(obj: Any, *keys: str) -> Any:
    """Get the first present key from either dict-like or model-like objects."""
    for key in keys:
        if isinstance(obj, dict) and key in obj:
            return obj[key]
        if hasattr(obj, key):
            return getattr(obj, key)
    return None


def get_type_name(obj: Any) -> str:
    return get_field(obj, "$type", "py_type") or ""


def canonicalize_for_atproto(value: Any) -> Any:
    """
    Normalize SDK-shaped payloads into canonical ATProto JSON:
    - recursively map `py_type` -> `$type`
    - drop null fields
    """
    if isinstance(value, list):
        return [canonicalize_for_atproto(v) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            key = "$type" if k == "py_type" else k
            out[key] = canonicalize_for_atproto(v)
        if "$type" not in out and "py_type" in value and value.get("py_type"):
            out["$type"] = value.get("py_type")
        return out
    return value


def extract_image_views_from_post(post: Any) -> List[Any]:
    """
    Return image view objects from post embeds.
    Handles both direct image embeds and recordWithMedia wrappers.
    """
    embed = get_field(post, "embed")
    if not embed:
        return []

    embed_type = get_type_name(embed)
    if embed_type == "app.bsky.embed.images#view":
        return list(get_field(embed, "images") or [])

    if embed_type == "app.bsky.embed.recordWithMedia#view":
        media = get_field(embed, "media")
        if get_type_name(media) == "app.bsky.embed.images#view":
            return list(get_field(media, "images") or [])

    return []


def run_scan(
    req: ScanRequest, progress_cb: Optional[Callable[[dict], None]] = None
) -> ScanResponse:
    def emit(
        message: str,
        posts_scanned: Optional[int] = None,
        images_found: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> None:
        if not progress_cb:
            return
        payload: dict = {"type": "progress", "message": message}
        if posts_scanned is not None:
            payload["posts_scanned"] = posts_scanned
        if images_found is not None:
            payload["images_found"] = images_found
        if extra:
            payload.update(extra)
        progress_cb(payload)

    handle = normalize_handle(req.handle)
    client = Client()

    try:
        client.login(handle, req.app_password)
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail="Failed to login to Bluesky. Check handle/app password.",
        ) from e

    emit(f"Authenticated as {handle}. Starting feed scan.")
    me_did = get_field(get_field(client, "me"), "did")

    posts_with_images: List[PostInfo] = []
    cached_generated = db.get_generated_alt_map(handle)
    cached_apply_status = db.get_image_status_map(handle)
    cursor = None
    page_num = 0
    scanned_posts = 0
    found_images = 0

    altgen_active = altgen_is_enabled() and req.generate_alt

    while True:
        page_num += 1
        emit(
            f"Fetching feed page {page_num}...",
            posts_scanned=scanned_posts,
            images_found=found_images,
        )
        try:
            feed = client.get_author_feed(
                actor=handle,
                cursor=cursor,
                filter="posts_with_replies",
                limit=100,
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching author feed: {e}",
            ) from e

        for item in feed.feed:
            post = item.post
            author = get_field(post, "author")
            author_handle = normalize_handle(str(get_field(author, "handle") or ""))
            author_did = get_field(author, "did")
            if (author_handle and author_handle != handle) or (
                me_did and author_did and author_did != me_did
            ):
                # getAuthorFeed can include repost-context posts; only scan posts authored by the user.
                continue
            record = get_field(post, "record")
            uri = get_field(post, "uri")
            cid = get_field(post, "cid")
            text = get_field(record, "text") or ""
            created_at = get_field(record, "created_at", "createdAt")
            scanned_posts += 1

            images_from_embed = extract_image_views_from_post(post)
            if not images_from_embed:
                emit(
                    f"Scanned post {uri} - no image embeds.",
                    posts_scanned=scanned_posts,
                    images_found=found_images,
                    extra={
                        "event": "post_scanned",
                        "post_uri": uri,
                        "post_state": "no_images",
                        "missing_images_needing_generation": 0,
                    },
                )
                continue

            images: List[ImageInfo] = []
            missing_images_needing_generation = 0
            total_images_missing_alt = 0
            for idx, img in enumerate(images_from_embed):
                alt = get_field(img, "alt")
                thumb_url = get_field(img, "thumb")
                fullsize_url = get_field(img, "fullsize")
                if not thumb_url or not fullsize_url:
                    continue

                generated_alt: Optional[str] = cached_generated.get((uri, idx))
                missing_alt = not alt or not str(alt).strip()
                if missing_alt:
                    total_images_missing_alt += 1
                if (
                    not generated_alt
                    and altgen_active
                    and missing_alt
                ):
                    generated_alt = generate_alt_text(fullsize_url, text)
                    if generated_alt:
                        db.record_generated_alt(handle, uri, idx, generated_alt, "generated")
                if missing_alt and not generated_alt:
                    missing_images_needing_generation += 1

                images.append(
                    ImageInfo(
                        index=idx,
                        thumb_url=thumb_url,
                        fullsize_url=fullsize_url,
                        alt=alt,
                        generated_alt=generated_alt,
                        apply_status=cached_apply_status.get((uri, idx)),
                    )
                )
                found_images += 1
                emit(
                    f"Found image in {uri} (index {idx}, alt {'present' if alt and str(alt).strip() else 'missing'}).",
                    posts_scanned=scanned_posts,
                    images_found=found_images,
                )

            if images:
                if missing_images_needing_generation > 0:
                    post_state = "images_missing_alt"
                elif total_images_missing_alt > 0:
                    post_state = "images_generated_not_applied"
                else:
                    post_state = "images_alt_ready"
                emit(
                    f"Scanned post {uri}.",
                    posts_scanned=scanned_posts,
                    images_found=found_images,
                    extra={
                        "event": "post_scanned",
                        "post_uri": uri,
                        "post_state": post_state,
                        "missing_images_needing_generation": missing_images_needing_generation,
                    },
                )
                posts_with_images.append(
                    PostInfo(
                        uri=uri,
                        cid=cid,
                        text=text,
                        created_at=created_at,
                        images=images,
                    )
                )

        cursor = feed.cursor
        if not cursor:
            break

    total_images = sum(len(p.images) for p in posts_with_images)
    db.save_scan(handle, [p.model_dump() for p in posts_with_images])

    emit(
        f"Scan complete. Posts with images: {len(posts_with_images)}. Images found: {total_images}.",
        posts_scanned=scanned_posts,
        images_found=total_images,
    )

    return ScanResponse(
        handle=handle,
        total_posts=len(posts_with_images),
        total_images=total_images,
        posts=posts_with_images,
        alt_generation_enabled=altgen_is_enabled(),
    )


def _add_gen_event(job: Dict[str, Any], payload: dict) -> None:
    with job["lock"]:
        seq = job["next_seq"]
        job["next_seq"] += 1
        event = {"seq": seq, **payload}
        job["events"].append(event)
        if len(job["events"]) > 10000:
            job["events"] = job["events"][-5000:]


def _run_generation_job(job_id: str, req: GenerateStartRequest) -> None:
    with GEN_JOBS_LOCK:
        job = GEN_JOBS.get(job_id)
    if not job:
        return

    for item in req.items:
        if job["cancel_event"].is_set():
            break

        _add_gen_event(
            job,
            {
                "type": "item_started",
                "uri": item.uri,
                "image_index": item.image_index,
            },
        )

        generated_alt: Optional[str] = None
        error: Optional[str] = None
        try:
            if item.current_alt and item.current_alt.strip():
                generated_alt = item.current_alt.strip()
            else:
                generated_alt = generate_alt_text(item.fullsize_url, item.post_text)
                if generated_alt:
                    job["generated_items"] += 1
                    db.record_generated_alt(
                        req.handle,
                        item.uri,
                        item.image_index,
                        generated_alt,
                        "generated",
                    )
        except Exception as e:
            error = str(e)

        job["processed_items"] += 1
        _add_gen_event(
            job,
            {
                "type": "item_result",
                "uri": item.uri,
                "image_index": item.image_index,
                "generated_alt": generated_alt,
                "error": error,
            },
        )

    job["done"] = True
    _add_gen_event(
        job,
        {
            "type": "complete",
            "stop_requested": job["cancel_event"].is_set(),
            "total_items": job["total_items"],
            "processed_items": job["processed_items"],
            "generated_items": job["generated_items"],
        },
    )


# ---------- /api/scan ----------

@app.post("/api/scan", response_model=ScanResponse)
def scan_images(req: ScanRequest) -> ScanResponse:
    return run_scan(req)


@app.post("/api/scan/stream")
def scan_images_stream(req: ScanRequest) -> StreamingResponse:
    stream_queue: queue.Queue[Any] = queue.Queue()
    done = object()

    def progress_cb(payload: dict) -> None:
        stream_queue.put(payload)

    def worker() -> None:
        try:
            result = run_scan(req, progress_cb=progress_cb)
            stream_queue.put({"type": "result", "data": result.model_dump()})
        except HTTPException as e:
            stream_queue.put(
                {
                    "type": "error",
                    "error": {"status_code": e.status_code, "detail": e.detail},
                }
            )
        except Exception as e:
            stream_queue.put(
                {"type": "error", "error": {"status_code": 500, "detail": str(e)}}
            )
        finally:
            stream_queue.put(done)

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            item = stream_queue.get()
            if item is done:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/api/generate/start", response_model=GenerateStartResponse)
def generate_start(req: GenerateStartRequest) -> GenerateStartResponse:
    if not altgen_is_enabled():
        raise HTTPException(
            status_code=400,
            detail="Alt-text generation is disabled. Configure OPENAI_API_KEY.",
        )

    handle = normalize_handle(req.handle)
    filtered_items = [
        item for item in req.items if not (item.current_alt and item.current_alt.strip())
    ]
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "handle": handle,
        "events": [],
        "next_seq": 1,
        "done": False,
        "cancel_event": threading.Event(),
        "total_items": len(filtered_items),
        "processed_items": 0,
        "generated_items": 0,
        "lock": threading.Lock(),
    }
    with GEN_JOBS_LOCK:
        GEN_JOBS[job_id] = job

    _add_gen_event(
        job,
        {
            "type": "started",
            "message": f"Starting alt generation for {len(filtered_items)} images.",
            "total_items": len(filtered_items),
        },
    )

    job_req = GenerateStartRequest(handle=handle, items=filtered_items)
    threading.Thread(target=_run_generation_job, args=(job_id, job_req), daemon=True).start()
    return GenerateStartResponse(job_id=job_id, total_items=len(filtered_items))


@app.get("/api/generate/events/{job_id}", response_model=GenerateEventsResponse)
def generate_events(job_id: str, after: int = Query(default=0, ge=0)) -> GenerateEventsResponse:
    with GEN_JOBS_LOCK:
        job = GEN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found.")

    with job["lock"]:
        events = [e for e in job["events"] if e["seq"] > after]
        done = job["done"]
        stop_requested = job["cancel_event"].is_set()
        total_items = job["total_items"]
        processed_items = job["processed_items"]
        generated_items = job["generated_items"]

    return GenerateEventsResponse(
        events=events,
        done=done,
        stop_requested=stop_requested,
        total_items=total_items,
        processed_items=processed_items,
        generated_items=generated_items,
    )


@app.post("/api/generate/one", response_model=GenerateOneResponse)
def generate_one(req: GenerateOneRequest) -> GenerateOneResponse:
    if not altgen_is_enabled():
        raise HTTPException(
            status_code=400,
            detail="Alt-text generation is disabled. Configure OPENAI_API_KEY or OPENROUTER_API_KEY.",
        )

    handle = normalize_handle(req.handle)
    item = req.item
    try:
        generated_alt = generate_alt_text(item.fullsize_url, item.post_text)
        if generated_alt and generated_alt.strip():
            db.record_generated_alt(
                handle,
                item.uri,
                item.image_index,
                generated_alt.strip(),
                "regenerated",
            )
            return GenerateOneResponse(generated_alt=generated_alt.strip())
        return GenerateOneResponse(generated_alt=None, error="No text returned.")
    except Exception as e:
        return GenerateOneResponse(generated_alt=None, error=str(e))


@app.post("/api/generate/stop/{job_id}")
def generate_stop(job_id: str) -> dict:
    with GEN_JOBS_LOCK:
        job = GEN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found.")

    job["cancel_event"].set()
    _add_gen_event(
        job,
        {"type": "stop_requested", "message": "Stop requested by user."},
    )
    return {"status": "stopping"}


# ---------- /api/apply ----------

def _apply_updates_for_uri(
    client: Client,
    handle: str,
    uri: str,
    updates: List[AltUpdate],
    verify_public: bool = False,
) -> List[ApplyResultItem]:
    did, collection, rkey = parse_at_uri(uri)

    try:
        rec_resp = client.com.atproto.repo.get_record(
            params={"repo": did, "collection": collection, "rkey": rkey}
        )
    except TypeError:
        rec_resp = client.com.atproto.repo.get_record(
            repo=did,
            collection=collection,
            rkey=rkey,
        )

    record = getattr(rec_resp, "value", None)
    rec_cid = getattr(rec_resp, "cid", None)
    if record is None:
        if isinstance(rec_resp, dict) and "value" in rec_resp:
            record = rec_resp["value"]
            rec_cid = rec_resp.get("cid")
        else:
            raise RuntimeError("Could not locate record value in response")

    if hasattr(record, "model_dump"):
        record = record.model_dump(by_alias=True, exclude_none=True)
    elif not isinstance(record, dict):
        raise RuntimeError(f"Unsupported record object type: {type(record)}")
    record = canonicalize_for_atproto(record)

    embed = record.get("embed")
    if not embed:
        raise RuntimeError("Record has no embed")

    embed_type = get_type_name(embed)
    if embed_type == "app.bsky.embed.images":
        images = get_field(embed, "images") or []
    elif embed_type == "app.bsky.embed.recordWithMedia":
        media = get_field(embed, "media") or {}
        if get_type_name(media) != "app.bsky.embed.images":
            raise RuntimeError(
                "recordWithMedia is present, but media is not app.bsky.embed.images"
            )
        images = get_field(media, "images") or []
    else:
        embed_keys = list(embed.keys()) if isinstance(embed, dict) else []
        raise RuntimeError(
            f"Unsupported embed type for alt updates: {embed_type}; keys={embed_keys}"
        )

    if not isinstance(images, list):
        raise RuntimeError("Record embed images collection is not a list")

    applied_count = 0
    for upd in updates:
        idx = upd.image_index
        if idx < 0 or idx >= len(images):
            continue
        image_entry = images[idx]
        if isinstance(image_entry, dict):
            image_entry["alt"] = upd.new_alt
        elif hasattr(image_entry, "alt"):
            setattr(image_entry, "alt", upd.new_alt)
        else:
            raise RuntimeError(
                f"Image entry at index {idx} is not editable: {type(image_entry)}"
            )
        applied_count += 1

    if applied_count == 0:
        raise RuntimeError(
            f"No matching image indices to update (requested {[u.image_index for u in updates]}, available 0..{max(len(images)-1, 0)})."
        )

    try:
        put_data = {
            "repo": did,
            "collection": collection,
            "rkey": rkey,
            "record": record,
            "validate": True,
        }
        if rec_cid:
            put_data["swapRecord"] = rec_cid
        put_resp = client.com.atproto.repo.put_record(data=put_data)
    except TypeError:
        put_resp = client.com.atproto.repo.put_record(
            repo=did,
            collection=collection,
            rkey=rkey,
            record=record,
            validate_=True,
            swap_record=rec_cid if rec_cid else None,
        )

    put_validation_status = get_field(put_resp, "validation_status", "validationStatus")
    if put_validation_status and str(put_validation_status) not in {"valid", "unknown"}:
        raise RuntimeError(f"Put record validation status: {put_validation_status}")

    # Low-cost verification through repo read. Public app-view verification can be enabled
    # for spot checks, but doing it per-item materially increases request volume.
    try:
        verify_resp = client.com.atproto.repo.get_record(
            params={"repo": did, "collection": collection, "rkey": rkey}
        )
    except TypeError:
        verify_resp = client.com.atproto.repo.get_record(
            repo=did,
            collection=collection,
            rkey=rkey,
        )
    verify_record = getattr(verify_resp, "value", None)
    if hasattr(verify_record, "model_dump"):
        verify_record = verify_record.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(verify_record, dict):
        raise RuntimeError("Could not verify updated record payload type.")

    verify_embed = verify_record.get("embed") or {}
    verify_embed_type = get_type_name(verify_embed)
    if verify_embed_type == "app.bsky.embed.images":
        verify_images = get_field(verify_embed, "images") or []
    elif verify_embed_type == "app.bsky.embed.recordWithMedia":
        verify_media = get_field(verify_embed, "media") or {}
        verify_images = get_field(verify_media, "images") or []
    else:
        verify_images = []

    for upd in updates:
        idx = upd.image_index
        if idx < 0 or idx >= len(verify_images):
            raise RuntimeError(
                f"Post-update verification failed: image index {idx} missing."
            )
        live_alt = get_field(verify_images[idx] or {}, "alt") or ""
        if (live_alt or "").strip() != upd.new_alt.strip():
            raise RuntimeError(
                f"Post-update verification mismatch at image {idx}. Expected alt to persist."
            )

    if verify_public:
        expected_by_index = {upd.image_index: upd.new_alt for upd in updates}
        public_verify_error = verify_alts_via_public_api(uri, expected_by_index)
        if public_verify_error:
            debug_suffix = ""
            pds_has_expected = False
            try:
                cmp = debug_compare_alt_views(uri)
                pds_alts = cmp.get("pds_record_alts") or []
                public_repo_alts = cmp.get("public_repo_alts") or []
                public_thread_alts = cmp.get("public_thread_record_alts") or []
                pds_map = {int(x.get("index", -1)): str(x.get("alt") or "") for x in pds_alts}
                pds_has_expected = all(
                    pds_map.get(int(idx), "").strip() == str(expected).strip()
                    for idx, expected in expected_by_index.items()
                )
                debug_suffix = (
                    f" | debug pds_alts={pds_alts[:4]} "
                    f"public_repo_alts={public_repo_alts[:4]} "
                    f"public_thread_record_alts={public_thread_alts[:4]}"
                )
            except Exception as dbg_e:
                debug_suffix = f" | debug compare failed: {dbg_e}"
            if pds_has_expected:
                raise PropagationPendingError(
                    "PDS accepted alt updates, waiting for public appview propagation."
                    + debug_suffix
                )
            raise RuntimeError(public_verify_error + debug_suffix)

    out: List[ApplyResultItem] = []
    for upd in updates:
        db.record_image_update(
            handle=handle,
            uri=uri,
            image_index=upd.image_index,
            new_alt=upd.new_alt,
            status="applied",
        )
        out.append(
            ApplyResultItem(
                uri=uri,
                image_index=upd.image_index,
                success=True,
                error=None,
            )
        )
    return out

@app.post("/api/apply", response_model=ApplyResponse)
def apply_alt_updates(req: ApplyRequest) -> ApplyResponse:
    if not req.updates:
        return ApplyResponse(updated=[])

    handle = normalize_handle(req.handle)
    client = Client()
    try:
        client.login(handle, req.app_password)
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail="Failed to login to Bluesky. Check handle/app password.",
        ) from e

    updates_by_uri: Dict[str, List[AltUpdate]] = {}
    for upd in req.updates:
        updates_by_uri.setdefault(upd.uri, []).append(upd)

    results: List[ApplyResultItem] = []
    for uri, updates in updates_by_uri.items():
        try:
            results.extend(_apply_updates_for_uri(client, handle, uri, updates, verify_public=False))
        except Exception as e:
            pretty_rate_limit = _rate_limit_error_message(e)
            err_msg = pretty_rate_limit or str(e)
            print(f"[apply] Failed for {uri}: {err_msg}")
            for upd in updates:
                db.record_image_update(
                    handle=handle,
                    uri=uri,
                    image_index=upd.image_index,
                    new_alt=upd.new_alt,
                    status="failed",
                )
                results.append(
                    ApplyResultItem(
                        uri=uri,
                        image_index=upd.image_index,
                        success=False,
                        error=err_msg,
                    )
                )

    return ApplyResponse(updated=results)


def _run_apply_queue_worker(job_id: str) -> None:
    with APPLY_JOBS_LOCK:
        runtime = APPLY_JOBS.get(job_id)
    if not runtime:
        return

    handle = runtime["handle"]
    app_password = runtime["app_password"]
    min_interval_s = runtime["min_interval_s"]
    client = Client()
    try:
        client.login(handle, app_password)
    except Exception as e:
        db.set_apply_job_status(job_id, "paused", pause_reason=f"Login failed: {e}")
        return

    last_run_at = 0.0
    last_propagation_check_at = 0.0
    max_propagation_checks = 36  # ~6 minutes at 10s cadence before hard failure
    while True:
        with APPLY_JOBS_LOCK:
            rt = APPLY_JOBS.get(job_id)
            if not rt:
                return
            manual_paused = rt.get("manual_paused", False)
            stop_requested = rt.get("stop_requested", False)
            pause_until_ts = rt.get("pause_until_ts")
            rt["active_uri"] = None
            rt["active_image_indices"] = []

        if stop_requested:
            db.set_apply_job_status(job_id, "paused", pause_reason="Paused by user")
            return

        now_ts = int(time.time())
        if manual_paused:
            db.set_apply_job_status(job_id, "paused", pause_reason="Paused by user")
            time.sleep(0.4)
            continue

        if pause_until_ts and now_ts < pause_until_ts:
            db.set_apply_job_status(
                job_id,
                "paused",
                pause_reason="Paused due to Bluesky rate limit",
                rate_limit_reset_at=pause_until_ts,
            )
            time.sleep(0.8)
            continue
        elif pause_until_ts and now_ts >= pause_until_ts:
            with APPLY_JOBS_LOCK:
                rt = APPLY_JOBS.get(job_id)
                if rt:
                    rt["pause_until_ts"] = None
            db.set_apply_job_status(job_id, "running", pause_reason=None, rate_limit_reset_at=None)

        # Throttle request cadence.
        elapsed = time.time() - last_run_at
        if elapsed < min_interval_s:
            time.sleep(min_interval_s - elapsed)

        # First, periodically check if any previously written items have propagated.
        now = time.time()
        if now - last_propagation_check_at >= 10.0:
            prop_group = db.claim_next_propagating_uri_group(job_id)
            if prop_group:
                p_uri = prop_group[0]["uri"]
                expected = {int(x["image_index"]): x["new_alt"] for x in prop_group}
                pub_err = verify_alts_via_public_api(p_uri, expected, retries=1, delay_seconds=0.0)
                if pub_err is None:
                    # Public now reflects the alt text; finalize.
                    db.mark_apply_items(
                        job_id,
                        p_uri,
                        [
                            {"image_index": int(x["image_index"]), "status": "applied", "error": None}
                            for x in prop_group
                        ],
                    )
                    for row in prop_group:
                        db.record_image_update(
                            handle=handle,
                            uri=p_uri,
                            image_index=int(row["image_index"]),
                            new_alt=row["new_alt"],
                            status="applied",
                        )
                else:
                    next_attempt = max(int(x.get("attempts", 0)) for x in prop_group) + 1
                    if next_attempt >= max_propagation_checks:
                        fail_msg = (
                            "Public appview did not reflect PDS alt updates after repeated checks. "
                            f"Last verify: {pub_err}"
                        )
                        print(f"[apply-queue] Propagation timeout for {p_uri}: {fail_msg}")
                        db.mark_apply_items(
                            job_id,
                            p_uri,
                            [
                                {"image_index": int(x["image_index"]), "status": "failed", "error": fail_msg}
                                for x in prop_group
                            ],
                        )
                        for row in prop_group:
                            db.record_image_update(
                                handle=handle,
                                uri=p_uri,
                                image_index=int(row["image_index"]),
                                new_alt=row["new_alt"],
                                status="failed",
                            )
                    else:
                        db.mark_apply_items(
                            job_id,
                            p_uri,
                            [
                                {
                                    "image_index": int(x["image_index"]),
                                    "status": "propagating",
                                    "error": f"{pub_err} (check {next_attempt}/{max_propagation_checks})",
                                }
                                for x in prop_group
                            ],
                        )
                last_propagation_check_at = now

        claimed = db.claim_next_pending_uri_group(job_id)
        if not claimed:
            if db.has_pending_or_running_apply_items(job_id):
                time.sleep(0.25)
                continue
            if db.has_propagating_apply_items(job_id):
                db.set_apply_job_status(
                    job_id,
                    "running",
                    pause_reason="Waiting for Bluesky appview propagation",
                    rate_limit_reset_at=None,
                )
                time.sleep(1.0)
                continue
            else:
                db.set_apply_job_status(job_id, "completed", pause_reason=None, rate_limit_reset_at=None)
            return

        uri = claimed[0]["uri"]
        idxs = [int(x["image_index"]) for x in claimed]
        with APPLY_JOBS_LOCK:
            rt = APPLY_JOBS.get(job_id)
            if rt:
                rt["active_uri"] = uri
                rt["active_image_indices"] = idxs

        updates = [
            AltUpdate(uri=uri, image_index=int(row["image_index"]), new_alt=row["new_alt"])
            for row in claimed
        ]

        try:
            apply_results = _apply_updates_for_uri(client, handle, uri, updates, verify_public=True)
            db.mark_apply_items(
                job_id,
                uri,
                [
                    {
                        "image_index": r.image_index,
                        "status": "applied" if r.success else "failed",
                        "error": r.error,
                    }
                    for r in apply_results
                ],
            )
        except PropagationPendingError as e:
            err_msg = str(e)
            print(f"[apply-queue] Propagation pending for {uri}: {err_msg}")
            db.mark_apply_items(
                job_id,
                uri,
                [
                    {"image_index": int(row["image_index"]), "status": "propagating", "error": err_msg}
                    for row in claimed
                ],
            )
            for row in claimed:
                db.record_image_update(
                    handle=handle,
                    uri=uri,
                    image_index=int(row["image_index"]),
                    new_alt=row["new_alt"],
                    status="propagating",
                )
        except Exception as e:
            rate_msg = _rate_limit_error_message(e)
            reset_ts = _rate_limit_reset_ts(e)
            err_msg = rate_msg or str(e)
            print(f"[apply-queue] Failed for {uri}: {err_msg}")
            if reset_ts:
                # Requeue in-flight items and auto-resume after reset.
                db.requeue_running_items(job_id, error=err_msg)
                with APPLY_JOBS_LOCK:
                    rt = APPLY_JOBS.get(job_id)
                    if rt:
                        rt["pause_until_ts"] = reset_ts
                db.set_apply_job_status(
                    job_id,
                    "paused",
                    pause_reason="Paused due to Bluesky rate limit",
                    rate_limit_reset_at=reset_ts,
                )
            else:
                db.mark_apply_items(
                    job_id,
                    uri,
                    [
                        {"image_index": int(row["image_index"]), "status": "failed", "error": err_msg}
                        for row in claimed
                    ],
                )

        last_run_at = time.time()


@app.post("/api/apply/queue/start", response_model=ApplyQueueStartResponse)
def apply_queue_start(req: ApplyRequest) -> ApplyQueueStartResponse:
    if not req.updates:
        raise HTTPException(status_code=400, detail="No updates were provided.")

    handle = normalize_handle(req.handle)
    job_id = str(uuid.uuid4())
    payloads = [u.model_dump() for u in req.updates]
    db.create_apply_job(job_id, handle, len(payloads))
    db.insert_apply_job_items(job_id, payloads)
    db.set_apply_job_status(job_id, "running")

    with APPLY_JOBS_LOCK:
        APPLY_JOBS[job_id] = {
            "job_id": job_id,
            "handle": handle,
            "app_password": req.app_password,
            "manual_paused": False,
            "stop_requested": False,
            "pause_until_ts": None,
            "active_uri": None,
            "active_image_indices": [],
            # ~0.5 uri-batches/sec to reduce pressure on the 5000/hour cap.
            "min_interval_s": 2.0,
        }

    threading.Thread(target=_run_apply_queue_worker, args=(job_id,), daemon=True).start()
    return ApplyQueueStartResponse(job_id=job_id, total_items=len(payloads))


@app.post("/api/apply/queue/pause/{job_id}")
def apply_queue_pause(job_id: str) -> dict:
    with APPLY_JOBS_LOCK:
        rt = APPLY_JOBS.get(job_id)
        if not rt:
            # Persisted job may exist without runtime process after restart.
            job = db.get_apply_job(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="Apply queue job not found.")
            db.set_apply_job_status(job_id, "paused", pause_reason="Paused by user")
            return {"status": "paused"}
        rt["manual_paused"] = True
    db.set_apply_job_status(job_id, "paused", pause_reason="Paused by user")
    return {"status": "paused"}


@app.post("/api/apply/queue/resume/{job_id}")
def apply_queue_resume(
    job_id: str,
    handle: str = Query(default=""),
    app_password: str = Query(default=""),
) -> dict:
    job = db.get_apply_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Apply queue job not found.")

    with APPLY_JOBS_LOCK:
        rt = APPLY_JOBS.get(job_id)
        if rt:
            rt["manual_paused"] = False
            rt["stop_requested"] = False
            rt["pause_until_ts"] = None
            db.set_apply_job_status(job_id, "running", pause_reason=None, rate_limit_reset_at=None)
            return {"status": "running"}

        if not app_password:
            raise HTTPException(
                status_code=400,
                detail="Queue runtime was not active. Provide app_password to resume this persisted job.",
            )

        runtime_handle = normalize_handle(handle or job["handle"])
        APPLY_JOBS[job_id] = {
            "job_id": job_id,
            "handle": runtime_handle,
            "app_password": app_password,
            "manual_paused": False,
            "stop_requested": False,
            "pause_until_ts": None,
            "active_uri": None,
            "active_image_indices": [],
            "min_interval_s": 2.0,
        }
    db.set_apply_job_status(job_id, "running", pause_reason=None, rate_limit_reset_at=None)
    threading.Thread(target=_run_apply_queue_worker, args=(job_id,), daemon=True).start()
    return {"status": "running"}


@app.get("/api/apply/queue/state/{job_id}", response_model=ApplyQueueStateResponse)
def apply_queue_state(job_id: str) -> ApplyQueueStateResponse:
    job = db.get_apply_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Apply queue job not found.")
    items = db.get_apply_job_item_statuses(job_id)
    pending_items = sum(1 for x in items if x["status"] == "pending")
    running_items = sum(1 for x in items if x["status"] == "running")
    propagating_items = sum(1 for x in items if x["status"] == "propagating")

    active_uri = None
    active_image_indices: List[int] = []
    with APPLY_JOBS_LOCK:
        rt = APPLY_JOBS.get(job_id)
        if rt:
            active_uri = rt.get("active_uri")
            active_image_indices = list(rt.get("active_image_indices") or [])

    return ApplyQueueStateResponse(
        job_id=job["job_id"],
        handle=job["handle"],
        status=job["status"],
        total_items=job["total_items"],
        processed_items=job["processed_items"],
        success_items=job["success_items"],
        failed_items=job["failed_items"],
        propagating_items=propagating_items,
        pending_items=pending_items,
        running_items=running_items,
        rate_limit_reset_at=job.get("rate_limit_reset_at"),
        pause_reason=job.get("pause_reason"),
        active_uri=active_uri,
        active_image_indices=active_image_indices,
        items=items,
    )


@app.get("/api/debug/alt-compare")
def debug_alt_compare(uri: str) -> dict:
    try:
        return debug_compare_alt_views(uri)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Alt compare failed: {e}") from e
