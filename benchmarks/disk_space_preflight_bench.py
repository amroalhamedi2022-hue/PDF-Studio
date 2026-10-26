"""Disk-backed PDF output preflight/reservation smoke benchmark.

Uses only a generated PDF with a large valid comment stream. This checks
filesystem allocation and no-clobber extraction, not OCR or a realistic scan.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from pdfstudio.jobs import sha256_file
from pdfstudio.page_tools import extract_pages
from pdfstudio.page_isolation import _estimate_output_bytes

MIB = 1024 * 1024


def build_disk_backed_pdf(path: Path, size_mib: int) -> None:
    if isinstance(size_mib, bool) or not isinstance(size_mib, int) or not 8 <= size_mib <= 128:
        raise ValueError("size_mib must be an integer in 8..128")
    target = size_mib * MIB
    line = b"% PDF Studio disk-space preflight benchmark payload\n"
    payload = (line * ((target // len(line)) + 1))[:target]

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(payload)
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Generated disk-space preflight benchmark"})
    with path.open("wb") as output:
        writer.write(output)


def run(size_mib: int, timeout_seconds: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="pdfstudio-disk-space-") as temp:
        root = Path(temp)
        source = root / "source-large.pdf"
        output = root / "extracted.pdf"
        build_disk_backed_pdf(source, size_mib)
        input_bytes = source.stat().st_size
        source_sha_before = sha256_file(source)
        free_before = shutil.disk_usage(root).free
        output_cap = 256 * MIB
        estimated = _estimate_output_bytes((source,), output_cap)

        started = time.perf_counter()
        extract_pages(
            source, output, "1",
            timeout_seconds=timeout_seconds,
            max_memory_mb=768,
            max_output_bytes=output_cap,
            free_space_margin_bytes=16 * MIB,
        )
        elapsed = time.perf_counter() - started

        with output.open("rb") as stream:
            page_count = len(PdfReader(stream, strict=False).pages)
        source_unchanged = sha256_file(source) == source_sha_before
        output_bytes = output.stat().st_size
        free_after = shutil.disk_usage(root).free
        leftovers = list(root.glob(".pdfstudio-stage-*"))

        result = {
            "status": "PASS",
            "size_mib_requested": size_mib,
            "input_bytes": input_bytes,
            "estimated_output_reservation_bytes": estimated,
            "free_space_margin_bytes": 16 * MIB,
            "free_before_bytes": free_before,
            "output_bytes": output_bytes,
            "free_after_bytes": free_after,
            "elapsed_seconds": round(elapsed, 3),
            "output_page_count": page_count,
            "source_sha256_unchanged": source_unchanged,
            "staging_leftovers": len(leftovers),
            "fixture": "synthetic born-digital PDF with a large comment stream; not OCR/scanned",
        }
        if output_bytes < size_mib * MIB or page_count != 1 or not source_unchanged or leftovers:
            result["status"] = "FAIL"
            raise RuntimeError(json.dumps(result, sort_keys=True))
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mib", type=int, default=64)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    print(json.dumps(run(args.size_mib, args.timeout_seconds), sort_keys=True))


if __name__ == "__main__":
    main()
