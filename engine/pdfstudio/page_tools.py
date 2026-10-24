"""B01 loss-aware PDF page operations; no OCR or arbitrary content editing.

This adapter intentionally refuses encrypted and *apparently* signed input,
never replaces the original, and atomically publishes a new PDF using a
same-directory temporary file and a no-clobber hard link. Hard-link publication
requires a filesystem supporting os.link, so unsupported filesystems fail safe.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter


class PageToolError(ValueError):
    """PDF page operation rejected without modifying the source."""


def _validated_source(path: str | Path) -> Path:
    src = Path(path).resolve(strict=True)
    if not src.is_file():
        raise PageToolError("input must be a file")
    with src.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise PageToolError("missing PDF header")
    return src


def _preflight_reader(reader: PdfReader) -> None:
    if reader.is_encrypted:
        raise PageToolError("encrypted PDF requires a separate authorized unlock workflow")
    root = reader.trailer["/Root"]
    if "/Perms" in root:
        raise PageToolError("potentially digitally signed PDF: refusing page alterations")
    # Best-effort signature-field detection; B01 does not claim complete
    # signature validation (which needs an independent signature verifier).
    fields = reader.get_fields() or {}
    if any(str(field.get("/FT", "")) == "/Sig" for field in fields.values()):
        raise PageToolError("signature form field found: refusing page alterations")


def _validated_output(path: str | Path, sources: Iterable[Path]) -> Path:
    dst = Path(path).resolve()
    if dst in sources:
        raise PageToolError("output must not equal any source PDF")
    if os.path.lexists(path):
        raise FileExistsError(f"output exists: {path}")
    if not dst.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {dst.parent}")
    return dst


def _publish(writer: PdfWriter, output: Path) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".pdfstudio-", suffix=".part", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            writer.write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        # Atomic no-clobber for a new file, unlike os.replace which overwrites.
        os.link(temporary, output)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def extract_pages(source: str | Path, output: str | Path, pages: Iterable[int]) -> Path:
    """Extract 0-based page indexes in caller-specified order to a new PDF."""
    src = _validated_source(source)
    dst = _validated_output(output, (src,))
    selected = list(pages)
    if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
        raise PageToolError("provide one or more integer 0-based page indexes")
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if any(i < 0 or i >= count for i in selected):
            raise PageToolError(f"page index outside 0..{count - 1}")
        writer = PdfWriter()
        for index in selected:
            writer.add_page(reader.pages[index])
        _publish(writer, dst)
    return dst


def merge_pdfs(sources: Iterable[str | Path], output: str | Path) -> Path:
    """Merge page ranges of all source PDFs in order, into a new PDF."""
    inputs = [_validated_source(p) for p in sources]
    if len(inputs) < 2:
        raise PageToolError("merge requires at least two PDF inputs")
    dst = _validated_output(output, inputs)
    writer = PdfWriter()
    with ExitStack() as stack:
        for path in inputs:
            fh = stack.enter_context(path.open("rb"))
            reader = PdfReader(fh, strict=False)
            _preflight_reader(reader)
            for page in reader.pages:
                writer.add_page(page)
        _publish(writer, dst)
    return dst
