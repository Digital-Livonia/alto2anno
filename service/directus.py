"""Thin HTTP helpers for the two Directus endpoints this service calls:
downloading a source ALTO asset and uploading a converted annotation file.

Kept deliberately dumb (no retries/business logic) so that error handling
and filename decisions stay visible in main.py.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from .config import settings


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.directus_service_token}"}


async def fetch_asset(client: httpx.AsyncClient, file_id: str) -> httpx.Response:
    """GET a file asset from Directus. Raises httpx.HTTPStatusError on non-2xx."""
    url = f"{settings.directus_url}/assets/{file_id}"
    response = await client.get(url, headers=_auth_headers(), timeout=settings.http_timeout_seconds)
    response.raise_for_status()
    return response


async def upload_file(client: httpx.AsyncClient, file_path: Path, upload_filename: str | None = None) -> str:
    """POST a local file to Directus's /files endpoint (multipart/form-data).

    This single call both stores the file on Directus's configured storage
    and creates the corresponding directus_files record, which is why it's
    used here instead of writing to shared storage directly.

    `upload_filename` overrides the filename Directus stores (defaults to
    the local file's own name) -- needed because the on-disk name may carry
    an internal ordering prefix that shouldn't leak into the uploaded
    file's name. See utils.strip_index_prefix().

    Returns the new file's id.
    """
    url = f"{settings.directus_url}/files"
    name = upload_filename or file_path.name
    with open(file_path, "rb") as fh:
        files = {"file": (name, fh, "application/json")}
        response = await client.post(
            url, headers=_auth_headers(), files=files, timeout=settings.http_timeout_seconds
        )
    response.raise_for_status()
    return response.json()["data"]["id"]


async def fetch_manifest(client: httpx.AsyncClient, manifest_uri: str) -> dict | None:
    """GET the item's IIIF manifest (public, no auth). Returns the parsed JSON
    object, or None on any network/HTTP/JSON problem or a non-object body.

    Best effort on purpose: the manifest only supplies the canvas order for the
    annotation files; if it is unreachable the caller falls back to the
    request order (and reports that) instead of failing the whole conversion.
    """
    try:
        response = await client.get(manifest_uri, timeout=settings.http_timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None
