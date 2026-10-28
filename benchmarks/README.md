# Acceptance benchmarks

Track CER/WER for Arabic, English and mixed documents; page counts, hashes, peak memory, disk usage, OCR pages/minute, restart recovery and output visual fidelity.

Fixture tiers: 10/100/1000/5000 pages and >=2 GB when test hardware permits. Never label a tier PASS until it was actually measured.


### Reproducible B01 synthetic scale checks
Run from repository root with `PYTHONPATH=engine python benchmarks/synthetic_pdf_bench.py --pages 100` (Linux/macOS) or set `PYTHONPATH=engine` on Windows. CI runs 100 pages on both OSes and 1000 pages on Linux.

For the full checkpoint path, run `PYTHONPATH=engine python benchmarks/synthetic_pdf_bench.py --pages 5000 --chars-per-page 256 --timeout-seconds 300 --checkpoint-resume`. The extra flag starts the real bounded native-text stage in a child process and abruptly terminates that stage parent (exit code 23) after 2,500 durable checkpoints. After the child is joined, the benchmark audits the interrupted journal, resumes only missing pages, reruns the completed stage to prove all 5,000 pages are reused, then ingests artifacts into a separate FTS5 index and checks search/FTS integrity and source SHA. JSON records generation, inspection, native indexing, crash, resume, complete-reuse and checkpoint-index timings; PDF/SQLite/artifact sizes; and POSIX process high-water RSS. The max-child RSS is **not** combined process RSS; Windows RSS is unavailable.

These are reproducible **born-digital synthetic** tests. Even a PASS at 5000 pages does **not** establish OCR quality, hostile-file sandboxing, a realistic 2GB-file result, Android/Windows UI behavior, or device acceptance. No user's files are included.
