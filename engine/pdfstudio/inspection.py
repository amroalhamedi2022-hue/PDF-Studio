"""B01 read-only PDF preflight; bounded sampling, no OCR or rendering.

Use in a constrained worker when inspecting untrusted PDFs. This module does
not by itself sandbox parsers or guarantee bounded memory for malicious PDFs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .jobs import sha256_file


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


def inspect_pdf(source: str | Path, *, password: str | None = None, sample_limit: int = 4) -> DocumentInspection:
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
            title = str(metadata.title) if metadata and metadata.title else None
            author = str(metadata.author) if metadata and metadata.author else None
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
