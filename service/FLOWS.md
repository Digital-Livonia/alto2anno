# Directus Flows: Annotator automation

The alto2anno service (this directory) only implements steps 2-3 of the
automation (download ALTO -> convert -> upload -> callback). Steps 1 and 4
are two Directus Flows, built directly via the Directus API (not stored in
this repo -- Flow/Operation definitions live in the `directus_flows` /
`directus_operations` tables). This file is the written reference for them,
since nothing else records what they contain.

Both flows currently exist on **dev** (`dev.db.dl.tlu.ee`) only, scoped to
the `magistraat` collection. Prod needs the same two flows built the same
way -- see the checklist at the bottom.

## Architecture

```
[user clicks button on a magistraat item]
        |
        v
Flow A "Annotator: run alto2anno"  (trigger: manual, location: item)
  1. read_record   (item-read)   -- reads alto_files
  2. build_payload (exec)        -- alto_files -> ordered alto_file_ids array
  3. send_request  (request)     -- POST http://<env>-alto2anno-service:8000/convert
                                     Authorization: Bearer ANNOTATOR_SHARED_SECRET
        |
        v  (synchronous; alto2anno-service does the actual work)
alto2anno-service /convert
  - downloads each ALTO file, runs alto2anno.py, uploads results
  - POSTs {collection, id, annotation_file_ids} to callback_url
    with Authorization: Bearer <callback_token>
        |
        v
Flow B "Annotator: receive conversion results"  (trigger: webhook)
  1. check_auth    (exec)        -- verifies $accountability.user is the
                                     "annotator" service account
  2. read_current  (item-read)   -- reads the record's current annotations
                                     junction row ids (to unlink)
  3. build_update  (exec)        -- {annotations: {create: [...new], delete: [...old]}}
  4. update_record (item-update) -- unlinks old files, links new ones
                                     (never deletes the old directus_files
                                     rows themselves -- see project safety
                                     note on shared dev/prod S3 storage)
  5. reindex_ocr   (request)     -- POST /iiif/parse-ocr so IIIF search
                                     stays in sync with the new annotations
                                     Authorization: Bearer <ocr indexer token>
```

## Token/secret inventory

Every token below is a **static Directus access token for a service
account**, generated in the Directus admin UI (user's profile page ->
Token field -> "Generate Token"), except `ANNOTATOR_SHARED_SECRET` which is
an arbitrary random string we generate ourselves. None of the actual
values are stored in this repo or in `kubernetes-conf` (see
`alto2anno-secrets.example.yaml` there) -- only this table of what exists
and where.

| Token | What it authenticates | Where it lives | Required Directus permissions |
|---|---|---|---|
| `ANNOTATOR_SHARED_SECRET` | Flow A -> alto2anno-service (`POST /convert`) | k8s Secret `<env->alto2anno-secrets`, key `annotatorSharedSecret`; same value hardcoded in Flow A's `send_request` operation header | None (not a Directus token, never sent to Directus) |
| `DIRECTUS_SERVICE_TOKEN` ("annotator" account) | alto2anno-service -> Directus (`GET /assets/:id`, `POST /files`) | k8s Secret `<env->alto2anno-secrets`, key `directusServiceToken` | `directus_files`: Read, Create |
| `callback_token` (same "annotator" account token, reused) | alto2anno-service -> Flow B's webhook trigger | Hardcoded in Flow A's `send_request` operation body (`callback_token` field) | Must be a genuine Directus token -- **not** an arbitrary secret. Directus's own auth middleware validates any `Authorization: Bearer` header globally, even on webhook trigger endpoints, and 401s the whole request before the flow even runs if the token doesn't authenticate. This is why the shared-secret approach used for `ANNOTATOR_SHARED_SECRET` doesn't work here. |
| ("annotator" account, continued) | Flow B's `check_auth` step | Hardcoded expected user id in the `check_auth` script (`data.$accountability.user === '<id>'`) | Just needs to exist as a real account; the check is identity, not a separate permission |
| "ocr indexer" account token | Flow B's `reindex_ocr` step -> `POST /iiif/parse-ocr` | Hardcoded in Flow B's `reindex_ocr` operation header | `IIIF_settings`: Read. The target collection (e.g. `magistraat`): Read. `ocr_entries`: full CRUD. (This account predates the Annotator work -- it's the one used for manual OCR indexing already documented in `directus-iiif-endpoint`'s README.) |
| Directus admin/flows-admin token | Only needed to *build or edit* these flows via the API (`directus_flows`, `directus_operations` CRUD) | Not stored anywhere in the running system -- used ad hoc, then discarded. Regenerate one to make further changes to the flows. | `directus_flows`, `directus_operations` (and `directus_panels` if editing visually): full CRUD |

**Why `/parse-ocr` needs its own explicit token at all:** the endpoint
(`src/index.js`) builds its internal `ItemsService` instances with
`accountability: req.accountability` -- i.e. it runs with whatever
permissions the *caller* has, not an elevated internal service context. An
unauthenticated call runs as the public role, which has no access to
`ocr_entries`/`IIIF_settings`, and fails with a `403`. This is the same gap
already flagged as a TODO in `directus-iiif-endpoint`'s README ("missing
auth token on POST /parse-ocr") -- discovered again here because Flow B's
`reindex_ocr` step originally had no `Authorization` header either (copied
from the pre-existing "Index annotations" flow's convention) and silently
did nothing as a result.

## Building these flows on a new environment (e.g. prod)

1. Create a Directus service account named "annotator" (or reuse dev's
   naming) with `directus_files` Read + Create. Generate its static token
   -> this is `DIRECTUS_SERVICE_TOKEN` for the k8s Secret, *and* the value
   used for `callback_token` in Flow A, *and* the `$accountability.user`
   id hardcoded into Flow B's `check_auth`.
2. Generate a random string for `ANNOTATOR_SHARED_SECRET` (e.g.
   `openssl rand -base64 32`) -> goes in the k8s Secret and in Flow A's
   request header.
3. Get the target environment's existing OCR-indexing service account
   token (or create one the same way, granting `IIIF_settings` Read + the
   collection's Read + `ocr_entries` CRUD) -> used in Flow B's
   `reindex_ocr` header.
4. Build Flow A and Flow B via the Directus API as described above,
   substituting the new environment's hostnames/tokens/account ids.
   (No export/import shortcut exists yet -- these were hand-built through
   the API operation by operation. Worth revisiting as a Directus flow
   *export* if a third environment is ever added.)
5. Verify end-to-end exactly like dev was verified: trigger Flow A on a
   real record, confirm the manifest's `canvas.annotations` now points at
   a working `/iiif/annotation-page/:id`, confirm `/iiif/search/...`
   returns results for text known to be on the page.
