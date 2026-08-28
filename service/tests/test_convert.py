"""Integration tests for POST /convert.

Outbound HTTP calls this service makes (to Directus for asset download /
file upload, and to the caller-supplied callback_url) are intercepted with
pytest-httpx's `httpx_mock` fixture, which patches the transport used by
httpx.AsyncClient regardless of which event loop / thread it runs on (the
TestClient runs the ASGI app on its own anyio-managed loop).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from .conftest import FIXTURES_DIR

DIRECTUS_URL = "https://directus.example.test"


def _asset_url(file_id: str) -> str:
    return f"{DIRECTUS_URL}/assets/{file_id}"


def _files_url() -> str:
    return f"{DIRECTUS_URL}/files"


# ---------------------------------------------------------------------------
# 1. Auth ordering: 401 must win even over a malformed body.
# ---------------------------------------------------------------------------


class TestAuthOrdering:
    def test_missing_auth_header_with_valid_body_is_401(self, client, valid_payload):
        response = client.post("/convert", json=valid_payload)
        assert response.status_code == 401

    def test_wrong_bearer_token_with_valid_body_is_401(self, client, valid_payload):
        response = client.post(
            "/convert", json=valid_payload, headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_missing_auth_header_with_malformed_json_body_is_401_not_400(self, client):
        # This is the specific ordering bug this middleware was written to
        # fix: an unauthenticated request must never reach Pydantic's body
        # validation (which would otherwise produce 400 and leak schema
        # details to a caller that hasn't proven it's allowed to ask).
        response = client.post(
            "/convert",
            content=b"{not valid json at all",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401

    def test_missing_auth_header_with_valid_json_but_missing_fields_is_401_not_400(self, client):
        response = client.post("/convert", json={"totally": "wrong shape"})
        assert response.status_code == 401

    def test_malformed_authorization_header_format_is_401(self, client, valid_payload):
        # No "Bearer " prefix.
        response = client.post(
            "/convert", json=valid_payload, headers={"Authorization": "test-shared-secret"}
        )
        assert response.status_code == 401

    def test_empty_authorization_header_is_401(self, client, valid_payload):
        response = client.post("/convert", json=valid_payload, headers={"Authorization": ""})
        assert response.status_code == 401

    def test_health_endpoint_does_not_require_auth(self, client):
        response = client.get("/health")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 2. Input validation -> 400 (once auth passes).
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_missing_required_field_is_400(self, client, auth_headers, valid_payload):
        del valid_payload["manifest_uri"]
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_empty_alto_file_ids_is_400(self, client, auth_headers, valid_payload):
        valid_payload["alto_file_ids"] = []
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_non_http_manifest_uri_is_400(self, client, auth_headers, valid_payload):
        valid_payload["manifest_uri"] = "ftp://not-http.example.test/manifest"
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_relative_manifest_uri_is_400(self, client, auth_headers, valid_payload):
        valid_payload["manifest_uri"] = "/relative/path"
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_non_http_callback_url_is_400(self, client, auth_headers, valid_payload):
        valid_payload["callback_url"] = "not-a-url-at-all"
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_url_field_validator_error_response_is_json_serializable(
        self, app, auth_headers, valid_payload
    ):
        """Regression test for a bug found in review: a field_validator
        failure (e.g. _must_be_http_url) puts the raised ValueError instance
        itself under errors()[i]["ctx"]["error"], which isn't
        JSON-serializable on its own. Passing exc.errors() straight to
        JSONResponse used to blow up while rendering the response body and
        produced a bare 500 for exactly the input this validator exists to
        reject cleanly (fixed by routing exc.errors() through
        jsonable_encoder in validation_exception_handler).

        Uses a TestClient with raise_server_exceptions=False (the default
        `client` fixture uses the FastAPI default of True, which would
        re-raise an underlying exception into the test instead of giving us
        the response an actual HTTP client would see) so this actually
        observes the over-the-wire status code, not the exception the
        server-side re-raise would have hidden it behind.
        """
        from fastapi.testclient import TestClient

        valid_payload["callback_url"] = "not-a-url-at-all"
        with TestClient(app, raise_server_exceptions=False) as lenient_client:
            response = lenient_client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_empty_string_collection_is_400(self, client, auth_headers, valid_payload):
        valid_payload["collection"] = ""
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400

    def test_missing_callback_token_is_400(self, client, auth_headers, valid_payload):
        del valid_payload["callback_token"]
        response = client.post("/convert", json=valid_payload, headers=auth_headers)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 3. Happy path: real xsltproc conversion, mocked Directus + callback.
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_convert_end_to_end_with_real_xsltproc(
        self, client, auth_headers, valid_payload, httpx_mock
    ):
        file_ids = valid_payload["alto_file_ids"]  # ["file-aaa", "file-bbb"]
        fixtures = [FIXTURES_DIR / "sample_page_a.xml", FIXTURES_DIR / "sample_page_b.xml"]

        for file_id, fixture_path in zip(file_ids, fixtures):
            httpx_mock.add_response(
                url=_asset_url(file_id),
                content=fixture_path.read_bytes(),
                headers={"content-disposition": f'attachment; filename="{fixture_path.name}"'},
            )

        uploaded_ids = ["new-anno-uuid-1", "new-anno-uuid-2"]
        for new_id in uploaded_ids:
            httpx_mock.add_response(
                url=_files_url(), method="POST", json={"data": {"id": new_id}}
            )

        httpx_mock.add_response(
            url=valid_payload["callback_url"], method="POST", json={"ok": True}
        )

        response = client.post("/convert", json=valid_payload, headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "collection": "magistraat",
            "id": "47",
            "annotation_file_ids": uploaded_ids,
        }

        # Verify the callback was actually POSTed with the right body + bearer token.
        requests = httpx_mock.get_requests(url=valid_payload["callback_url"])
        assert len(requests) == 1
        callback_request = requests[0]
        assert callback_request.headers["Authorization"] == "Bearer callback-secret"
        assert json.loads(callback_request.content) == {
            "collection": "magistraat",
            "id": "47",
            "annotation_file_ids": uploaded_ids,
        }

        # Sanity-check the uploaded content actually looks like the real
        # xsltproc-produced annotation JSON (not just a mocked stand-in).
        upload_requests = httpx_mock.get_requests(url=_files_url())
        assert len(upload_requests) == 2
        first_upload_body = upload_requests[0].content
        assert b'"@type":"sc:AnnotationList"' in first_upload_body

        # The uploaded filename must be the original stem, not the
        # "000N_"-prefixed one used on disk to force alto2anno.py's sort
        # order -- see test below for why (directus-iiif-endpoint matches
        # annotation files to canvas images by exact filename stem).
        assert b'filename="sample_page_a.json"' in first_upload_body
        assert b'filename="0001_sample_page_a.json"' not in first_upload_body

    def test_uploaded_filename_strips_our_index_prefix_even_when_original_already_has_one(
        self, client, auth_headers, valid_payload, httpx_mock
    ):
        # Regression test for a real bug: this project's ALTO/image files
        # already carry their own "NNNN_" ordering prefix in their real
        # filenames (e.g. "0001_001.xml" alongside canvas image
        # "0001_001.jpg"). derive_filename() prepends a second index on
        # top ("0001_0001_001.xml"), and the *uploaded* filename must not
        # keep that -- it has to come back out as "0001_001.json" so it
        # still matches the canvas image's filename stem.
        file_id = valid_payload["alto_file_ids"][0]
        fixture_path = FIXTURES_DIR / "sample_page_a.xml"
        httpx_mock.add_response(
            url=_asset_url(file_id),
            content=fixture_path.read_bytes(),
            headers={"content-disposition": 'attachment; filename="0001_001.xml"'},
        )
        # Second file can be anything valid; not the focus of this test.
        other_id = valid_payload["alto_file_ids"][1]
        httpx_mock.add_response(
            url=_asset_url(other_id),
            content=(FIXTURES_DIR / "sample_page_b.xml").read_bytes(),
            headers={"content-disposition": 'attachment; filename="0002_002.xml"'},
        )
        httpx_mock.add_response(url=_files_url(), method="POST", json={"data": {"id": "new-1"}})
        httpx_mock.add_response(url=_files_url(), method="POST", json={"data": {"id": "new-2"}})
        httpx_mock.add_response(url=valid_payload["callback_url"], method="POST", json={"ok": True})

        response = client.post("/convert", json=valid_payload, headers=auth_headers)

        assert response.status_code == 200, response.text
        upload_requests = httpx_mock.get_requests(url=_files_url())
        assert len(upload_requests) == 2
        assert b'filename="0001_001.json"' in upload_requests[0].content
        assert b'filename="0001_0001_001.json"' not in upload_requests[0].content
        assert b'filename="0002_002.json"' in upload_requests[1].content


# ---------------------------------------------------------------------------
# 5. xsltproc missing from PATH -> 502, not a crash/hang.
# ---------------------------------------------------------------------------


class TestMissingXsltproc:
    def test_missing_xsltproc_returns_502(
        self, client, auth_headers, valid_payload, httpx_mock, monkeypatch
    ):
        file_ids = valid_payload["alto_file_ids"]
        fixtures = [FIXTURES_DIR / "sample_page_a.xml", FIXTURES_DIR / "sample_page_b.xml"]
        for file_id, fixture_path in zip(file_ids, fixtures):
            httpx_mock.add_response(url=_asset_url(file_id), content=fixture_path.read_bytes())

        import service.converter as converter_module

        monkeypatch.setattr(converter_module.shutil, "which", lambda name: None)

        response = client.post("/convert", json=valid_payload, headers=auth_headers)

        assert response.status_code == 502
        assert "conversion" in response.json()["detail"].lower()
        # No files should have been uploaded or a callback fired -- the
        # request must have failed before ever reaching those steps.
        assert httpx_mock.get_requests(url=_files_url()) == []
        assert httpx_mock.get_requests(url=valid_payload["callback_url"]) == []


# ---------------------------------------------------------------------------
# 6. alto2anno.py silently drops output for one or more files -> 502.
# ---------------------------------------------------------------------------


class TestPartialConversionOutput:
    def test_fewer_outputs_than_inputs_returns_502_naming_the_count(
        self, client, auth_headers, valid_payload, httpx_mock, monkeypatch
    ):
        payload = dict(valid_payload, alto_file_ids=["file-1", "file-2", "file-3"])
        for file_id in payload["alto_file_ids"]:
            httpx_mock.add_response(url=_asset_url(file_id), content=b"<alto/>")

        import service.main as main_module
        from service.converter import ConversionResult

        async def fake_run_alto2anno(directory, manifest_uri, xratio, yratio):
            # Simulate alto2anno.py's documented quirk: it exits 0 but
            # silently produced output for only one of the three inputs.
            xml_files = sorted(Path(directory).glob("*.xml"))
            assert len(xml_files) == 3
            only_one = xml_files[0]
            (only_one.with_suffix(".json")).write_text("{}")
            return ConversionResult(stdout="", stderr="")

        monkeypatch.setattr(main_module, "run_alto2anno", fake_run_alto2anno)

        response = client.post("/convert", json=payload, headers=auth_headers)

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "2" in detail and "3" in detail
        assert httpx_mock.get_requests(url=_files_url()) == []
        assert httpx_mock.get_requests(url=payload["callback_url"]) == []


# ---------------------------------------------------------------------------
# 7. Fail-fast: 2nd of 3 uploads fails -> whole request fails, no partial list.
# ---------------------------------------------------------------------------


class TestFailFastOnPartialUploadFailure:
    def test_second_of_three_uploads_failing_fails_whole_request(
        self, client, auth_headers, valid_payload, httpx_mock, monkeypatch
    ):
        payload = dict(valid_payload, alto_file_ids=["file-1", "file-2", "file-3"])
        for file_id in payload["alto_file_ids"]:
            httpx_mock.add_response(url=_asset_url(file_id), content=b"<alto/>")

        import service.main as main_module
        from service.converter import ConversionResult

        async def fake_run_alto2anno(directory, manifest_uri, xratio, yratio):
            for xml_file in Path(directory).glob("*.xml"):
                xml_file.with_suffix(".json").write_text("{}")
            return ConversionResult(stdout="", stderr="")

        monkeypatch.setattr(main_module, "run_alto2anno", fake_run_alto2anno)

        upload_calls = []

        async def fake_upload_file(client_arg, file_path, upload_filename=None):
            upload_calls.append(file_path.name)
            if len(upload_calls) == 2:
                request = httpx.Request("POST", _files_url())
                response = httpx.Response(500, request=request, text="upload failed")
                raise httpx.HTTPStatusError("upload failed", request=request, response=response)
            return f"uploaded-{len(upload_calls)}"

        monkeypatch.setattr(main_module, "upload_file", fake_upload_file)

        response = client.post("/convert", json=payload, headers=auth_headers)

        assert response.status_code == 502
        detail = response.json()["detail"]
        # Exactly one file had been uploaded before the 2nd upload failed.
        assert "1 of 3" in detail
        assert len(upload_calls) == 2  # never attempted the 3rd upload
        # No partial annotation_file_ids anywhere in the error response.
        assert "annotation_file_ids" not in response.json()
        assert httpx_mock.get_requests(url=payload["callback_url"]) == []


# ---------------------------------------------------------------------------
# Downstream failure: Directus asset download itself fails -> 502.
# ---------------------------------------------------------------------------


class TestUpstreamDownloadFailure:
    def test_asset_download_failure_returns_502(
        self, client, auth_headers, valid_payload, httpx_mock
    ):
        file_ids = valid_payload["alto_file_ids"]
        httpx_mock.add_response(url=_asset_url(file_ids[0]), status_code=404)
        # Fail-fast: files are downloaded sequentially in request order and
        # the first failure aborts immediately, so the second file's asset
        # is never even requested. No mock registered for it -- if this
        # assumption is wrong, pytest-httpx will fail the test with an
        # "unmatched request" error, which is itself a useful signal.

        response = client.post("/convert", json=valid_payload, headers=auth_headers)

        assert response.status_code == 502
        assert file_ids[0] in response.json()["detail"]


class TestCallbackDeliveryFailure:
    def test_callback_failure_returns_502_after_uploads_succeeded(
        self, client, auth_headers, valid_payload, httpx_mock, monkeypatch
    ):
        for file_id in valid_payload["alto_file_ids"]:
            httpx_mock.add_response(url=_asset_url(file_id), content=b"<alto/>")

        import service.main as main_module
        from service.converter import ConversionResult

        async def fake_run_alto2anno(directory, manifest_uri, xratio, yratio):
            for xml_file in Path(directory).glob("*.xml"):
                xml_file.with_suffix(".json").write_text("{}")
            return ConversionResult(stdout="", stderr="")

        async def fake_upload_file(client_arg, file_path, upload_filename=None):
            return f"uploaded-{file_path.stem}"

        monkeypatch.setattr(main_module, "run_alto2anno", fake_run_alto2anno)
        monkeypatch.setattr(main_module, "upload_file", fake_upload_file)

        httpx_mock.add_response(url=valid_payload["callback_url"], method="POST", status_code=500)

        response = client.post("/convert", json=valid_payload, headers=auth_headers)

        assert response.status_code == 502
        assert "callback" in response.json()["detail"].lower()
