"""Small standalone helpers with no HTTP/subprocess side effects."""
from __future__ import annotations

import re
from pathlib import Path

_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)
_INDEX_PREFIX_RE = re.compile(r'^\d{4}_')


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


def strip_index_prefix(stem: str) -> str:
    """Reverse the zero-padded index prefix derive_filename() adds.

    Found via a real bug: this project's ALTO/image filenames already
    carry their own "NNNN_" ordering prefix (e.g. "0001_001.xml" /
    "0001_001.jpg"), so prepending our own index on top produced
    "0001_0001_001.json" for the converted output -- which no longer
    matched the canvas image's filename stem ("0001_001"), silently
    breaking the manifest's annotation-to-canvas matching in
    directus-iiif-endpoint's getAnnotations() (it looks for an exact
    `${stem}.json`). The uploaded output filename must be built from this
    stripped stem, not the prefixed one used to drive alto2anno.py's sort.
    """
    return _INDEX_PREFIX_RE.sub('', stem, count=1)
