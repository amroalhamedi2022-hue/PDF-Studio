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


def run_benchmark(pages: int, chars_per_page: int, timeout_seconds: float,
                  checkpoint_resume: bool = False) -> dict:
    from pdfstudio.inspection import inspect_pdf
    from pdfstudio.jobs import JobJournal, sha256_file
    from pdfstudio.library_index import LibraryIndex
    from pdfstudio.native_text_stage import run_native_text_stage

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

        checkpoint_metrics = {}
        if checkpoint_resume:
            # Exercise the real SQLite journal, bounded native-text parser,
            # immutable per-page artifacts, resume verification and FTS bridge.
            # This is deliberately born-digital synthetic data, not OCR or 2GB.
            journal = JobJournal(Path(temp) / "jobs.sqlite")
            job_id = journal.create_job(pdf, Path(temp) / "checkpoint-output.pdf", pages)
            artifacts = Path(temp) / "checkpoint-artifacts"
            total_chars_cap = max(20_000_000, pages * (chars_per_page + 64))

            started = time.perf_counter()
            first_stage = run_native_text_stage(
                journal, job_id, artifacts, max_pages=pages,
                max_chars_per_page=max(500000, chars_per_page + 64),
                max_total_chars=total_chars_cap, timeout_seconds=timeout_seconds,
            )
            checkpoint_stage_seconds = time.perf_counter() - started
            if first_stage["cancelled"] or len(first_stage["processed"]) != pages:
                raise AssertionError("checkpoint stage did not process every synthetic page")

            started = time.perf_counter()
            resumed_stage = run_native_text_stage(
                journal, job_id, artifacts, max_pages=pages,
                max_chars_per_page=max(500000, chars_per_page + 64),
                max_total_chars=total_chars_cap, timeout_seconds=timeout_seconds,
            )
            checkpoint_resume_seconds = time.perf_counter() - started
            if (resumed_stage["cancelled"] or resumed_stage["processed"]
                    or len(resumed_stage["skipped"]) != pages):
                raise AssertionError("resume did not reuse every verified page checkpoint")
            snapshot = journal.recovery_snapshot(job_id, "native_text")
            if len(snapshot["valid_pages"]) != pages or snapshot["invalid_pages"] or snapshot["missing_pages"]:
                raise AssertionError("checkpoint audit found missing or invalid artifacts")

            checkpoint_index = LibraryIndex(
                Path(temp) / "checkpoint-index.sqlite", max_pages=pages,
                max_chars_per_page=max(500000, chars_per_page + 64),
                max_total_chars=total_chars_cap,
            )
            started = time.perf_counter()
            checkpoint_index_result = checkpoint_index.index_native_text_checkpoints(journal, job_id)
            checkpoint_index_seconds = time.perf_counter() - started
            checkpoint_index.integrity_check()
            if checkpoint_index_result.indexed_pages != pages:
                raise AssertionError("checkpoint FTS bridge did not index every page")
            if len(checkpoint_index.search("pdfstudio", limit=10)) != min(pages, 10):
                raise AssertionError("checkpoint FTS search missed synthetic page text")
            if sha256_file(pdf) != digest:
                raise AssertionError("checkpoint/resume changed the original PDF")
            checkpoint_metrics = {
                "checkpoint_stage_seconds": round(checkpoint_stage_seconds, 3),
                "checkpoint_resume_seconds": round(checkpoint_resume_seconds, 3),
                "checkpoint_index_seconds": round(checkpoint_index_seconds, 3),
                "checkpoint_artifacts": len(snapshot["valid_pages"]),
                "checkpoint_resumed_pages": len(resumed_stage["skipped"]),
                "checkpoint_indexed_pages": checkpoint_index_result.indexed_pages,
                "checkpoint_index_bytes": (Path(temp) / "checkpoint-index.sqlite").stat().st_size,
                "checkpoint_artifact_bytes": sum(p.stat().st_size for p in artifacts.glob("page-*.txt")),
                "checkpoint_resume_verified": True,
                "checkpoint_source_unchanged": True,
            }
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
            **checkpoint_metrics,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--chars-per-page", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--checkpoint-resume", action="store_true",
                        help="also benchmark real per-page SQLite checkpointing, resume, and checkpoint-to-FTS indexing")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("timeout must be positive")
    print(json.dumps(run_benchmark(
        args.pages, args.chars_per_page, args.timeout_seconds,
        checkpoint_resume=args.checkpoint_resume,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
