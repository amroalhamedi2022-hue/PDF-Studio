"""B01 loss-aware PDF page operations; no OCR or arbitrary content editing.

This adapter refuses encrypted and apparently signed input, writes only to a
private worker staging path under a configurable byte cap, and leaves final
no-clobber publication to the parent process.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

from .page_ranges import PageRangeError, parse_page_ranges
from .jobs import sha256_file


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


class _CappedWriter:
    """Delegate a preallocated binary stream while enforcing the output ceiling."""

    def __init__(self, stream, max_bytes: int):
        self._stream = stream
        self._max_bytes = max_bytes
        # The parent preallocates the staging file. Track bytes actually written,
        # not its reserved length, so the final PDF can be truncated accurately.
        self._length = 0

    @property
    def length(self) -> int:
        return self._length

    def write(self, data):
        position = self._stream.tell()
        projected_size = max(self._length, position + len(data))
        if projected_size > self._max_bytes:
            raise PageToolError("PDF output exceeds configured byte limit")
        written = self._stream.write(data)
        self._length = max(self._length, position + written)
        return written

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _publish(writer: PdfWriter, output: Path, sources: tuple[Path, ...],
             digests: tuple[str, ...], max_output_bytes: int) -> None:
    # The parent has already reserved this private staging file on the same
    # filesystem as the destination. Reuse it instead of allocating a second
    # full-size temporary copy.
    created = False
    try:
        with output.open("r+b") as fh:
            created = True
            fh.seek(0)
            capped = _CappedWriter(fh, max_output_bytes)
            writer.write(capped)
            if capped.length < 5:
                raise PageToolError("PDF output is empty or truncated")
            fh.truncate(capped.length)
            fh.flush()
            os.fsync(fh.fileno())
        if any(sha256_file(src) != digest for src, digest in zip(sources, digests)):
            raise PageToolError("source changed during PDF operation")
    except BaseException:
        if created:
            try:
                output.unlink()
            except FileNotFoundError:
                pass
        raise


def _extract_pages_direct(source: str | Path, output: str | Path, pages: Iterable[int] | str,
                          digests: tuple[str, ...], max_pages: int,
                          max_output_bytes: int) -> Path:
    """Extract 0-based indices or one-based inclusive text ranges to a new PDF."""
    src = _validated_source(source)
    dst = _validated_output(output, (src,))
    if not isinstance(pages, str):
        selected = list(pages)
        if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
            raise PageToolError("provide one or more integer 0-based page indexes")
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if count < 1 or count > max_pages:
            raise PageToolError('page count exceeds configured limit')
        if isinstance(pages, str):
            try:
                selected = parse_page_ranges(pages, count)
            except PageRangeError as exc:
                raise PageToolError(str(exc)) from exc
        if any(i < 0 or i >= count for i in selected):
            raise PageToolError(f"page index outside 0..{count - 1}")
        writer = PdfWriter()
        for index in selected:
            writer.add_page(reader.pages[index])
        _publish(writer, dst, (src,), digests, max_output_bytes)
    return dst


def _merge_pdfs_direct(sources: Iterable[str | Path], output: str | Path,
                       digests: tuple[str, ...], max_pages: int,
                       max_output_bytes: int) -> Path:
    """Merge page ranges of all source PDFs in order, into a new PDF."""
    inputs = [_validated_source(p) for p in sources]
    if len(inputs) < 2:
        raise PageToolError("merge requires at least two PDF inputs")
    dst = _validated_output(output, inputs)
    writer = PdfWriter()
    with ExitStack() as stack:
        total_pages = 0
        for path in inputs:
            fh = stack.enter_context(path.open("rb"))
            reader = PdfReader(fh, strict=False)
            _preflight_reader(reader)
            count = len(reader.pages)
            total_pages += count
            if count < 1 or total_pages > max_pages:
                raise PageToolError('total page count exceeds configured limit')
            for page in reader.pages:
                writer.add_page(page)
        _publish(writer, dst, tuple(inputs), digests, max_output_bytes)
    return dst


def extract_pages(source: str | Path, output: str | Path, pages: Iterable[int] | str,
                  *, timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                  max_pages: int = 10000,
                  max_output_bytes: int = 1024 * 1024 * 1024,
                  free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Extract pages with an output ceiling and preflight/reserved disk budget."""
    from .page_isolation import run_page_operation
    selection = pages if isinstance(pages, str) else list(pages)
    return run_page_operation("extract", (source,), output, selection,
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)


def merge_pdfs(sources: Iterable[str | Path], output: str | Path, *,
               timeout_seconds: float = 90.0, max_memory_mb: int = 768,
               max_pages: int = 10000,
               max_output_bytes: int = 1024 * 1024 * 1024,
               free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Merge pages with an output ceiling and preflight/reserved disk budget."""
    from .page_isolation import run_page_operation
    return run_page_operation("merge", tuple(sources), output,
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)
