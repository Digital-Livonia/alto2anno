# alto2anno.py

Convert ALTO XML files to IIIF annotation lists using an XSLT stylesheet.

## Requirements
- Python 3.8+
- `xsltproc` available on PATH (libxslt). On Windows install via Chocolatey `choco install libxslt` or Scoop `scoop install libxslt` or follow the guide on https://runestone.academy/ns/books/published/pretextguide/installing-xsltproc.html
- XSL files: `annotationListNoArt.xsl` (default) or `alto2annosv3.xsl` in this folder.

## Usage
Non-interactive (all parameters passed):
```bash
python alto2anno.py \
  -d /path/to/xmls \
  -x ./annotationListNoArt.xsl \
  -m https://db.dl.tlu.ee/iiif/manifest/magistraat/47 \
  --xratio 1 \
  --yratio 1
```

Interactive (press Enter to accept defaults):
```bash
python alto2anno.py
```

## Parameters
- `-d`, `--directory` – directory containing ALTO `.xml` files (JSON written beside each XML).
- `-x`, `--xsl` – XSL stylesheet to apply.
- `-m`, `--manifest` – manifest URI used inside produced annotations.
- `--xratio`, `--yratio` – ratio parameters forwarded to the XSLT (default `1`).

## Output
For each `*.xml` file, a matching `*.json` annotation file is created in the same directory. Canvas URI is derived from the sequence index in alphabetical order.

## HTTP service

`service/` contains a FastAPI wrapper that lets a Directus Flow trigger this
conversion over HTTP (download ALTO files from Directus, run this CLI,
upload the resulting annotation files back to Directus, then call back a
webhook with the new file ids). This CLI itself is unchanged and still used
as-is, invoked as a subprocess. See [`service/README.md`](service/README.md)
for the endpoint contract, required environment variables, and how to run it
locally or via Docker.
