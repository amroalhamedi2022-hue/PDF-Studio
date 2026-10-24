# Source Dependencies

## B01 - pypdf
- Pinned version: 6.21.2 (2026-10-21 release, documented security hardening).
- Source: https://github.com/py-pdf/pypdf/releases/tag/6.21.2
- SPDX: BSD-3-Clause (retain notices in release bundle).
- Uses: read-only page enumeration, metadata, geometry and sampled text extraction. **Not** render, OCR or text editing.
- Unit tests: tests/test_inspection.py (valid synthetic pages and encryption).
- Runtime threat: arbitrary PDF parser and decompression; **isolate untrusted PDFs in resource-capped subprocess before accepting F01.01**.
- Environment mismatch: tests first run locally on installed pypdf 5.9.0; CI validates selected pinned version; don't conflate results.
