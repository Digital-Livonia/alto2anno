# alto2anno HTTP service

A small FastAPI wrapper around `alto2anno.py` (unmodified, called via
subprocess) so a Directus Flow can trigger ALTO -> IIIF annotation
conversion over HTTP instead of running the CLI by hand.

Flow this service implements (steps 2-3 of the larger automation; step 1,
the Directus Flow button, and step 4, attaching the resulting files to a
record, live elsewhere and are out of scope here):

1. Receive `POST /convert` from a Directus Flow.
2. Download each listed ALTO file from Directus (`GET /assets/:id`).
3. Run `alto2anno.py` against them to produce annotation JSON files.
4. Upload each JSON file back to Directus (`POST /files`).
5. `POST` the resulting file ids to the caller-supplied `callback_url`.

## `POST /convert`

### Auth

`Authorization: Bearer <ANNOTATOR_SHARED_SECRET>` — a secret shared between
this service and whichever Flow calls it. Missing or wrong -> `401`.

### Request body

```json
{
  "collection": "magistraat",
  "id": "47",
  "alto_file_ids": ["uuid1", "uuid2"],
  "manifest_uri": "https://db.dl.tlu.ee/iiif/manifest/magistraat/47",
  "callback_url": "https://db.dl.tlu.ee/some/webhook",
  "callback_token": "..."
}
```

- `alto_file_ids` is **ordered** — it must list the pages in the same order
  they appear on the manifest's canvas sequence. `alto2anno.py` assigns each
  page's canvas index by alphabetically sorting filenames in its working
  directory, so this service downloads files and renames them with a
  zero-padded prefix (`0001_...`, `0002_...`, ...) matching their position
  in this list, to force that ordering regardless of each file's original
  name in Directus. See `derive_filename` in `service/utils.py`.
- `xratio` / `yratio` (optional, default `"1"`) — forwarded to `alto2anno.py`
  as-is.

### Response

On success, `200`:

```json
{
  "collection": "magistraat",
  "id": "47",
  "annotation_file_ids": ["new-uuid1", "new-uuid2"]
}
```

This is also the exact body POSTed to `callback_url` (with
`Authorization: Bearer <callback_token>`) before this response is returned
— see "Processing model" below.

### Error responses

- `400` — malformed/missing request fields (e.g. empty `alto_file_ids`, a
  `callback_url` that isn't an absolute `http(s)` URL).
- `401` — missing/invalid `ANNOTATOR_SHARED_SECRET`.
- `502` — a downstream step failed: fetching an ALTO file from Directus,
  running `alto2anno.py` (including `xsltproc` missing from `PATH`),
  uploading a converted file, or delivering the callback.

**Fail-fast behavior:** if any single ALTO file fails to download or
convert, or any single converted file fails to upload, the *entire request*
fails (no partial `annotation_file_ids` is returned or uploaded-and-ignored
mid-batch). This is the simplest and safest default given a caller has no
defined way to reconcile a partial annotation set against a manifest's full
canvas sequence. One caveat: if an upload succeeds for some files and a
*later* one fails, or if the final callback delivery itself fails, the
already-uploaded files remain in Directus, unreferenced by anything — this
service does not attempt to roll them back. Worth a second look if orphaned
`directus_files` rows become a problem in practice.

### Processing model

`/convert` runs the whole pipeline synchronously and only responds once
it's fully done (or has failed) — it does not return an immediate `202`
and report results solely via the callback. This keeps the HTTP status of
the `/convert` response itself meaningful for downstream failures (`502`),
rather than requiring a separate error-reporting shape for a background
job. The tradeoff: the calling Flow's HTTP client must allow a request
timeout long enough to cover downloading, converting, and uploading every
file in the batch.

## Required environment variables

| Variable | Required | Description |
|---|---|---|
| `ANNOTATOR_SHARED_SECRET` | yes | Bearer token this service requires on incoming `/convert` requests. |
| `DIRECTUS_URL` | yes | Base URL of the Directus instance, no trailing slash (e.g. `https://db.dl.tlu.ee`). |
| `DIRECTUS_SERVICE_TOKEN` | yes | Bearer token for a Directus service account, used for `GET /assets/:id` and `POST /files`. |
| `ALTO2ANNO_XSL_PATH` | no | Overrides the XSL stylesheet path. Defaults to `annotationListNoArt.xsl` next to `alto2anno.py` in the repo root. |
| `ALTO2ANNO_SCRIPT_PATH` | no | Overrides the path to `alto2anno.py`. Defaults to the copy in the repo root. |
| `PYTHON_EXECUTABLE` | no | Interpreter used to invoke `alto2anno.py`. Defaults to the interpreter running this service. |
| `HTTP_TIMEOUT_SECONDS` | no | Timeout (seconds) for each outbound HTTP call to Directus / the callback URL. Defaults to `60`. |

Missing required variables raise a `RuntimeError` at process startup
(`service/config.py`), so misconfiguration fails immediately rather than on
the first request.

`callback_token` is per-request, supplied by the caller in the `/convert`
body — it is not an environment variable, and is distinct from
`ANNOTATOR_SHARED_SECRET`.

## Running locally

```bash
cd alto2anno
pip install -r service/requirements.txt
# plus xsltproc on PATH, e.g. `brew install libxslt` / `apt-get install xsltproc`

export ANNOTATOR_SHARED_SECRET=dev-secret
export DIRECTUS_URL=https://db.dl.tlu.ee
export DIRECTUS_SERVICE_TOKEN=...

uvicorn service.main:app --reload
```

## Running via Docker

```bash
cd alto2anno
docker build -f service/Dockerfile -t alto2anno-service .
docker run --rm -p 8000:8000 \
  -e ANNOTATOR_SHARED_SECRET=dev-secret \
  -e DIRECTUS_URL=https://db.dl.tlu.ee \
  -e DIRECTUS_SERVICE_TOKEN=... \
  alto2anno-service
```

(Build context is the repo root, not `service/`, since the image also needs
`alto2anno.py` and the `.xsl` files from the repo root.)

## Deploying to Kubernetes

Runs as its own standalone container, separate from Directus (different
runtime — Python/FastAPI vs. Directus's Node.js extensions — plus a system
binary dependency on `xsltproc` that has no reason to live inside the
Directus image).

- **Image**: built and pushed to `ghcr.io/digital-livonia/alto2anno:latest`
  by `.github/workflows/build.yml` on every push to `main` that touches
  `service/`, `alto2anno.py`, or the `.xsl` files.
- **Manifests**: `alto2anno-deployment.yaml` / `alto2anno-service.yaml` (prod)
  and `dev-alto2anno-deployment.yaml` / `dev-alto2anno-service.yaml` (dev)
  live in the `kubernetes-conf` repo, alongside the rest of the cluster's
  config. Two separate Deployments, not one shared instance: dev and prod
  are separate Directus instances/databases, so each needs its own
  `DIRECTUS_SERVICE_TOKEN` (and its own `ANNOTATOR_SHARED_SECRET`).
- **No Ingress**: only a Directus Flow's "Request URL" operation calls
  `/convert`, and that request runs server-side from inside the Directus
  pod — already in-cluster. So each Deployment gets only a `ClusterIP`
  Service, reachable at `alto2anno-service.dl-tlu-ee.svc.cluster.local:8000`
  (prod) / `dev-alto2anno-service.dl-tlu-ee.svc.cluster.local:8000` (dev).
  No public hostname is needed; this also keeps `/convert` off the public
  internet entirely rather than relying solely on the shared-secret check.
- **Secrets**: `alto2anno-secrets` / `dev-alto2anno-secrets`, holding
  `annotatorSharedSecret` and `directusServiceToken`. Created directly in
  the cluster (not tracked in git, same as `tu-s3-credentials`) — see
  `alto2anno-secrets.example.yaml` in `kubernetes-conf` for the exact
  `kubectl create secret` commands.
- **No PVC**: the service only ever touches a per-request
  `tempfile.TemporaryDirectory`; nothing persists between requests.

The Directus Flow's webhook URL for each environment is then
`http://alto2anno-service.dl-tlu-ee.svc.cluster.local:8000/convert` (prod) /
`http://dev-alto2anno-service.dl-tlu-ee.svc.cluster.local:8000/convert`
(dev).

## Known caveat inherited from `alto2anno.py` (not fixed here)

`alto2anno.py` hardcodes `https://db.dl.tlu.ee/...` for the `annoURI` and
`canvasURI` XSLT parameters regardless of the `manifest_uri` passed in
(see `process_directory` in `alto2anno.py`) — so annotation/canvas URIs
inside the generated JSON always point at the production host, even when
converting for a different environment. This service does not work around
it, per instructions to treat `alto2anno.py` as a stable, unmodified
dependency; whoever consumes the generated files (e.g. the existing
"rewrite ocr_entries canvas/manifest URLs to the current environment" fix
elsewhere in this project) needs to be aware of it.
