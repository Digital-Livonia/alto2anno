"""Environment-based configuration for the alto2anno HTTP service.

All required values are validated eagerly at import time (see `settings`
below) so a misconfigured deployment fails immediately at process startup
with a clear message, rather than on the first incoming request.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# service/config.py -> service/ -> repo root. Used to resolve alto2anno.py
# and the XSL stylesheet regardless of the process's current working
# directory (e.g. when run as `uvicorn service.main:app` from anywhere).
REPO_ROOT = Path(__file__).resolve().parent.parent


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "See service/README.md for the full list of required configuration."
        )
    return value


@dataclass(frozen=True)
class Settings:
    annotator_shared_secret: str
    directus_url: str
    directus_service_token: str
    alto2anno_script: Path
    xsl_path: Path
    python_executable: str
    http_timeout_seconds: float


def load_settings() -> Settings:
    directus_url = _require_env("DIRECTUS_URL").rstrip("/")

    xsl_path = Path(
        os.environ.get("ALTO2ANNO_XSL_PATH", str(REPO_ROOT / "annotationListNoArt.xsl"))
    ).resolve()
    script_path = Path(
        os.environ.get("ALTO2ANNO_SCRIPT_PATH", str(REPO_ROOT / "alto2anno.py"))
    ).resolve()

    if not xsl_path.is_file():
        raise RuntimeError(f"XSL stylesheet not found at '{xsl_path}'.")
    if not script_path.is_file():
        raise RuntimeError(f"alto2anno.py script not found at '{script_path}'.")

    return Settings(
        annotator_shared_secret=_require_env("ANNOTATOR_SHARED_SECRET"),
        directus_url=directus_url,
        directus_service_token=_require_env("DIRECTUS_SERVICE_TOKEN"),
        alto2anno_script=script_path,
        xsl_path=xsl_path,
        python_executable=os.environ.get("PYTHON_EXECUTABLE", sys.executable),
        http_timeout_seconds=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "60")),
    )


settings = load_settings()
