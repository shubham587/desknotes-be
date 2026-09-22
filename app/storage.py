"""Media storage — the swap point for where captured photos/diagrams live.

`upload_media(name, data, content_type) -> public URL`. Uploads to Supabase
Storage when configured (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY); otherwise
falls back to local disk under MEDIA_DIR, served at /media (dev / offline use,
or hosts without Storage set up). Callers never care which one is active.
"""

from __future__ import annotations

import uuid

import httpx

from . import config


def _upload_to_supabase(name: str, data: bytes, content_type: str) -> str:
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_STORAGE_BUCKET}/{name}"
    resp = httpx.put(
        url,
        content=data,
        headers={
            "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_STORAGE_BUCKET}/{name}"


def _upload_to_disk(name: str, data: bytes) -> str:
    (config.MEDIA_DIR / name).write_bytes(data)
    return f"/media/{name}"


def upload_media(data: bytes, content_type: str, suffix: str = "") -> str:
    """Store an image, return its public URL (absolute if Storage; /media/... if
    local disk). `suffix` is appended to the generated name (e.g. an original
    filename) purely for readability."""
    name = f"{uuid.uuid4().hex}{suffix}"
    if config.STORAGE_ENABLED:
        return _upload_to_supabase(name, data, content_type)
    return _upload_to_disk(name, data)


def fetch_media(url: str) -> bytes:
    """Read bytes back from a stored media URL — a full URL (Supabase Storage,
    fetched over HTTP) or a local /media/... path (read straight off disk)."""
    if url.startswith("http"):
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    name = url.rsplit("/", 1)[-1]  # avoid path traversal
    path = config.MEDIA_DIR / name
    if not path.exists():
        raise FileNotFoundError(url)
    return path.read_bytes()
