"""alto2anno.py as a command-line tool: how a file's canvas number is chosen.

Default (unchanged): the position in the alphabetically sorted *.xml list.
New: `--index-map` gives explicit canvas indices per filename, so the numbers
no longer depend on how the files happen to sort or on how many pages have ALTO.
Runs the real script with the real xsltproc (same as the service happy-path test).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

import pytest

from .conftest import FIXTURES_DIR, REPO_ROOT

pytestmark = pytest.mark.skipif(shutil.which("xsltproc") is None, reason="xsltproc not installed")

SCRIPT = REPO_ROOT / "alto2anno.py"
XSL = REPO_ROOT / "annotationListNoArt.xsl"  # the stylesheet the service uses by default


def _run(directory, *extra):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "-d", str(directory), "-x", str(XSL),
         "-m", "https://example.test/manifest", *extra],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _canvas_of(path):
    """Canvas number every annotation in the file targets (must be exactly one)."""
    targets = set(re.findall(r"/iiif/canvas/(\d+)#xywh=", path.read_text(encoding="utf-8")))
    assert len(targets) == 1, f"{path.name}: targets {targets}"
    return int(targets.pop())


@pytest.fixture()
def pages(tmp_path):
    # Two pages whose names sort the "wrong" way round on purpose: b < c.
    shutil.copy(FIXTURES_DIR / "sample_page_a.xml", tmp_path / "b.xml")
    shutil.copy(FIXTURES_DIR / "sample_page_b.xml", tmp_path / "c.xml")
    return tmp_path


def test_without_index_map_the_alphabetical_position_is_the_canvas_number(pages):
    _run(pages)
    assert _canvas_of(pages / "b.json") == 1
    assert _canvas_of(pages / "c.json") == 2


def test_index_map_overrides_the_position(pages, tmp_path_factory):
    index_map = tmp_path_factory.mktemp("map") / "map.json"
    index_map.write_text(json.dumps({"b.xml": 7, "c.xml": 3}))
    _run(pages, "--index-map", str(index_map))
    assert _canvas_of(pages / "b.json") == 7
    assert _canvas_of(pages / "c.json") == 3


def test_file_missing_from_the_index_map_falls_back_to_its_position(pages, tmp_path_factory):
    index_map = tmp_path_factory.mktemp("map") / "map.json"
    index_map.write_text(json.dumps({"b.xml": 5}))  # c.xml not listed
    _run(pages, "--index-map", str(index_map))
    assert _canvas_of(pages / "b.json") == 5
    assert _canvas_of(pages / "c.json") == 2  # position 2 in the sorted list
