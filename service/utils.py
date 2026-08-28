"""Small standalone helpers with no HTTP/subprocess side effects."""
from __future__ import annotations

import re
from pathlib import Path

_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


def filename_from_content_disposition(header_value: str | None) -> str | None:
    """Best-effort extraction of the original filename from a
    Content-Disposition response header. Directus's GET /assets/:id doesn't
    reliably set this for every storage driver/version, so this returns
    None when it can't find one and the caller must fall back.
    """
    if not header_value:
        return None
    match = _FILENAME_RE.search(header_value)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def derive_filename(index: int, file_id: str, content_disposition: str | None) -> str:
    """Build the on-disk filename for one downloaded ALTO file.

    Design note: alto2anno.py assigns each page's canvas index by
    alphabetically sorting the *.xml filenames in its working directory
    (see process_directory() in alto2anno.py), and the caller's
    `alto_file_ids` list is assumed to already be in canvas/page order.
    Neither Directus's original filename (when recoverable) nor a bare
    file_id sorts reliably that way (e.g. "page10" < "page2"; UUIDs sort
    essentially at random), so every file is prefixed with its zero-padded
    request position to force the correct alphabetical order regardless of
    the recovered name.
    """
    original = filename_from_content_disposition(content_disposition)
    base = Path(original).name if original else f"{file_id}.xml"
    if not base.lower().endswith(".xml"):
        base += ".xml"
    return f"{index:04d}_{base}"
