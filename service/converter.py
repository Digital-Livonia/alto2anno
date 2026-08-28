"""Runs alto2anno.py (the existing CLI tool) as a subprocess.

Important quirk of alto2anno.py: it iterates every *.xml file in the target
directory and, per file, catches xsltproc failures internally (it prints an
error and continues to the next file) -- see `process_directory` in
alto2anno.py. That means a zero exit code only tells us the script itself
didn't crash; it does NOT guarantee every input file produced a matching
*.json output. Callers of `run_alto2anno` must verify expected outputs
themselves (main.py does this after calling this function).
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import settings


class ConversionError(Exception):
    """Raised when alto2anno.py itself cannot be run or exits non-zero."""


@dataclass
class ConversionResult:
    stdout: str
    stderr: str


async def run_alto2anno(directory: Path, manifest_uri: str, xratio: str, yratio: str) -> ConversionResult:
    if shutil.which("xsltproc") is None:
        raise ConversionError("xsltproc is not installed or not on PATH.")

    command = [
        settings.python_executable,
        str(settings.alto2anno_script),
        "-d", str(directory),
        "-x", str(settings.xsl_path),
        "-m", manifest_uri,
        "--xratio", xratio,
        "--yratio", yratio,
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if process.returncode != 0:
        raise ConversionError(
            f"alto2anno.py exited with code {process.returncode}: "
            f"{stderr.strip() or stdout.strip() or '(no output)'}"
        )

    return ConversionResult(stdout=stdout, stderr=stderr)
