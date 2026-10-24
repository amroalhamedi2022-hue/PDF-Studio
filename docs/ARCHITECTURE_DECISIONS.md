# B00 — Architecture Decisions (ADR)

## ADR-001 — Monorepo and separate adapters (provisional, 2026-10-24)
**Context:** one product, Android + Windows, offline-first, extremely large PDFs; native Android and local Windows Python tooling differ.
**Decision:** shared versioned JSON job schema and SQLite-compatible journal concepts, platform-specific UI/adapters, no shared dynamic-language runtime forced into Android. B00 Python engine/pdfstudio is a working *Windows/host proof of concept*, not a claim of Android implementation. Never embed large VL models in Android by default.
**Consequence:** duplicate platform adapter implementations are isolated behind contract tests; performance and license decisions remain benchmark gates.

## ADR-002 — Integrity-first PDF output
**Decision:** copy-on-write outputs, source SHA-256 pinning, streamed hashes, immutable page artifact hashes, durable SQLite checkpoint metadata, resumable stages and atomic final assembly later. No overwrite without explicit confirmation. Signature and encryption preflight before edits.
**B00 evidence:** engine/pdfstudio/jobs.py + tests/test_foundation.py; page processing and atomic PDF output not implemented.

## ADR-003 — OCR selection (conditional)
**Decision:** Tesseract+OCRmyPDF baseline on Windows for searchable text; Arabic-capable PaddleOCR model and Docling/Paddle layout are research candidates; use alternate engines for genuinely weak pages. Android Tesseract port benchmark needed. No code or model copied in B00.
**Risks:** license/model compliance, Arabic RTL order, script detection, CPU/RAM consumption.

## ADR-004 — Windows UX toolkit not yet selected
**Candidates:** Qt/PySide6 vs native .NET/WPF with Python subprocess for heavy processing.
**Selection criteria:** full offline operation, file throughput, signed installers, accessibility, bilingual RTL, licensing, maintenance, safe subprocess boundaries and resource caps. Pick in B01 after measured spike.

## ADR-005 — Android UX foundation
**Candidate:** Kotlin + Compose with Android SAF for documents, platform PDF viewer where viable, bounded JNI OCR bridge. Android app never assumes unrestricted whole-storage access. Tested on Xiaomi Pad 5 later, not in B00.

## ADR-006 — Data schema future contract
Initial entities: Document, Page, TextSpan, BoundingBox, LayoutBlock, Table, Formula, Image, Annotation, EditOperation, Job, Checkpoint, ExportProfile. Every OCR item must include source page/geometry/provenance, normalized search keys separately from original text. Migration/versioning will be defined before B01 persistence.
