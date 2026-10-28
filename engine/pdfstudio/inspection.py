"""B01 read-only PDF preflight; bounded sampling, no OCR or rendering.

Use in a constrained worker when inspecting untrusted PDFs. This module does
not by itself sandbox parsers or guarantee bounded memory for malicious PDFs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import multiprocessing as mp
import os
import time
from pathlib import Path

from .jobs import sha256_file, sha256_file_before_deadline
from .windows_job import WindowsJob, WindowsJobError


class DocumentInspectionError(ValueError):
    """Document structure cannot be inspected safely with this adapter."""


class PdfPasswordRequired(DocumentInspectionError):
    """Password is missing or wrong."""


@dataclass(frozen=True)
class PageSample:
    page_index: int
    width_pt: float
    height_pt: float
    has_extracted_text: bool
    extracted_characters: int


@dataclass(frozen=True)
class DocumentInspection:
    source: str
    size_bytes: int
    sha256: str
    page_count: int | None
    encrypted: bool
    password_required: bool
    title: str | None
    author: str | None
    sampled_pages: tuple[PageSample, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _sample_indices(page_count: int, limit: int) -> tuple[int, ...]:
    if limit < 0 or isinstance(limit, bool):
        raise ValueError("sample limit must be a nonnegative integer")
    if not page_count or not limit:
        return ()
    if page_count <= limit:
        return tuple(range(page_count))
    if limit == 1:
        return (0,)
    return tuple(sorted({round(i * (page_count - 1) / (limit - 1)) for i in range(limit)}))


def _inspect_pdf_direct(source: str | Path, *, password: str | None = None, sample_limit: int = 4) -> DocumentInspection:
    """Return a read-only metadata/text-layer sample; does NOT run OCR.

    Encrypted PDFs return metadata without page details unless unlocked. A
    parser exception becomes DocumentInspectionError (never a repaired file).
    Large/hostile files still need external process isolation and resource caps.
    """
    from pypdf import PdfReader
    src = Path(source).resolve(strict=True)
    if not src.is_file():
        raise DocumentInspectionError("source must be a regular file")
    if not isinstance(sample_limit, int) or isinstance(sample_limit, bool) or sample_limit < 0 or sample_limit > 32:
        raise ValueError("sample_limit must be an integer in 0..32")
    with src.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise DocumentInspectionError("missing PDF file header")
    digest = sha256_file(src)
    size = src.stat().st_size
    try:
        with src.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            encrypted = reader.is_encrypted
            if encrypted and (password is None or not reader.decrypt(password)):
                return DocumentInspection(str(src), size, digest, None, True, True, None, None, (), ("encrypted document; page data unavailable",))
            count = len(reader.pages)
            metadata = reader.metadata
            title = str(metadata.title)[:2048] if metadata and metadata.title else None
            author = str(metadata.author)[:2048] if metadata and metadata.author else None
            results = []
            warnings = []
            for index in _sample_indices(count, sample_limit):
                page = reader.pages[index]
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                    warnings.append(f"text extraction unsuccessful on page {index + 1}")
                results.append(PageSample(index, width, height, bool(text.strip()), len(text)))
            return DocumentInspection(str(src), size, digest, count, encrypted, False, title, author, tuple(results), tuple(warnings))
    except DocumentInspectionError:
        raise
    except Exception as exc:
        raise DocumentInspectionError("PDF parser failed; original file not modified") from exc


def _inspection_worker(pipe, gate, source: str, password: str | None, sample_limit: int,
                       max_pages: int, memory_bytes: int, cpu_seconds: int) -> None:
    """Untrusted pypdf code executes only after OS process limits are set."""
    try:
        if os.name == "nt":
            if gate is None or not gate.wait(15):
                return
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        import logging
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        result = _inspect_pdf_direct(source, password=password, sample_limit=sample_limit)
        if result.page_count is not None and result.page_count > max_pages:
            pipe.send(("error", "page-count-cap"))
        else:
            pipe.send(("ok", result))
    except BaseException:
        # Parser exception strings may contain private PDF data: never forward.
        try:
            pipe.send(("error", "parse-failed"))
        except (BrokenPipeError, EOFError, OSError, MemoryError):
            pass
    finally:
        pipe.close()


def inspect_pdf(source: str | Path, *, password: str | None = None,
                sample_limit: int = 4, timeout_seconds: float = 90.0,
                max_memory_mb: int = 768, max_pages: int = 10000) -> DocumentInspection:
    """Inspect an untrusted PDF in a bounded POSIX or Windows child.

    POSIX uses RLIMIT; Windows uses a pre-parse Job Object. This is resource
    containment, NOT a filesystem/syscall sandbox. Source remains read-only.
    """
    if not isinstance(sample_limit, int) or isinstance(sample_limit, bool) or not 0 <= sample_limit <= 32:
        raise ValueError("sample_limit must be an integer in 0..32")
    for name, value in (("max_memory_mb", max_memory_mb), ("max_pages", max_pages)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if os.name not in ("posix", "nt"):
        raise DocumentInspectionError("isolated inspection unavailable on this platform")
    if os.name == "posix":
        try:
            import resource
            if not hasattr(resource, "RLIMIT_AS") or not hasattr(resource, "RLIMIT_CPU"):
                raise ImportError("resource limits unavailable")
        except ImportError as exc:
            raise DocumentInspectionError("OS resource limits unavailable") from exc
    src = Path(source).resolve(strict=True)
    if not src.is_file():
        raise DocumentInspectionError("source must be a regular file")
    with src.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise DocumentInspectionError("missing PDF file header")
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    gate = context.Event() if os.name == "nt" else None
    try:
        job = WindowsJob(max_memory_mb * 1024 * 1024) if os.name == "nt" else None
    except WindowsJobError as exc:
        parent.close()
        child.close()
        raise DocumentInspectionError("Windows inspection limits unavailable") from exc
    process = context.Process(target=_inspection_worker,
                              args=(child, gate, str(src), password, sample_limit, max_pages,
                                    max_memory_mb * 1024 * 1024,
                                    max(2, math.ceil(timeout_seconds) + 1)),
                              daemon=True)
    # The wall-clock budget starts before process creation and Windows Job
    # Object assignment; otherwise slow startup can exceed the caller's timeout
    # before the parser deadline even begins.
    deadline = time.monotonic() + timeout_seconds
    try:
        process.start()
        child.close()
        if job is not None:
            try:
                job.assign(process.pid)
            except WindowsJobError as exc:
                raise DocumentInspectionError("Windows inspection worker could not be confined") from exc
            gate.set()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DocumentInspectionError("isolated PDF inspection timed out")
        if not parent.poll(remaining):
            raise DocumentInspectionError("isolated PDF inspection timed out")
        try:
            message = parent.recv()
        except (EOFError, OSError) as exc:
            raise DocumentInspectionError("PDF inspection worker terminated") from exc
        if not isinstance(message, tuple) or len(message) != 2 or message[0] != "ok":
            raise DocumentInspectionError("isolated PDF inspection rejected input")
        result = message[1]
        if not isinstance(result, DocumentInspection):
            raise DocumentInspectionError("invalid isolated inspection response")
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive() or process.exitcode != 0:
            raise DocumentInspectionError("isolated inspection worker did not exit cleanly")
        if time.monotonic() >= deadline:
            raise DocumentInspectionError("isolated PDF inspection timed out")
        try:
            verified_digest = sha256_file_before_deadline(src, deadline)
        except TimeoutError as exc:
            raise DocumentInspectionError(
                "isolated PDF inspection timed out during source verification"
            ) from exc
        except OSError as exc:
            raise DocumentInspectionError("PDF changed during inspection") from exc
        if verified_digest != result.sha256:
            raise DocumentInspectionError("PDF changed during inspection")
        if time.monotonic() >= deadline:
            raise DocumentInspectionError("isolated PDF inspection timed out")
        return result
    finally:
        child.close()
        parent.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
            process.join(timeout=1)
        process.close()
        if job is not None:
            job.close()

def _top_level_bookmark_worker(pipe, gate, source: str, max_pages: int,
                               max_bookmarks: int, memory_bytes: int,
                               cpu_seconds: int) -> None:
    """Read only bounded top-level outline destinations in a constrained child."""
    try:
        if os.name == "nt":
            if gate is None or not gate.wait(15):
                return
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        import logging
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        from pypdf import PdfReader
        from .page_tools import _preflight_reader

        with open(source, "rb") as stream:
            if stream.read(5) != b"%PDF-":
                pipe.send(("error", "header"))
                return
            stream.seek(0)
            reader = PdfReader(stream, strict=False)
            _preflight_reader(reader)
            page_count = len(reader.pages)
            if page_count < 1 or page_count > max_pages:
                pipe.send(("error", "page-count-cap"))
                return

            # pypdf represents descendants as nested lists. Only direct
            # destinations are used as chapter boundaries; nested headings do
            # not create unexpectedly tiny files. Titles never cross IPC or
            # become filenames, avoiding both unbounded text and path injection.
            starts = []
            for item in reader.outline:
                if isinstance(item, list):
                    continue
                if not hasattr(item, "title"):
                    pipe.send(("error", "invalid-outline"))
                    return
                page_index = reader.get_destination_page_number(item)
                if (not isinstance(page_index, int) or isinstance(page_index, bool)
                        or page_index < 0 or page_index >= page_count):
                    pipe.send(("error", "invalid-outline-destination"))
                    return
                starts.append(page_index)
                if len(starts) > max_bookmarks:
                    pipe.send(("error", "bookmark-count-cap"))
                    return
            unique_starts = tuple(sorted(set(starts)))
            if not unique_starts:
                pipe.send(("error", "no-top-level-bookmarks"))
                return
            pipe.send(("ok", page_count, unique_starts))
    except BaseException:
        # Never return parser exception strings: malformed PDFs may embed
        # document text or filesystem details in those messages.
        try:
            pipe.send(("error", "parse-failed"))
        except (BrokenPipeError, EOFError, OSError, MemoryError):
            pass
    finally:
        pipe.close()


def inspect_top_level_bookmark_starts(
        source: str | Path, *, max_pages: int = 10000, max_bookmarks: int = 100,
        timeout_seconds: float = 90.0, max_memory_mb: int = 768
) -> tuple[int, tuple[int, ...], str]:
    """Return bounded top-level bookmark page indexes and the pinned source SHA.

    Parsing happens in a child with POSIX resource limits or a Windows Job
    Object. This is resource containment, not a filesystem/syscall sandbox.
    The caller receives no bookmark titles or document text.
    """
    if os.name not in ("posix", "nt"):
        raise DocumentInspectionError("isolated bookmark inspection unavailable on this host")
    if os.name == "posix":
        try:
            import resource
            if not hasattr(resource, "RLIMIT_AS") or not hasattr(resource, "RLIMIT_CPU"):
                raise ImportError("resource limits unavailable")
        except ImportError as exc:
            raise DocumentInspectionError("OS resource limits unavailable") from exc
    for name, value in (("max_pages", max_pages), ("max_memory_mb", max_memory_mb)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (not isinstance(max_bookmarks, int) or isinstance(max_bookmarks, bool)
            or max_bookmarks < 1 or max_bookmarks > 100):
        raise ValueError("max_bookmarks must be in 1..100")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout_seconds must be finite and positive")

    deadline = time.monotonic() + float(timeout_seconds)
    src = Path(source).resolve(strict=True)
    if not src.is_file():
        raise DocumentInspectionError("source must be a regular file")
    with src.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise DocumentInspectionError("missing PDF file header")

    def fingerprint(path: Path) -> tuple[int, ...]:
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    try:
        before = fingerprint(src)
        source_digest = sha256_file(src)
        after = fingerprint(src)
    except OSError as exc:
        raise DocumentInspectionError("PDF became unreadable during bookmark preflight") from exc
    if before != after:
        raise DocumentInspectionError("PDF changed during bookmark preflight")
    if time.monotonic() >= deadline:
        raise DocumentInspectionError("isolated bookmark inspection timed out")

    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    gate = context.Event() if os.name == "nt" else None
    job = None
    process = None
    try:
        try:
            job = WindowsJob(max_memory_mb * 1024 * 1024) if os.name == "nt" else None
        except WindowsJobError as exc:
            raise DocumentInspectionError("Windows bookmark worker limits unavailable") from exc
        process = context.Process(
            target=_top_level_bookmark_worker,
            args=(child, gate, str(src), max_pages, max_bookmarks,
                  max_memory_mb * 1024 * 1024, max(2, math.ceil(timeout_seconds) + 1)),
            daemon=True,
        )
        process.start()
        child.close()
        if job is not None:
            try:
                job.assign(process.pid)
            except WindowsJobError as exc:
                raise DocumentInspectionError("Windows bookmark worker could not be confined") from exc
            gate.set()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not parent.poll(remaining):
            raise DocumentInspectionError("isolated bookmark inspection timed out")
        try:
            message = parent.recv()
        except (EOFError, OSError) as exc:
            raise DocumentInspectionError("bookmark worker terminated unexpectedly") from exc
        if (not isinstance(message, tuple) or len(message) != 3 or message[0] != "ok"):
            raise DocumentInspectionError("isolated PDF bookmark inspection rejected input")
        page_count, starts = message[1], message[2]
        if (not isinstance(page_count, int) or isinstance(page_count, bool)
                or page_count < 1 or page_count > max_pages
                or not isinstance(starts, tuple) or not starts
                or len(starts) > max_bookmarks
                or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= page_count
                       for i in starts)
                or tuple(sorted(set(starts))) != starts):
            raise DocumentInspectionError("invalid bounded bookmark response")
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive() or process.exitcode != 0:
            raise DocumentInspectionError("bookmark worker did not exit cleanly")
        try:
            current_before = fingerprint(src)
            current_digest = sha256_file(src)
            current_after = fingerprint(src)
        except OSError as exc:
            raise DocumentInspectionError("PDF changed during bookmark inspection") from exc
        if current_before != current_after or current_after != after or current_digest != source_digest:
            raise DocumentInspectionError("PDF changed during bookmark inspection")
        return page_count, starts, source_digest
    finally:
        child.close()
        parent.close()
        if process is not None:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                process.join(timeout=1)
            process.close()
        if job is not None:
            job.close()

