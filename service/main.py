"""FastAPI service wrapping alto2anno.py for use from a Directus Flow.

Endpoint contract, required environment variables, and local/Docker run
instructions are documented in service/README.md.

Processing model: /convert runs the whole download -> convert -> upload ->
callback pipeline synchronously within the request and only returns once
it's done (or failed). This keeps the HTTP status code of the /convert
response itself meaningful for downstream failures (502) as opposed to a
fire-and-forget 202 that would leave errors only reachable via a callback
that itself carries no defined error shape. The tradeoff is that the caller
(the Directus Flow) must allow a long enough request timeout to cover
downloading N files, running xsltproc N times, and uploading N files.
"""
from __future__ import annotations

import logging
import secrets
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .converter import ConversionError, run_alto2anno
from .directus import fetch_asset, upload_file
from .utils import derive_filename, strip_index_prefix

logger = logging.getLogger("alto2anno.service")

app = FastAPI(title="alto2anno service", version="1.0.0")


def _shared_secret_is_valid(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.removeprefix("Bearer ").strip()
    return secrets.compare_digest(token, settings.annotator_shared_secret)


@app.middleware("http")
async def _require_shared_secret(request: Request, call_next):
    # Enforced as middleware (ahead of routing/body parsing) rather than as
    # in-handler logic, specifically so an unauthenticated request is always
    # rejected with 401 -- even one with a malformed body, which would
    # otherwise trip Pydantic's 400 validation-error path first and leak
    # request-schema details to a caller that never proved it was allowed
    # to ask.
    if request.url.path == "/convert":
        if not _shared_secret_is_valid(request.headers.get("authorization")):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid Authorization header."})
    return await call_next(request)


class ConvertRequest(BaseModel):
    collection: str = Field(..., min_length=1)
    id: str = Field(..., min_length=1)
    alto_file_ids: list[str] = Field(..., min_length=1)
    manifest_uri: str = Field(..., min_length=1)
    callback_url: str = Field(..., min_length=1)
    callback_token: str = Field(..., min_length=1)
    # Forwarded as-is to alto2anno.py; "1"/"1" matches the CLI's own default.
    xratio: str = "1"
    yratio: str = "1"

    @field_validator("manifest_uri", "callback_url")
    @classmethod
    def _must_be_http_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("must be an absolute http:// or https:// URL")
        return value


class ConvertResponse(BaseModel):
    collection: str
    id: str
    annotation_file_ids: list[str]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default is 422; the spec for this service calls for 400 on bad input.
    #
    # jsonable_encoder (not exc.errors() directly) matters here: a
    # field_validator failure (e.g. _must_be_http_url below) puts the raised
    # ValueError instance itself under errors()[i]["ctx"]["error"], which
    # isn't JSON-serializable -- passing it straight to JSONResponse blew up
    # while rendering the response body and produced a 500 for exactly the
    # bad input this handler exists to turn into a clean 400.
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/convert", response_model=ConvertResponse)
async def convert(payload: ConvertRequest) -> ConvertResponse:
    async with httpx.AsyncClient() as client:
        with tempfile.TemporaryDirectory(prefix="alto2anno-") as tmp:
            tmp_path = Path(tmp)

            # Downloaded in request order; filenames are prefixed with their
            # position so alto2anno.py's alphabetical sort preserves that
            # order when assigning canvas indices. See utils.derive_filename.
            input_stems: list[str] = []
            for index, file_id in enumerate(payload.alto_file_ids, start=1):
                try:
                    response = await fetch_asset(client, file_id)
                except httpx.HTTPError as exc:
                    logger.warning("Failed to download ALTO asset %s: %s", file_id, exc)
                    raise HTTPException(
                        status_code=502,
                        detail=f"Failed to download ALTO file '{file_id}' from Directus.",
                    ) from exc

                filename = derive_filename(index, file_id, response.headers.get("content-disposition"))
                (tmp_path / filename).write_bytes(response.content)
                input_stems.append(Path(filename).stem)

            try:
                await run_alto2anno(tmp_path, payload.manifest_uri, payload.xratio, payload.yratio)
            except ConversionError as exc:
                logger.error("alto2anno.py failed: %s", exc)
                raise HTTPException(status_code=502, detail="ALTO to annotation conversion failed.") from exc

            # alto2anno.py swallows per-file xsltproc errors internally and
            # always exits 0 for the batch (see converter.py docstring), so a
            # clean subprocess exit does not guarantee every input produced
            # an output file -- check explicitly.
            missing = [stem for stem in input_stems if not (tmp_path / f"{stem}.json").is_file()]
            if missing:
                logger.error("alto2anno.py did not produce output for %d/%d file(s)", len(missing), len(input_stems))
                raise HTTPException(
                    status_code=502,
                    detail=f"Conversion did not produce output for {len(missing)} of {len(input_stems)} file(s).",
                )

            # Fail-fast default: if any single file fails to upload, the
            # whole request fails rather than returning a partial
            # annotation_file_ids list. Simpler and safer for a caller that
            # doesn't (yet) know how to reconcile a partial result against
            # the manifest's full canvas sequence. Note this does leave any
            # already-uploaded files sitting in Directus, unreferenced.
            annotation_file_ids: list[str] = []
            for stem in input_stems:
                json_path = tmp_path / f"{stem}.json"
                # Upload under the original (un-prefixed) name -- the index
                # prefix only exists to force alto2anno.py's alphabetical
                # sort order and must not leak into the stored filename,
                # since directus-iiif-endpoint's getAnnotations() matches
                # annotation files to canvases by exact filename stem.
                upload_filename = f"{strip_index_prefix(stem)}.json"
                try:
                    file_id = await upload_file(client, json_path, upload_filename=upload_filename)
                except httpx.HTTPError as exc:
                    logger.error("Failed to upload %s to Directus: %s", json_path.name, exc)
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "Failed to upload converted annotation file(s) to Directus "
                            f"({len(annotation_file_ids)} of {len(input_stems)} were uploaded "
                            "before the failure and remain in Directus, unreferenced)."
                        ),
                    ) from exc
                annotation_file_ids.append(file_id)

            callback_body = {
                "collection": payload.collection,
                "id": payload.id,
                "annotation_file_ids": annotation_file_ids,
            }
            try:
                cb_response = await client.post(
                    payload.callback_url,
                    json=callback_body,
                    headers={"Authorization": f"Bearer {payload.callback_token}"},
                    timeout=settings.http_timeout_seconds,
                )
                cb_response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("Callback to %s failed: %s", payload.callback_url, exc)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Annotation files were converted and uploaded to Directus, but delivering "
                        "the callback failed. The uploaded files are not referenced anywhere yet."
                    ),
                ) from exc

    return ConvertResponse(
        collection=payload.collection, id=payload.id, annotation_file_ids=annotation_file_ids
    )
