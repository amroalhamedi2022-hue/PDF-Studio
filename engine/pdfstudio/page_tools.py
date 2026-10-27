"""B01 loss-aware PDF page operations; no OCR or arbitrary content editing.

This adapter refuses encrypted and apparently signed input, writes only to a
private worker staging path under a configurable byte cap, and leaves final
no-clobber publication to the parent process.
"""

from __future__ import annotations

import math
import os
import re
import stat
import tempfile
from contextlib import ExitStack
from itertools import islice
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

from .page_ranges import PageRangeError, parse_page_ranges
from .inspection import DocumentInspectionError, inspect_top_level_bookmark_starts
from .jobs import sha256_file


class PageToolError(ValueError):
    """PDF page operation rejected without modifying the source."""


def _bounded_items(values, limit: int, label: str) -> list:
    """Materialize at most limit+1 items, rejecting oversized or infinite iterables.

    The extra item is only a sentinel proving the input exceeded the contract;
    it prevents an untrusted or accidental infinite generator from exhausting
    parent-process memory before the isolated PDF worker starts.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("item limit must be a positive integer")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise PageToolError(f"{label} must be iterable") from exc
    items = list(islice(iterator, limit + 1))
    if len(items) > limit:
        raise PageToolError(f"{label} exceeds configured limit of {limit} items")
    return items


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


def _validated_output(path: str | Path, sources: Iterable[Path], *,
                      allow_existing_staging: bool = False) -> Path:
    dst = Path(path).resolve()
    if dst in sources:
        raise PageToolError("output must not equal any source PDF")
    if os.path.lexists(path):
        # Only the private worker entry points may reuse the parent's preallocated
        # result.pdf. Public destination validation remains strictly no-clobber.
        if (not allow_existing_staging or os.path.islink(path) or not dst.is_file()):
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

    def seek(self, offset, whence=os.SEEK_SET):
        # The backing file is preallocated, so its physical EOF is only a
        # reservation. Expose the logical output EOF to writers that seek there.
        if whence == os.SEEK_END:
            return self._stream.seek(self._length + offset, os.SEEK_SET)
        return self._stream.seek(offset, whence)

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


def _normalize_crop_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Validate an absolute PDF user-space crop box (left, bottom, right, top)."""
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise PageToolError("crop box must contain left, bottom, right, and top")
    coordinates: list[float] = []
    for value in box:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PageToolError("crop box coordinates must be finite numbers")
        try:
            numeric = float(value)
        except (OverflowError, ValueError) as exc:
            raise PageToolError("crop box coordinates must be finite numbers") from exc
        if not math.isfinite(numeric):
            raise PageToolError("crop box coordinates must be finite numbers")
        coordinates.append(numeric)
    left, bottom, right, top = coordinates
    if right <= left or top <= bottom:
        raise PageToolError("crop box must have positive width and height")
    return left, bottom, right, top


def _extract_pages_direct(source: str | Path, output: str | Path, pages: Iterable[int] | str,
                          digests: tuple[str, ...], max_pages: int,
                          max_output_bytes: int) -> Path:
    """Extract 0-based indices or one-based inclusive text ranges to a new PDF."""
    src = _validated_source(source)
    dst = _validated_output(output, (src,), allow_existing_staging=True)
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
                selected = parse_page_ranges(pages, count, max_selections=max_pages)
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
    dst = _validated_output(output, inputs, allow_existing_staging=True)
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


def _rotate_pages_direct(source: str | Path, output: str | Path,
                        pages: Iterable[int] | str, rotation: int,
                        digests: tuple[str, ...], max_pages: int,
                        max_output_bytes: int) -> Path:
    """Rotate selected pages clockwise into a new PDF without altering the source."""
    if not isinstance(rotation, int) or isinstance(rotation, bool) or rotation not in (90, 180, 270):
        raise PageToolError("rotation must be 90, 180, or 270 degrees clockwise")
    src = _validated_source(source)
    dst = _validated_output(output, (src,), allow_existing_staging=True)
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if count < 1 or count > max_pages:
            raise PageToolError("page count exceeds configured limit")
        if isinstance(pages, str):
            try:
                selected = parse_page_ranges(pages, count, max_selections=max_pages)
            except PageRangeError as exc:
                raise PageToolError(str(exc)) from exc
        else:
            selected = list(pages)
            if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
                raise PageToolError("provide one or more integer 0-based page indexes")
        if any(i < 0 or i >= count for i in selected):
            raise PageToolError(f"page index outside 0..{count - 1}")
        selected_set = set(selected)
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            if index in selected_set:
                page.rotate(rotation)
            writer.add_page(page)
        _publish(writer, dst, (src,), digests, max_output_bytes)
    return dst


def _delete_pages_direct(source: str | Path, output: str | Path,
                        pages: Iterable[int] | str, digests: tuple[str, ...],
                        max_pages: int, max_output_bytes: int) -> Path:
    """Delete selected pages into a new PDF; never permit a zero-page result."""
    src = _validated_source(source)
    dst = _validated_output(output, (src,), allow_existing_staging=True)
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if count < 1 or count > max_pages:
            raise PageToolError("page count exceeds configured limit")
        if isinstance(pages, str):
            try:
                selected = parse_page_ranges(pages, count, max_selections=max_pages)
            except PageRangeError as exc:
                raise PageToolError(str(exc)) from exc
        else:
            try:
                selected = list(pages)
            except TypeError as exc:
                raise PageToolError("pages must be a range string or iterable of indexes") from exc
            if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
                raise PageToolError("provide one or more integer 0-based page indexes")
        if any(i < 0 or i >= count for i in selected):
            raise PageToolError(f"page index outside 0..{count - 1}")
        removed = set(selected)
        if len(removed) >= count:
            raise PageToolError("cannot delete every page from a PDF")
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            if index not in removed:
                writer.add_page(page)
        _publish(writer, dst, (src,), digests, max_output_bytes)
    return dst


def _reorder_pages_direct(source: str | Path, output: str | Path,
                          order: Iterable[int] | str, digests: tuple[str, ...],
                          max_pages: int, max_output_bytes: int) -> Path:
    """Reorder all pages using a complete 0-based permutation or one-based range string."""
    src = _validated_source(source)
    dst = _validated_output(output, (src,), allow_existing_staging=True)
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if count < 1 or count > max_pages:
            raise PageToolError("page count exceeds configured limit")
        if isinstance(order, str):
            try:
                selected = parse_page_ranges(order, count, max_selections=max_pages)
            except PageRangeError as exc:
                raise PageToolError(str(exc)) from exc
        else:
            try:
                selected = list(order)
            except TypeError as exc:
                raise PageToolError("order must be a range string or iterable of indexes") from exc
        if (len(selected) != count
                or any(not isinstance(i, int) or isinstance(i, bool) for i in selected)
                or any(i < 0 or i >= count for i in selected)
                or set(selected) != set(range(count))):
            raise PageToolError("page order must be a permutation of every 0-based page index")
        writer = PdfWriter()
        for index in selected:
            writer.add_page(reader.pages[index])
        _publish(writer, dst, (src,), digests, max_output_bytes)
    return dst


def _crop_pages_direct(source: str | Path, output: str | Path,
                      pages: Iterable[int] | str, box: tuple[float, float, float, float],
                      digests: tuple[str, ...], max_pages: int,
                      max_output_bytes: int) -> Path:
    """Set selected pages' CropBox to absolute PDF user-space coordinates.

    This is a reversible viewing/printing crop; it does not remove hidden
    content from the page content stream and is not a redaction operation.
    """
    left, bottom, right, top = _normalize_crop_box(box)
    src = _validated_source(source)
    dst = _validated_output(output, (src,), allow_existing_staging=True)
    with src.open("rb") as fh:
        reader = PdfReader(fh, strict=False)
        _preflight_reader(reader)
        count = len(reader.pages)
        if count < 1 or count > max_pages:
            raise PageToolError("page count exceeds configured limit")
        if isinstance(pages, str):
            try:
                selected = parse_page_ranges(pages, count, max_selections=max_pages)
            except PageRangeError as exc:
                raise PageToolError(str(exc)) from exc
        else:
            try:
                selected = list(pages)
            except TypeError as exc:
                raise PageToolError("provide a range string or iterable of 0-based page indexes") from exc
            if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
                raise PageToolError("provide one or more integer 0-based page indexes")
        if any(i < 0 or i >= count for i in selected):
            raise PageToolError(f"page index outside 0..{count - 1}")

        selected_set = set(selected)
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            if index in selected_set:
                media = page.mediabox
                if (left < float(media.left) or bottom < float(media.bottom)
                        or right > float(media.right) or top > float(media.top)):
                    raise PageToolError(
                        f"crop box must stay inside page {index}'s media box"
                    )
                page.cropbox.lower_left = (left, bottom)
                page.cropbox.upper_right = (right, top)
            writer.add_page(page)
        _publish(writer, dst, (src,), digests, max_output_bytes)
    return dst


def extract_pages(source: str | Path, output: str | Path, pages: Iterable[int] | str,
                  *, timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                  max_pages: int = 10000,
                  max_output_bytes: int = 1024 * 1024 * 1024,
                  free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Extract pages with an output ceiling and preflight/reserved disk budget."""
    from .page_isolation import run_page_operation
    selection = (pages if isinstance(pages, str)
                 else _bounded_items(pages, max_pages, "page selection"))
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

 


def delete_pages(source: str | Path, output: str | Path,
                 pages: Iterable[int] | str, *,
                 timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                 max_pages: int = 10000,
                 max_output_bytes: int = 1024 * 1024 * 1024,
                 free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Delete selected pages (0-based indexes or one-based inclusive range string).

    Deleting every page is rejected. The source remains untouched and the output
    is published only after the bounded worker succeeds.
    """
    from .page_isolation import run_page_operation
    if isinstance(pages, str):
        selection = pages
    else:
        selection = _bounded_items(pages, max_pages, "page selection")
    return run_page_operation("delete", (source,), output, selection,
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)


def reorder_pages(source: str | Path, output: str | Path,
                  order: Iterable[int] | str, *,
                  timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                  max_pages: int = 10000,
                  max_output_bytes: int = 1024 * 1024 * 1024,
                  free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Reorder all pages by a complete 0-based permutation or one-based range string."""
    from .page_isolation import run_page_operation
    if isinstance(order, str):
        selection = order
    else:
        selection = _bounded_items(order, max_pages, "page order")
    return run_page_operation("reorder", (source,), output, selection,
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)

def crop_pages(source: str | Path, output: str | Path,
               box: tuple[float, float, float, float],
               pages: Iterable[int] | str = "all", *,
               timeout_seconds: float = 90.0, max_memory_mb: int = 768,
               max_pages: int = 10000,
               max_output_bytes: int = 1024 * 1024 * 1024,
               free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Set selected pages' CropBox using (left, bottom, right, top) PDF coordinates.

    Range strings are one-based inclusive; integer iterables use zero-based
    indexes. CropBox cropping is reversible and does not sanitize hidden content.
    """
    from .page_isolation import run_page_operation
    normalized_box = _normalize_crop_box(box)
    if isinstance(pages, str):
        selection = pages
    else:
        selection = _bounded_items(pages, max_pages, "page selection")
    return run_page_operation("crop", (source,), output, (selection, normalized_box),
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)

def rotate_pages(source: str | Path, output: str | Path,
                 pages: Iterable[int] | str = "all", *, degrees: int = 90,
                 timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                 max_pages: int = 10000,
                 max_output_bytes: int = 1024 * 1024 * 1024,
                 free_space_margin_bytes: int = 16 * 1024 * 1024) -> Path:
    """Rotate selected pages clockwise (90/180/270) into a new no-clobber PDF.

    Range strings are one-based inclusive; integer iterables use zero-based
    page indexes, matching the existing extraction API.
    """
    from .page_isolation import run_page_operation
    if not isinstance(degrees, int) or isinstance(degrees, bool) or degrees not in (90, 180, 270):
        raise PageToolError("degrees must be 90, 180, or 270 clockwise")
    if isinstance(pages, str):
        selection = pages
    else:
        selection = _bounded_items(pages, max_pages, "page selection")
    return run_page_operation("rotate", (source,), output, (selection, degrees),
                              timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
                              max_pages=max_pages, max_output_bytes=max_output_bytes,
                              free_space_margin_bytes=free_space_margin_bytes)



def split_pdf(source: str | Path, output_dir: str | Path,
              ranges: Iterable[Iterable[int] | str], *,
              base_name: str = "split",
              timeout_seconds: float = 90.0, max_memory_mb: int = 768,
              max_pages: int = 10000,
              max_output_bytes: int = 1024 * 1024 * 1024,
              max_total_output_bytes: int = 1024 * 1024 * 1024,
              free_space_margin_bytes: int = 16 * 1024 * 1024,
              _expected_source_sha256: str | None = None) -> tuple[Path, ...]:
    """Split a PDF into explicitly selected page groups without clobbering outputs.

    Each group is either a one-based inclusive range string (for example
    "1-3,5") or an iterable of zero-based page indexes. Up to 100 groups are
    accepted. Groups are independently extracted by the existing isolated
    worker, then published as a set only after every part is staged and the
    source SHA-256 is unchanged. A failure rolls back only outputs whose file
    identity and contents still match this call; paths changed concurrently are
    preserved. Timeouts apply per part. The total-output cap applies across
    all parts. Overlapping groups are allowed intentionally, like extraction.
    """
    if isinstance(ranges, (str, bytes, bytearray)):
        raise PageToolError("ranges must be an iterable of page groups, not one range")
    groups = _bounded_items(ranges, 100, "split groups")
    if not groups:
        raise PageToolError("split requires between 1 and 100 page groups")

    normalized: list[Iterable[int] | str] = []
    for group in groups:
        if isinstance(group, str):
            if not group.strip():
                raise PageToolError("split page ranges must be nonempty")
            normalized.append(group)
            continue
        selected = _bounded_items(group, max_pages, "split page group")
        if not selected or any(not isinstance(i, int) or isinstance(i, bool) for i in selected):
            raise PageToolError("each split group must contain integer 0-based page indexes")
        normalized.append(selected)

    if (not isinstance(base_name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", base_name) is None):
        raise PageToolError("base_name must be 1-80 ASCII letters, digits, underscores or hyphens")
    for key, value in (("max_memory_mb", max_memory_mb), ("max_pages", max_pages),
                       ("max_output_bytes", max_output_bytes),
                       ("max_total_output_bytes", max_total_output_bytes)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if max_total_output_bytes < 5:
        raise ValueError("max_total_output_bytes must allow a PDF header")
    if (not isinstance(free_space_margin_bytes, int)
            or isinstance(free_space_margin_bytes, bool) or free_space_margin_bytes < 0):
        raise ValueError("free_space_margin_bytes must be a non-negative integer")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout_seconds must be finite and positive")

    raw_directory = Path(output_dir)
    if raw_directory.is_symlink():
        raise PageToolError("split output directory must not be a symlink")
    directory = raw_directory.resolve(strict=True)
    if not directory.is_dir():
        raise PageToolError("split output path must be an existing directory")
    src = _validated_source(source)
    targets = tuple(directory / f"{base_name}-part-{index:03d}.pdf"
                    for index in range(1, len(normalized) + 1))
    if any(os.path.lexists(target) for target in targets):
        raise FileExistsError("one or more split output files already exist")

    source_digest = sha256_file(src)
    if (_expected_source_sha256 is not None
            and source_digest != _expected_source_sha256):
        raise PageToolError("source changed after bookmark split planning")
    total_bytes = 0
    # target, expected identity, expected size, expected mtime, expected digest
    published: list[tuple[Path, tuple[int, int], int, int, str | None]] = []

    def rollback_published() -> bool:
        """Remove only outputs that still match the exact files created here."""
        incomplete = False
        for target, identity, size, mtime_ns, digest in reversed(published):
            try:
                info = target.lstat()
                if (not stat.S_ISREG(info.st_mode)
                        or (info.st_dev, info.st_ino) != identity
                        or info.st_size != size or info.st_mtime_ns != mtime_ns):
                    incomplete = True
                    continue
                if digest is not None and sha256_file(target) != digest:
                    incomplete = True
                    continue
                target.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                incomplete = True
        return incomplete

    try:
        with tempfile.TemporaryDirectory(prefix=".pdfstudio-split-", dir=directory) as temp_name:
            staging_directory = Path(temp_name)
            staged_files: list[Path] = []
            for index, group in enumerate(normalized):
                if sha256_file(src) != source_digest:
                    raise PageToolError("source changed during PDF split")
                remaining = max_total_output_bytes - total_bytes
                if remaining < 5:
                    raise PageToolError("split exceeds configured total output byte limit")
                part_cap = min(max_output_bytes, remaining)
                staged = staging_directory / targets[index].name
                extract_pages(
                    src, staged, group,
                    timeout_seconds=timeout_seconds,
                    max_memory_mb=max_memory_mb,
                    max_pages=max_pages,
                    max_output_bytes=part_cap,
                    free_space_margin_bytes=free_space_margin_bytes,
                )
                info = staged.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_size < 5
                        or info.st_size > part_cap):
                    raise PageToolError("split worker produced an invalid part")
                with staged.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise PageToolError("split worker produced a non-PDF part")
                total_bytes += info.st_size
                if total_bytes > max_total_output_bytes:
                    raise PageToolError("split exceeds configured total output byte limit")
                staged_files.append(staged)

            if sha256_file(src) != source_digest:
                raise PageToolError("source changed during PDF split")
            if any(os.path.lexists(target) for target in targets):
                raise FileExistsError("a split output path was created concurrently")

            try:
                for staged, target in zip(staged_files, targets):
                    staged_info = staged.lstat()
                    staged_identity = (staged_info.st_dev, staged_info.st_ino)
                    os.link(staged, target)
                    try:
                        target_info = target.lstat()
                    except OSError as exc:
                        raise PageToolError("split output disappeared during publication") from exc
                    target_identity = (target_info.st_dev, target_info.st_ino)
                    if (not stat.S_ISREG(target_info.st_mode)
                            or target_identity != staged_identity):
                        # Remove only if this destination still aliases the staged file.
                        try:
                            current_target = target.lstat()
                            current_stage = staged.lstat()
                            if (stat.S_ISREG(current_target.st_mode)
                                    and stat.S_ISREG(current_stage.st_mode)
                                    and (current_target.st_dev, current_target.st_ino)
                                    == (current_stage.st_dev, current_stage.st_ino)):
                                target.unlink()
                        except OSError:
                            pass
                        raise PageToolError("split output identity changed during publication")

                    # Record immediately so a later validation failure can safely roll back.
                    published.append((target, staged_identity, target_info.st_size,
                                      target_info.st_mtime_ns, None))
                    digest = sha256_file(target)
                    stable_info = target.lstat()
                    if ((stable_info.st_dev, stable_info.st_ino) != staged_identity
                            or stable_info.st_size != target_info.st_size
                            or stable_info.st_mtime_ns != target_info.st_mtime_ns):
                        raise PageToolError("split output changed during validation")
                    published[-1] = (target, staged_identity, stable_info.st_size,
                                     stable_info.st_mtime_ns, digest)
                    with target.open("rb") as stream:
                        if stream.read(5) != b"%PDF-":
                            raise PageToolError("published split part has no PDF header")

                if sha256_file(src) != source_digest:
                    raise PageToolError("source changed before split publication completed")
            except BaseException as exc:
                if rollback_published():
                    raise PageToolError(
                        "split failed; output paths changed concurrently and were preserved"
                    ) from exc
                raise

        return targets
    except BaseException:
        # A staging failure occurs before publication and leaves no final outputs.
        # Publication failures are rolled back inside the staging-directory scope.
        raise

def split_pdf_by_bookmarks(source: str | Path, output_dir: str | Path, *,
                           base_name: str = "split",
                           timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                           max_pages: int = 10000,
                           max_output_bytes: int = 1024 * 1024 * 1024,
                           max_total_output_bytes: int = 1024 * 1024 * 1024,
                           free_space_margin_bytes: int = 16 * 1024 * 1024) -> tuple[Path, ...]:
    """Split at top-level PDF bookmarks using bounded inspection and safe publication.

    Output names are numeric and never include document-supplied bookmark titles.
    Pages before the first bookmark become a separate preface part. Nested
    bookmarks are ignored so a deeply nested outline cannot explode part count.
    The inspected source SHA is pinned into split_pdf to reject changes between
    outline planning and page extraction. The existing 100-part, per-part and
    aggregate byte caps and no-clobber publication remain in force.
    """
    try:
        page_count, bookmark_starts, source_digest = inspect_top_level_bookmark_starts(
            source, max_pages=max_pages, max_bookmarks=100,
            timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
        )
    except DocumentInspectionError as exc:
        raise PageToolError("bookmark split could not safely inspect the PDF outline") from exc
    boundaries = list(bookmark_starts)
    if boundaries[0] > 0:
        boundaries.insert(0, 0)
    groups: list[str] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] - 1 if index + 1 < len(boundaries) else page_count - 1
        if end >= start:
            groups.append(f"{start + 1}-{end + 1}")
    if not groups or len(groups) > 100:
        raise PageToolError("bookmark split requires between 1 and 100 nonempty parts")
    return split_pdf(
        source, output_dir, groups, base_name=base_name,
        timeout_seconds=timeout_seconds, max_memory_mb=max_memory_mb,
        max_pages=max_pages, max_output_bytes=max_output_bytes,
        max_total_output_bytes=max_total_output_bytes,
        free_space_margin_bytes=free_space_margin_bytes,
        _expected_source_sha256=source_digest,
    )

