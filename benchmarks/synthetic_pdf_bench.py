"""Deterministic, privacy-safe PDF Studio B01 scale benchmark.

Creates born-digital synthetic pages only. Measurements are not OCR, hostile-PDF,
realistic 2GB, Android, or GUI performance evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def build_synthetic_pdf(path: Path, pages: int, chars_per_page: int) -> None:
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise ValueError("pages must be positive")
    if isinstance(chars_per_page, bool) or not isinstance(chars_per_page, int) or not 64 <= chars_per_page <= 8192:
        raise ValueError("chars_per_page must be in 64..8192")
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for number in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        prefix = f"PDFSTUDIO page{number:06d} "
        payload = (prefix + ("synthetic benchmark data " * (chars_per_page // 25 + 1)))[:chars_per_page]
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 10 Tf 40 730 Td (" + payload + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Synthetic PDF Studio scale benchmark"})
    with path.open("wb") as output:
        writer.write(output)


def _rss_mb(who: int) -> float | None:
    if os.name != "posix":
        return None
    import resource
    rss = resource.getrusage(who).ru_maxrss
    return round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)


def run_benchmark(pages: int, chars_per_page: int, timeout_seconds: float) -> dict:
    from pdfstudio.inspection import inspect_pdf
    from pdfstudio.jobs import sha256_file
    from pdfstudio.library_index import LibraryIndex

    with tempfile.TemporaryDirectory(prefix="pdfstudio-bench-") as temp:
        pdf = Path(temp) / "synthetic.pdf"
        db = Path(temp) / "index.sqlite"
        start = time.perf_counter()
        build_synthetic_pdf(pdf, pages, chars_per_page)
        generation_seconds = time.perf_counter() - start
        digest = sha256_file(pdf)
        start = time.perf_counter()
        inspected = inspect_pdf(pdf, sample_limit=3, timeout_seconds=timeout_seconds, max_pages=pages)
        inspection_seconds = time.perf_counter() - start
        if inspected.page_count != pages or not all(p.has_extracted_text for p in inspected.sampled_pages):
            raise AssertionError("inspection page count or native text mismatch")
        index = LibraryIndex(db, max_pages=pages, max_total_chars=max(20_000_000, pages * (chars_per_page + 64)))
        start = time.perf_counter()
        result = index.index_pdf(pdf)
        indexing_seconds = time.perf_counter() - start
        if result.indexed_pages != pages:
            raise AssertionError("not all pages were indexed")
        hits = index.search("pdfstudio", limit=10)
        if len(hits) != min(pages, 10):
            raise AssertionError("FTS search did not find synthetic page text")
        index.integrity_check()
        if sha256_file(pdf) != digest:
            raise AssertionError("original PDF changed")
        if os.name == "posix":
            import resource
            parent_rss = _rss_mb(resource.RUSAGE_SELF)
            child_rss = _rss_mb(resource.RUSAGE_CHILDREN)
        else:
            parent_rss = child_rss = None
        return {
            "status": "PASS", "platform": sys.platform, "pages": pages,
            "chars_per_page": chars_per_page, "pdf_bytes": pdf.stat().st_size,
            "sqlite_bytes": db.stat().st_size, "generation_seconds": round(generation_seconds, 3),
            "inspection_seconds": round(inspection_seconds, 3),
            "indexing_seconds": round(indexing_seconds, 3),
            "parent_peak_rss_mb": parent_rss, "max_child_peak_rss_mb": child_rss,
            "rss_scope": "POSIX process high-water marks; max child, not sum; unavailable on Windows",
            "pdf_unchanged": True, "fts_integrity": "PASS", "indexed_pages": result.indexed_pages,
            "limitations": "synthetic native text only; not OCR, 2GB, hostile PDF, Android, or GUI benchmark",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--chars-per-page", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("timeout must be positive")
    print(json.dumps(run_benchmark(args.pages, args.chars_per_page, args.timeout_seconds), sort_keys=True))


if __name__ == "__main__":
    main()
