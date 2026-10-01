"""Optional local Tesseract OCR adapter for image and PDF invoice files."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_DOCUMENT_BYTES = 18 * 1024 * 1024
MAX_PDF_PAGES = 10
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class OCRUnavailableError(RuntimeError):
    """Raised when a required local OCR executable is not installed/configured."""


def _resolve_program(explicit_path: str | None, program: str) -> str:
    resolved = shutil.which(explicit_path or program)
    if resolved is None:
        raise OCRUnavailableError(f"{program} is unavailable; install it or set its configured path")
    return resolved


def _validate_document(content: bytes, filename: str) -> str:
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Invoice document must be non-empty and no larger than 18 MiB")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Supported invoice files are PDF, PNG, JPEG, and TIFF")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("File content does not match the PDF extension")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("File content does not match the PNG extension")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("File content does not match the JPEG extension")
    if suffix in {".tif", ".tiff"} and content[:4] not in {b"II*\x00", b"MM\x00*"}:
        raise ValueError("File content does not match the TIFF extension")
    return suffix


def extract_ocr_text(
    content: bytes,
    filename: str,
    *,
    tesseract_path: str | None = None,
    pdftoppm_path: str | None = None,
    tessdata_path: str | None = None,
    languages: str = "eng",
) -> str:
    """OCR a bounded PDF/image using local executables; never writes to a persistent path."""
    suffix = _validate_document(content, filename)
    tesseract = _resolve_program(tesseract_path, "tesseract")
    env = {**os.environ, "TESSDATA_PREFIX": tessdata_path} if tessdata_path else None
    with tempfile.TemporaryDirectory(prefix="procurax-ocr-") as temp_dir:
        root = Path(temp_dir)
        source = root / f"invoice{suffix}"
        source.write_bytes(content)
        if suffix == ".pdf":
            converter = _resolve_program(pdftoppm_path, "pdftoppm")
            prefix = root / "page"
            subprocess.run(  # noqa: S603 - resolved local executable and fixed arguments; no shell
                [
                    converter,
                    "-f",
                    "1",
                    "-l",
                    str(MAX_PDF_PAGES),
                    "-r",
                    "200",
                    "-png",
                    str(source),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )  # noqa: S603 - resolved local executable, constant flags, and temporary paths; no shell
            pages = sorted(root.glob("page-*.png"))
            if not pages:
                raise ValueError("No readable pages were found in the PDF")
        else:
            pages = [source]
        results: list[str] = []
        for page in pages:
            completed = subprocess.run(  # noqa: S603 - resolved local executable and fixed arguments; no shell
                [tesseract, str(page), "stdout", "-l", languages, "--psm", "6"],
                check=True,
                capture_output=True,
                timeout=45,
                env=env,
            )  # noqa: S603 - resolved local executable, fixed arguments, and temporary image path
            results.append(completed.stdout.decode("utf-8", errors="replace"))
        text = "\n\n".join(results).strip()
        if not text:
            raise ValueError("OCR did not find readable text")
        if len(text) > 100_000:
            raise ValueError("OCR output is larger than the supported extraction limit")
        return text
