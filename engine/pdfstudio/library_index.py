"""Offline page-level SQLite FTS5 index for PDF Studio B01.

Stores text locally with source SHA-256 provenance; does not run OCR.
Native text/page-count parsing uses a bounded POSIX worker or Windows Job Object;\nAndroid requires independently validated platform isolation before untrusted input.
The caller must store the index in application-private storage.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .jobs import SourceChangedError, sha256_file
from .search import normalize_arabic_search

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


class LibraryIndexError(ValueError):
    """Indexing failed; any previous index remains intact."""


class LibraryIndexCancelled(LibraryIndexError):
    """Cooperative cancellation; all uncommitted FTS rows are rolled back."""


@dataclass(frozen=True)
class IndexResult:
    source: str
    source_sha256: str
    page_count: int
    indexed_pages: int
    changed: bool
    provenance: str


@dataclass(frozen=True)
class SearchHit:
    source: str
    source_sha256: str
    page_number: int  # 1-based
    preview: str  # original prefix, NOT a match-positioned snippet
    provenance: str  # native or ocr


class LibraryIndex:
    """Transactional, source-pinned library search.

    Text/page caps bound indexed output, not parser allocations. pypdf may
    decompress a hostile content stream before a size check can be applied.
    """

    def __init__(self, database: str | Path, *, max_pages: int = 10000,
                 max_chars_per_page: int = 500000, max_total_chars: int = 20000000):
        for name, value in (("max_pages", max_pages),
                            ("max_chars_per_page", max_chars_per_page),
                            ("max_total_chars", max_total_chars)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.database = Path(database)
        self.max_pages = max_pages
        self.max_chars_per_page = max_chars_per_page
        self.max_total_chars = max_total_chars
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS library_documents (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL UNIQUE,
                    source_sha256 TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    indexed_pages INTEGER NOT NULL,
                    provenance TEXT NOT NULL CHECK(provenance IN ('native', 'ocr')),
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_pages (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES library_documents(id) ON DELETE CASCADE,
                    page_index INTEGER NOT NULL,
                    search_text TEXT NOT NULL,
                    preview TEXT NOT NULL,
                    UNIQUE(document_id, page_index)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5(
                    search_text, content='library_pages', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS library_pages_ai AFTER INSERT ON library_pages BEGIN
                    INSERT INTO library_fts(rowid, search_text) VALUES(new.id, new.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS library_pages_ad AFTER DELETE ON library_pages BEGIN
                    INSERT INTO library_fts(library_fts, rowid, search_text)
                    VALUES('delete', old.id, old.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS library_pages_au AFTER UPDATE ON library_pages BEGIN
                    INSERT INTO library_fts(library_fts, rowid, search_text)
                    VALUES('delete', old.id, old.search_text);
                    INSERT INTO library_fts(rowid, search_text) VALUES(new.id, new.search_text);
                END;
            """)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _source(self, source: str | Path) -> Path:
        src = Path(source).resolve(strict=True)
        if not src.is_file():
            raise LibraryIndexError("source must be a regular file")
        with src.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise LibraryIndexError("missing PDF header")
        return src

    @staticmethod
    def _check_cancel(cancel_requested: Callable[[], bool] | None) -> None:
        if cancel_requested is not None and cancel_requested():
            raise LibraryIndexCancelled("indexing cancelled; previous index retained")

    def _commit_pages(self, src: Path, digest: str, count: int,
                      pages: Iterable[tuple[int, str]], provenance: str,
                      cancel_requested: Callable[[], bool] | None = None) -> IndexResult:
        self._check_cancel(cancel_requested)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= self.max_pages:
            raise LibraryIndexError("page count outside configured index bounds")
        total = 0
        seen: set[int] = set()
        with self._open() as db:
            previous = db.execute("SELECT * FROM library_documents WHERE source=?", (str(src),)).fetchone()
            # OCR corrections must always be reindexed, even when the source PDF
            # has not changed. Native text can safely reuse an unchanged index.
            if provenance == "native" and previous and previous["source_sha256"] == digest \
                    and previous["provenance"] == provenance and previous["page_count"] == count \
                    and previous["indexed_pages"] == count:
                if sha256_file(src) != digest:
                    raise SourceChangedError("source PDF changed during indexing")
                self._check_cancel(cancel_requested)
                return IndexResult(str(src), digest, count, count, False, provenance)
            if previous:
                db.execute("DELETE FROM library_pages WHERE document_id=?", (previous["id"],))
                doc_id = previous["id"]
                db.execute("""UPDATE library_documents SET source_sha256=?, source_size=?, page_count=?,
                    indexed_pages=0, provenance=?, indexed_at=? WHERE id=?""",
                    (digest, src.stat().st_size, count, provenance,
                     datetime.now(timezone.utc).isoformat(), doc_id))
            else:
                cur = db.execute("""INSERT INTO library_documents
                    (source, source_sha256, source_size, page_count, indexed_pages, provenance, indexed_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)""",
                    (str(src), digest, src.stat().st_size, count, provenance,
                     datetime.now(timezone.utc).isoformat()))
                doc_id = cur.lastrowid
            for index, text in pages:
                self._check_cancel(cancel_requested)
                if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= count or index in seen:
                    raise LibraryIndexError("invalid or repeated page index")
                if not isinstance(text, str):
                    raise LibraryIndexError("page text must be Unicode")
                if len(text) > self.max_chars_per_page:
                    raise LibraryIndexError("page text exceeds configured cap")
                total += len(text)
                if total > self.max_total_chars:
                    raise LibraryIndexError("document text exceeds configured cap")
                seen.add(index)
                db.execute("""INSERT INTO library_pages(document_id, page_index, search_text, preview)
                    VALUES (?, ?, ?, ?)""", (doc_id, index, normalize_arabic_search(text), text[:240]))
            if sha256_file(src) != digest:
                raise SourceChangedError("source PDF changed during indexing")
            db.execute("UPDATE library_documents SET indexed_pages=? WHERE id=?", (len(seen), doc_id))
            self._check_cancel(cancel_requested)  # still inside the transaction
            return IndexResult(str(src), digest, count, len(seen), True, provenance)

    def index_pdf(self, source: str | Path, *,
                  cancel_requested: Callable[[], bool] | None = None) -> IndexResult:
        """Index native text in a bounded child; cancellation rolls back FTS."""
        if cancel_requested is not None and not callable(cancel_requested):
            raise TypeError("cancel_requested must be callable")
        self._check_cancel(cancel_requested)
        from .bounded_parser import IsolatedPdfReader, ParserIsolationError
        src = self._source(source)
        digest = sha256_file(src)
        try:
            with IsolatedPdfReader(src, max_pages=self.max_pages,
                                   max_chars_per_page=self.max_chars_per_page,
                                   max_total_chars=self.max_total_chars) as reader:
                return self._commit_pages(src, digest, reader.page_count,
                                          reader.pages(), "native", cancel_requested)
        except (LibraryIndexError, SourceChangedError):
            raise
        except ParserIsolationError as exc:
            raise LibraryIndexError("isolated PDF extraction rejected input: " + str(exc)) from exc
        except Exception as exc:
            raise LibraryIndexError("PDF extraction failed; previous index retained") from exc

    def index_ocr_text(self, source: str | Path, page_count: int,
                       pages: Iterable[tuple[int, str]], *, expected_sha256: str,
                       cancel_requested: Callable[[], bool] | None = None) -> IndexResult:
        """Ingest trusted OCR text after bounded source page-count verification."""
        if cancel_requested is not None and not callable(cancel_requested):
            raise TypeError("cancel_requested must be callable")
        self._check_cancel(cancel_requested)
        from .bounded_parser import IsolatedPdfReader, ParserIsolationError
        src = self._source(source)
        digest = sha256_file(src)
        if digest != expected_sha256:
            raise SourceChangedError("OCR text belongs to a different source PDF")
        try:
            with IsolatedPdfReader(src, max_pages=self.max_pages,
                                   max_chars_per_page=self.max_chars_per_page,
                                   max_total_chars=self.max_total_chars,
                                   extract_text=False) as reader:
                actual_pages = reader.page_count
                list(reader.pages())  # require the worker's successful end marker
        except ParserIsolationError as exc:
            raise LibraryIndexError("cannot verify OCR source pages: " + str(exc)) from exc
        except Exception as exc:
            raise LibraryIndexError("cannot verify OCR page count") from exc
        if actual_pages != page_count:
            raise LibraryIndexError("OCR page count differs from the source PDF")
        return self._commit_pages(src, digest, page_count, pages, "ocr", cancel_requested)

    def search(self, query: str, *, limit: int = 25) -> list[SearchHit]:
        """Match all normalized terms; user cannot inject raw FTS syntax."""
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be in 1..100")
        if len(query) > 512:
            raise ValueError("query too long")
        terms = _WORDS.findall(normalize_arabic_search(query))
        if len(terms) > 32:
            raise ValueError("too many search terms")
        if not terms:
            return []
        expression = " AND ".join(f'"{term}"' for term in terms)
        with self._open() as db:
            rows = db.execute("""SELECT d.source, d.source_sha256, d.provenance,
                p.page_index, p.preview FROM library_fts
                JOIN library_pages p ON p.id=library_fts.rowid
                JOIN library_documents d ON d.id=p.document_id
                WHERE library_fts MATCH ? ORDER BY bm25(library_fts), d.source, p.page_index LIMIT ?""",
                (expression, limit)).fetchall()
        return [SearchHit(r["source"], r["source_sha256"], r["page_index"] + 1,
                          r["preview"], r["provenance"]) for r in rows]

    def verify_source(self, source: str | Path) -> bool:
        """Reject stale search provenance before opening a result."""
        src = Path(source).resolve()
        with self._open() as db:
            row = db.execute("SELECT source_sha256 FROM library_documents WHERE source=?", (str(src),)).fetchone()
        if row is None:
            raise KeyError(str(src))
        try:
            current = sha256_file(src)
        except OSError as exc:
            raise SourceChangedError("indexed source missing or unreadable") from exc
        if current != row["source_sha256"]:
            raise SourceChangedError("indexed source changed; reindex before opening")
        return True

    def remove(self, source: str | Path) -> bool:
        """Forget indexed text without modifying the original PDF."""
        src = str(Path(source).resolve())
        with self._open() as db:
            row = db.execute("SELECT id FROM library_documents WHERE source=?", (src,)).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM library_pages WHERE document_id=?", (row["id"],))
            db.execute("DELETE FROM library_documents WHERE id=?", (row["id"],))
        return True

    def integrity_check(self) -> None:
        """Check FTS5 consistency with the external content table."""
        with self._open() as db:
            db.execute("INSERT INTO library_fts(library_fts, rank) VALUES('integrity-check', 1)")
