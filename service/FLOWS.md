# Directus Flows: Annotator automation

The alto2anno service (this directory) only implements steps 2-3 of the
automation (download ALTO -> convert -> upload -> callback). Steps 1 and 4
are two Directus Flows, built directly via the Directus API (not stored in
this repo -- Flow/Operation definitions live in the `directus_flows` /
`directus_operations` tables). This file is the written reference for them,
since nothing else records what they contain.

Both flows exist on **dev** (`dev.db.dl.tlu.ee`) and **prod** (`db.dl.tlu.ee`),
scoped to the `magistraat` collection. Each environment has its own pair of
flows, its own `alto2anno-secrets` (see `kubernetes-conf`), and its own
service-account tokens baked into its own flows -- see the checklist at the
bottom for what building this on a *third* environment would require.

**Known caveat found while rolling out prod:** prod's currently deployed
build of `directus-iiif-endpoint` predates the `GET /iiif/annotation-page/:id`
route (introduced there in 1.0.11) -- `canvas.annotations[].id` in prod
manifests still points straight at `GET /assets/:id.json` instead. This
doesn't block the Annotator flows here (OCR search indexing works
independently, and alto2anno.py happens to hardcode `db.dl.tlu.ee` as its
URI base regardless of `manifest_uri`, which is coincidentally prod's own
real domain -- so the raw asset's `on`/`within` URLs are still correct on
prod specifically). It does mean prod doesn't get the annotation-page
origin-rewriting fixes from `directus-iiif-endpoint` 1.0.9-1.0.12 until that
extension itself is redeployed there -- a separate, not-yet-scheduled piece
of work, tracked in `directus-iiif-endpoint`'s own README/changelog, not
here.

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
| "ocr indexer" account token | Flow B's `reindex_ocr` step -> `POST /iiif/parse-ocr` | Hardcoded in Flow B's `reindex_ocr` operation header | See exact permission list below -- more than it looks like at first, and dev's account already had all of it while prod's didn't, which is what made this worth spelling out precisely. (This account predates the Annotator work -- it's the one used for manual OCR indexing already documented in `directus-iiif-endpoint`'s README.) |
| Directus admin/flows-admin token | Only needed to *build or edit* these flows via the API (`directus_flows`, `directus_operations` CRUD) | Not stored anywhere in the running system -- used ad hoc, then discarded. Regenerate one to make further changes to the flows. | `directus_flows`, `directus_operations` (and `directus_panels` if editing visually): full CRUD |

**Exact permissions the "ocr indexer" account's policy needs**, found by
rolling prod out and hitting each missing one in turn (`/parse-ocr`'s
`req.accountability`-based checks fail with a plain `403`/`404` per
missing piece, not one combined error, so this took a few rounds to nail
down):

- `IIIF_settings`: **Read** -- to look up the collection's configured
  `annotation_files` field name.
- The target collection itself (e.g. `magistraat`): **Read** -- to read
  the record and its linked annotation files.
- **The M2M junction collection backing that field**, e.g.
  `magistraat_files_annotations` for `magistraat.annotations`: **Read**.
  This one is easy to miss -- Directus requires read access on the
  junction collection itself to resolve a nested relation field
  (`annotations.directus_files_id`), even though nothing ever queries the
  junction collection by name directly. Find the right junction name via
  `GET /relations?filter[related_collection][_eq]=<collection>` with an
  admin-scoped token.
- `directus_files`: **Read** -- to fetch each linked annotation file's
  content.
- `ocr_entries`: full **CRUD** -- to replace the old entries with newly
  parsed ones.

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
   token, or create one the same way -> used in Flow B's `reindex_ocr`
   header. Either way, verify its policy actually has **all five**
   permissions listed above, not just `ocr_entries` CRUD -- don't assume
   an existing account has them just because OCR search already works on
   that environment (prod's account was missing three of the five despite
   OCR search having worked there before, apparently indexed some other
   way originally).
4. Build Flow A and Flow B via the Directus API as described above,
   substituting the new environment's hostnames/tokens/account ids.
   (No export/import shortcut exists yet -- these were hand-built through
   the API operation by operation. Worth revisiting as a Directus flow
   *export* if a third environment is ever added.)
5. Verify end-to-end: trigger Flow A on a real record, confirm
   `/iiif/search/<collection>/<id>?q=...` returns results for text known
   to be on the page (this alone proves the whole chain -- convert,
   upload, link, reindex -- ran). Also open the manifest and check
   `canvas.annotations[].id`: on an environment running
   `directus-iiif-endpoint` >= 1.0.11 it should be
   `.../iiif/annotation-page/<id>`; on an older deployment it'll still be
   a raw `.../assets/<id>.json` link (see the prod caveat above) -- that's
   a separate, pre-existing gap, not a sign the Annotator flows are wired
   wrong.
