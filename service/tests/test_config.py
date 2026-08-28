"""Tests for service/config.py's eager env validation.

`service.config` builds a module-level `settings` singleton by calling
`load_settings()` at import time. That makes the already-imported
`service.config.settings` untestable for the "missing env var" case without
re-executing a process, but `load_settings()` itself is a plain function we
can call directly with a patched `os.environ`, which is what the task
description points at. We purge `service.config` from `sys.modules` first
so each test gets a genuinely fresh module (avoiding any state bleed from
other tests that imported `service.main`).
"""
from __future__ import annotations

import importlib
import sys

import pytest

REQUIRED = {
    "ANNOTATOR_SHARED_SECRET": "s",
    "DIRECTUS_URL": "https://directus.example.test",
    "DIRECTUS_SERVICE_TOKEN": "t",
}


def _fresh_config_module():
    sys.modules.pop("service.config", None)
    return importlib.import_module("service.config")


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ALTO2ANNO_XSL_PATH", raising=False)
    monkeypatch.delenv("ALTO2ANNO_SCRIPT_PATH", raising=False)
    yield
    sys.modules.pop("service.config", None)


@pytest.mark.parametrize("missing", ["DIRECTUS_URL", "ANNOTATOR_SHARED_SECRET", "DIRECTUS_SERVICE_TOKEN"])
def test_load_settings_raises_when_required_var_missing(monkeypatch, missing):
    # Note: service/config.py calls `load_settings()` at *module* import
    # time (`settings = load_settings()`), so the RuntimeError fires during
    # `_fresh_config_module()` itself -- there's no already-imported module
    # to call `.load_settings()` on again afterwards.
    for key, value in REQUIRED.items():
        if key != missing:
            monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match=missing):
        _fresh_config_module()


def test_load_settings_succeeds_with_all_required_vars(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    config = _fresh_config_module()
    settings = config.load_settings()
    assert settings.annotator_shared_secret == "s"
    assert settings.directus_url == "https://directus.example.test"
    assert settings.directus_service_token == "t"
    assert settings.http_timeout_seconds == 60.0
    assert settings.xsl_path.is_file()
    assert settings.alto2anno_script.is_file()


def test_directus_url_trailing_slash_is_stripped(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DIRECTUS_URL", "https://directus.example.test/")
    config = _fresh_config_module()
    settings = config.load_settings()
    assert settings.directus_url == "https://directus.example.test"


def test_importing_module_with_missing_env_raises_at_import_time(monkeypatch):
    # This is the actual "fails at process startup" behavior described in
    # the README: importing service.config with required env unset raises
    # immediately, before any request handling code runs.
    monkeypatch.delenv("DIRECTUS_URL", raising=False)
    sys.modules.pop("service.config", None)
    with pytest.raises(RuntimeError):
        importlib.import_module("service.config")


def test_missing_xsl_path_override_raises(monkeypatch, tmp_path):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ALTO2ANNO_XSL_PATH", str(tmp_path / "does-not-exist.xsl"))
    with pytest.raises(RuntimeError, match="XSL stylesheet not found"):
        _fresh_config_module()


def test_missing_script_path_override_raises(monkeypatch, tmp_path):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ALTO2ANNO_SCRIPT_PATH", str(tmp_path / "does-not-exist.py"))
    with pytest.raises(RuntimeError, match="alto2anno.py script not found"):
        _fresh_config_module()
