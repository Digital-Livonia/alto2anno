"""Shared pytest fixtures for the alto2anno service test suite.

`service/config.py` builds its `settings` singleton at *import* time by
reading `os.environ` (see `load_settings()` there), so importing
`service.main`/`service.config` before the required env vars are present
either raises `RuntimeError` (see test_config.py, which exercises that
directly) or, once imported once, keeps returning the module already
cached in `sys.modules` regardless of later env changes. To get a clean,
predictable import for every test we set the required environment
variables *before* importing anything under `service.*`, then import
inside a fixture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

REQUIRED_ENV = {
    "ANNOTATOR_SHARED_SECRET": "test-shared-secret",
    "DIRECTUS_URL": "https://directus.example.test",
    "DIRECTUS_SERVICE_TOKEN": "test-directus-token",
}


def _purge_service_modules() -> None:
    for name in list(sys.modules):
        if name == "service" or name.startswith("service."):
            del sys.modules[name]


@pytest.fixture()
def service_env(monkeypatch):
    """Set required env vars and return a fresh, importable `service.main`."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    _purge_service_modules()
    import service.main as main_module  # noqa: PLC0415

    yield main_module
    _purge_service_modules()


@pytest.fixture()
def app(service_env):
    return service_env.app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers():
    return {"Authorization": f"Bearer {REQUIRED_ENV['ANNOTATOR_SHARED_SECRET']}"}


@pytest.fixture()
def valid_payload():
    return {
        "collection": "magistraat",
        "id": "47",
        "alto_file_ids": ["file-aaa", "file-bbb"],
        "manifest_uri": "https://db.dl.tlu.ee/iiif/manifest/magistraat/47",
        "callback_url": "https://caller.example.test/webhook",
        "callback_token": "callback-secret",
    }
