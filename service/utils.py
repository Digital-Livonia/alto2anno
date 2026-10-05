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

    Design note: the prefix keeps the on-disk names unique and, as the
    fallback when the manifest is unusable, makes alto2anno.py's alphabetical
    sort follow the request order (canvas number = position in the request).
    Normally the canvas number comes from the manifest instead (see
    plan_canvas_indices), so the prefix no longer decides it. A bare file_id
    or Directus's original name does not sort reliably either ("page10" <
    "page2"; UUIDs sort essentially at random), hence a zero-padded position.
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


def canvas_stem(filename: str) -> str:
    """Filename stem the way directus-iiif-endpoint computes it: everything
    before the *first* dot of the bare filename (`filename_download.split('.')[0]`).
    That is the rule it uses to attach an annotation file to a canvas image,
    so ALTO files must be matched to canvases with exactly the same rule.
    """
    return Path(filename).name.split(".")[0]


def plan_canvas_indices(
    alto_stems: list[str | None], manifest: object
) -> tuple[list[int] | None, list[str]]:
    """Canvas index (1-based) for each ALTO file, taken from the manifest.

    Why: alto2anno.py numbers canvases by file position, and the caller's
    `alto_file_ids` is not guaranteed to be in page order (the Flow reads the
    alto_files junction, typically in upload order). The page's real number is
    its position among the manifest's canvases, found by matching the ALTO
    filename stem to a canvas `filename` stem.

    `alto_stems` holds one entry per input file in request order (None when
    the original filename could not be recovered). Returns `(indices, warnings)`:

    * `indices[i]` is the canvas index for input `i`. A stem with no canvas
      gets an index past the last canvas (such a file can never attach to a
      canvas anyway) and is reported in `warnings`. A stem on several canvases
      uses the first one; a stem requested twice maps both to the same canvas;
      both are warned about.
    * `indices` is None when the manifest has no usable canvas filenames; the
      caller then keeps the legacy request order (first warning says so).
    """
    items = manifest.get("items") if isinstance(manifest, dict) else None
    index_by_stem: dict[str, int] = {}
    ambiguous: set[str] = set()
    canvas_count = 0
    if isinstance(items, list):
        canvas_count = len(items)
        for position, canvas in enumerate(items, start=1):
            filename = canvas.get("filename") if isinstance(canvas, dict) else None
            if not isinstance(filename, str) or not filename:
                continue
            stem = canvas_stem(filename)
            if stem in index_by_stem:
                ambiguous.add(stem)
            else:
                index_by_stem[stem] = position

    if not index_by_stem:
        return None, ["Manifest has no canvas filenames; using request order for canvas numbers."]

    warnings: list[str] = []
    indices: list[int] = []
    unmatched: list[str] = []
    next_free = canvas_count
    for stem in alto_stems:
        if stem is not None and stem in index_by_stem:
            indices.append(index_by_stem[stem])
        else:
            next_free += 1
            indices.append(next_free)
            unmatched.append(stem if stem is not None else "(unknown filename)")

    if unmatched:
        warnings.append(
            "No canvas with a matching filename for: "
            + ", ".join(unmatched)
            + ". Their annotations will not attach to any canvas."
        )
    shared = sorted({s for s in alto_stems if s in ambiguous})
    if shared:
        warnings.append("Several canvases share the filename stem: " + ", ".join(shared) + " (first one used).")
    repeated = sorted({s for s in alto_stems if s is not None and alto_stems.count(s) > 1})
    if repeated:
        warnings.append("The same ALTO filename stem was sent more than once: " + ", ".join(repeated) + ".")
    return indices, warnings
