"""Bounded native PDF text extraction for B01 on POSIX and Windows hosts.

Runs pypdf in a separate *resource-limited* process. This is NOT an OS
security sandbox: no syscall/filesystem confinement. Windows uses a Job
Object assigned before the worker can parse; unsupported hosts fail closed.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Iterator

from .windows_job import WindowsJob, WindowsJobError


class ParserIsolationError(ValueError):
    """Untrusted PDF parsing did not complete within configured limits."""


def _parse_worker(pipe, gate, filename: str, max_pages: int, page_chars: int,
                  total_chars: int, memory_bytes: int, cpu_seconds: int,
                  extract_text: bool, selected_pages: tuple[int, ...] | None) -> None:
    try:
        # Import pypdf only *after* limits have been applied.
        if os.name == "nt":
            # The parent assigns the memory-capped Job Object before release.
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

        with open(filename, "rb") as stream:
            if stream.read(5) != b"%PDF-":
                pipe.send(("error", "header"))
                return
            stream.seek(0)
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted:
                pipe.send(("error", "encrypted"))
                return
            count = len(reader.pages)
            if count < 1 or count > max_pages:
                pipe.send(("error", "page-count"))
                return
            if selected_pages is not None and any(i >= count for i in selected_pages):
                pipe.send(("error", "page-selection"))
                return
            pipe.send(("count", count))
            if extract_text:
                accumulated = 0
                indexes = range(count) if selected_pages is None else selected_pages
                for index in indexes:
                    page = reader.pages[index]
                    text = page.extract_text() or ""
                    if not isinstance(text, str) or len(text) > page_chars:
                        pipe.send(("error", "page-text-cap"))
                        return
                    accumulated += len(text)
                    if accumulated > total_chars:
                        pipe.send(("error", "total-text-cap"))
                        return
                    pipe.send(("page", index, text))
            pipe.send(("done",))
    except BaseException:
        # Do not expose exception messages: PDFs may contain private content.
        try:
            pipe.send(("error", "parser"))
        except (BrokenPipeError, EOFError, OSError, MemoryError):
            pass
    finally:
        pipe.close()


class IsolatedPdfReader:
    """Spawn-only, time-limited page text reader with bounded IPC payloads.

    POSIX RLIMIT_AS/CPU or a pre-parse Windows Job Object are required.
    Neither mechanism confines filesystem access or provides a full sandbox.
    The caller must rehash the source before committing extracted results.
    """

    def __init__(self, source: str | Path, *, max_pages: int,
                 max_chars_per_page: int, max_total_chars: int,
                 timeout_seconds: float = 90.0, max_memory_mb: int = 768,
                 extract_text: bool = True,
                 page_indexes: tuple[int, ...] | list[int] | None = None):
        if os.name not in ("posix", "nt"):
            raise ParserIsolationError("memory-capped PDF parsing is not available on this host")
        if os.name == "posix":
            try:
                import resource
                if not hasattr(resource, "RLIMIT_AS") or not hasattr(resource, "RLIMIT_CPU"):
                    raise ImportError("resource limits unavailable")
            except ImportError as exc:
                raise ParserIsolationError("OS resource limits unavailable") from exc
        for key, value in (("max_pages", max_pages),
                           ("max_chars_per_page", max_chars_per_page),
                           ("max_total_chars", max_total_chars),
                           ("max_memory_mb", max_memory_mb)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if page_indexes is not None:
            if not isinstance(page_indexes, (tuple, list)) or any(
                not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= max_pages
                for i in page_indexes
            ):
                raise ValueError("page_indexes must be a sequence of nonnegative indexes below max_pages")
            if len(set(page_indexes)) != len(page_indexes):
                raise ValueError("page_indexes cannot contain duplicates")
            page_indexes = tuple(sorted(page_indexes))
        self.page_indexes = page_indexes
        self.source = Path(source)
        self.max_pages = max_pages
        self.page_chars = max_chars_per_page
        self.total_chars = max_total_chars
        self.timeout = float(timeout_seconds)
        self.memory_bytes = max_memory_mb * 1024 * 1024
        self.extract_text = extract_text
        self.page_count: int | None = None
        self._pipe = None
        self._process = None
        self._job = None
        self._deadline = 0.0
        self._consumed = False

    def __enter__(self) -> "IsolatedPdfReader":
        ctx = mp.get_context("spawn")  # never fork a GUI process or SQLite handle
        parent, child = ctx.Pipe(duplex=False)
        gate = ctx.Event() if os.name == "nt" else None
        try:
            job = WindowsJob(self.memory_bytes) if os.name == "nt" else None
        except WindowsJobError as exc:
            parent.close()
            child.close()
            raise ParserIsolationError("Windows PDF parser limits unavailable") from exc
        proc = ctx.Process(target=_parse_worker,
                           args=(child, gate, str(self.source), self.max_pages,
                                 self.page_chars, self.total_chars,
                                 self.memory_bytes, max(2, math.ceil(self.timeout) + 1),
                                 self.extract_text, self.page_indexes), daemon=True)
        self._pipe = parent
        self._process = proc
        self._job = job
        self._deadline = time.monotonic() + self.timeout
        try:
            proc.start()
            child.close()
            if job is not None:
                try:
                    job.assign(proc.pid)
                except WindowsJobError as exc:
                    raise ParserIsolationError("Windows PDF parser could not be confined") from exc
                gate.set()
            response = self._receive()
            if response[0] != "count":
                raise ParserIsolationError("parser did not return a page count")
            self.page_count = response[1]
            return self
        except BaseException:
            child.close()
            self.__exit__(None, None, None)
            raise

    def _receive(self) -> tuple:
        if self._pipe is None or self._process is None:
            raise ParserIsolationError("parser worker is not active")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0 or not self._pipe.poll(remaining):
            raise ParserIsolationError("PDF parser timed out")
        try:
            response = self._pipe.recv()
        except (EOFError, OSError) as exc:
            raise ParserIsolationError("PDF parser terminated unexpectedly") from exc
        if not isinstance(response, tuple) or not response:
            raise ParserIsolationError("invalid PDF worker response")
        if response[0] == "error":
            raise ParserIsolationError("PDF parser rejected input: " + str(response[1]))
        return response

    def pages(self) -> Iterator[tuple[int, str]]:
        if self._consumed:
            raise ParserIsolationError("PDF worker stream may be consumed once")
        self._consumed = True
        if not self.extract_text:
            response = self._receive()
            if response[0] != "done":
                raise ParserIsolationError("invalid count-only PDF response")
            self._require_clean_exit()
            return
        expected_indexes = range(self.page_count or 0) if self.page_indexes is None else self.page_indexes
        for expected in expected_indexes:
            response = self._receive()
            if len(response) != 3 or response[0] != "page" or response[1] != expected:
                raise ParserIsolationError("PDF worker page sequence invalid")
            yield expected, response[2]
        if self._receive() != ("done",):
            raise ParserIsolationError("PDF worker did not finish cleanly")
        self._require_clean_exit()

    def _require_clean_exit(self) -> None:
        if self._process is None:
            raise ParserIsolationError("PDF worker is not active")
        self._process.join(timeout=max(0.0, self._deadline - time.monotonic()))
        if self._process.is_alive() or self._process.exitcode != 0:
            raise ParserIsolationError("PDF parser worker did not exit cleanly")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._pipe is not None:
            self._pipe.close()
            self._pipe = None
        try:
            if self._process is not None:
                if self._process.pid is not None:
                    if self._process.is_alive():
                        self._process.terminate()
                        self._process.join(timeout=1)
                        if self._process.is_alive():
                            self._process.kill()
                    self._process.join(timeout=1)
                self._process.close()
                self._process = None
        finally:
            if self._job is not None:
                self._job.close()
                self._job = None
