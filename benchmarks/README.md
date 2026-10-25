# Acceptance benchmarks

Track CER/WER for Arabic, English and mixed documents; page counts, hashes, peak memory, disk usage, OCR pages/minute, restart recovery and output visual fidelity.

Fixture tiers: 10/100/1000/5000 pages and >=2 GB when test hardware permits. Never label a tier PASS until it was actually measured.


### Reproducible B01 synthetic scale check
Run from repository root with `PYTHONPATH=engine python benchmarks/synthetic_pdf_bench.py --pages 100` (Linux/macOS) or set `PYTHONPATH=engine` on Windows. Repeat with `--pages 1000`. JSON includes generated PDF size, SQLite index size, generation/inspection/indexing seconds, source-integrity/FTS checks and POSIX process high-water RSS. The max-child RSS is **not** combined process RSS; Windows RSS is unavailable. These tiny born-digital synthetic files are **not** 2GB/OCR/hostile-file benchmarks. CI runs 100 pages on both OSes and 1000 on Linux. No user's files are included.
