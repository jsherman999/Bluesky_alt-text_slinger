from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict

from atproto import Client

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
    success: bool
    error: Optional[str] = None


class ApplyResponse(BaseModel):
    updated: List[ApplyResultItem]


# ---------- App setup ----------

app = FastAPI(title="Bluesky Alt-Text Slinger – Phase 4")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize SQLite tables at import time
db.init_db()


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


# ---------- /api/scan ----------

@app.post("/api/scan", response_model=ScanResponse)
def scan_images(req: ScanRequest) -> ScanResponse:
    client = Client()

    try:
        client.login(req.handle, req.app_password)
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail="Failed to login to Bluesky. Check handle/app password.",
        ) from e

    posts_with_images: List[PostInfo] = []
    cursor = None

    altgen_active = altgen_is_enabled() and req.generate_alt

    while True:
        try:
            feed = client.get_author_feed(actor=req.handle, cursor=cursor)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching author feed: {e}",
            )

        for item in feed.feed:
            post = item.post
            record = post.record
            uri = post.uri
            cid = post.cid

            embed = getattr(post, "embed", None)
            if not embed or getattr(embed, "$type", "") != "app.bsky.embed.images#view":
                continue

            text = getattr(record, "text", "") or ""
            created_at = getattr(record, "created_at", None)

            images: List[ImageInfo] = []
            for idx, img in enumerate(embed.images):
                alt = img.alt if hasattr(img, "alt") else None
                thumb_url = img.thumb
                fullsize_url = img.fullsize

                generated_alt: Optional[str] = None
                if altgen_active and (not alt or not alt.strip()):
                    generated_alt = generate_alt_text(fullsize_url, text)

                images.append(
                    ImageInfo(
                        index=idx,
                        thumb_url=thumb_url,
                        fullsize_url=fullsize_url,
                        alt=alt,
                        generated_alt=generated_alt,
                    )
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

    # Persist scan results to SQLite
    db.save_scan(req.handle, [p.model_dump() for p in posts_with_images])

    return ScanResponse(
        handle=req.handle,
        total_posts=len(posts_with_images),
        total_images=total_images,
        posts=posts_with_images,
        alt_generation_enabled=altgen_active,
    )


# ---------- /api/apply ----------

@app.post("/api/apply", response_model=ApplyResponse)
def apply_alt_updates(req: ApplyRequest) -> ApplyResponse:
    if not req.updates:
        return ApplyResponse(updated=[])

    client = Client()
    try:
        client.login(req.handle, req.app_password)
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
            did, collection, rkey = parse_at_uri(uri)

            rec_resp = client.com.atproto.repo.get_record(
                repo=did,
                collection=collection,
                rkey=rkey,
            )

            record = getattr(rec_resp, "value", None)
            if record is None:
                if isinstance(rec_resp, dict) and "value" in rec_resp:
                    record = rec_resp["value"]
                else:
                    raise RuntimeError("Could not locate record value in response")

            embed = record.get("embed")
            if not embed:
                raise RuntimeError("Record has no embed")
            if embed.get("$type") != "app.bsky.embed.images":
                raise RuntimeError(
                    f"Embed type is not app.bsky.embed.images: {embed.get('$type')}"
                )

            images = embed.get("images") or []
            if not isinstance(images, list):
                raise RuntimeError("Record embed.images is not a list")

            # Apply updates
            for upd in updates:
                idx = upd.image_index
                if idx < 0 or idx >= len(images):
                    continue
                images[idx]["alt"] = upd.new_alt

            client.com.atproto.repo.put_record(
                repo=did,
                collection=collection,
                rkey=rkey,
                record=record,
            )

            # Record each update in SQLite as "applied"
            for upd in updates:
                db.record_image_update(
                    handle=req.handle,
                    uri=uri,
                    image_index=upd.image_index,
                    new_alt=upd.new_alt,
                    status="applied",
                )

            results.append(
                ApplyResultItem(
                    uri=uri,
                    success=True,
                    error=None,
                )
            )

        except Exception as e:
            # Record failed updates
            for upd in updates:
                db.record_image_update(
                    handle=req.handle,
                    uri=uri,
                    image_index=upd.image_index,
                    new_alt=upd.new_alt,
                    status="failed",
                )

            results.append(
                ApplyResultItem(
                    uri=uri,
                    success=False,
                    error=str(e),
                )
            )

    return ApplyResponse(updated=results)
