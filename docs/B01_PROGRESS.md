# B01 — First PDF Core Slice (IN PROGRESS)

## Delivered source
- Read-only inspection: actual PDF parsing, page count, page size, SHA-256, title/author, encryption/password gating, bounded text-layer sampling.
- Invalid/malformed PDF rejection with no writes to the input.
- Synthetic fixtures generated in memory using pypdf (no private PDF files committed).
- 5 inspection tests for valid blank PDF, text layer, wrong/correct password, malformed input and sample-limit guard.
- 7 page operation tests: reordered extraction, merge fidelity/count, no-clobber existing output, invalid selections, source-overwrite refusal, encrypted file refusal, signature-permission refusal.
- Atomic no-clobber publish uses same-directory temporary file and os.link; filesystems lacking hardlinks fail safely.
- pypdf pinned to 6.21.2 for CI, the published Oct 21 2026 security-release; host test environment used pypdf 5.9.0 pending CI on the pin.

## Known exclusions
Not yet implemented: advanced split/reorder UI, complete signature validation, large-file streaming merge, rendering, thumbnails, editing, library/index, full-text search across documents, safe parser subprocess isolation, signatures/attachments introspection, billion-object malformed-document caps, genuine 1000+/5000-page performance, Android integration, Windows native UI. These remain F01/F08 TODO; do not mark PASS from the preflight-only spike.

## Tests
- Host synthetic suite: B00 8 + B01 12 = 20 PASS (Python with local pypdf 5.9.0). Pinned 6.21.2 GitHub CI evidence pending.
- No APK/EXE building, no OCR or user content processed.

## Next implementation
Implement page ranges, metadata browser, safe file IO, and parser-isolation limits; build valid corpus and benchmark; proceed to viewer adapter after ADR decision.

## B01 page-selection slice — 2026-10-24
- Implemented `engine/pdfstudio/page_ranges.py`: user-visible **1-based inclusive** selection such as `1-3,5`, open-ended `2-`, reverse `5-3`, `all`, and intentional repeated pages; rejects malformed, zero and out-of-bounds selections rather than silently clamping. This is distinct from pypdf's existing zero-based, stop-exclusive `PageRange` semantics (reviewed upstream; BSD-3-Clause). No copy of upstream parser.
- Integrated text selection into `extract_pages` without breaking its existing zero-based integer-list contract. PDF source remains unchanged; output uses existing no-clobber publication.
- Added `tests/test_page_ranges.py`: six parser contracts and two **synthetic PDF round-trip/no-write** tests. This is B01/F08.01 partial coverage, not complete F08.01 acceptance.
- Verified on source commit `53afa4eac9976f9b70841f22153539750e2d0e20`: [PR CI #40097066019](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40097066019) **PASS**, Python 3.12, pinned pypdf 6.21.2, syntax check and **28 unit/source tests PASS**; [push CI #40097062301](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40097062301) **PASS**. Earlier cancelled intermediate CI is not evidence of a failure.
- **NOT TESTED**: Android/Windows UI, hostile-file sandboxing, large-document performance, real OCR, and full end-to-end acceptance. No APK/EXE/AAB or installer packaging invoked.


## B01 local library / Arabic search slice — 2026-10-24

- Added production `engine/pdfstudio/library_index.py`: local SQLite FTS5 cross-document, page-linked search over native pypdf-extracted text; original PDFs are never written. Search keys use the existing Arabic diacritic and alef-variant normalization while preserving original preview text. User query terms are escaped/quoted, with limits on length and result count.
- Database uses an **external-content FTS5 table with insert/delete/update triggers** so replacement/removal does not leave stale hits; transactional rollback preserves the prior index if extraction, page validation, SHA verification or text caps fail. A source SHA-256 is recorded; the caller must invoke `verify_source()` before opening a search result (search does not automatically rehash files). Unchanged native PDFs can skip reindex; corrected OCR text is deliberately reindexed even with the same source hash.
- Added an OCR-text **ingestion boundary**, not an OCR engine: verifies source SHA and real PDF page count, accepts explicitly labeled OCR text with 0-based page provenance; partial page coverage remains labeled. Native and OCR text are not silently conflated.
- Added `tests/test_library_index.py` with **13 synthetic tests**: native PDF indexing/search, persistence, source mutation, stale-term cleanup, Arabic/English OCR text, source digest mismatch, transaction rollback, injection-like search terms, text caps, encryption refusal, corrected OCR reindex, cross-file search, partial OCR coverage, actual page-count mismatch, deletion and FTS integrity (some cases combined).
- **Verified CI** on source commit `901dd8a77ece7966e225e88b76e7e7b32e6a7ad6`: [Source checks run #40106244525](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40106244525) **PASS**, Python 3.12 with pinned `pypdf==6.21.2`, syntax check and **41 unit/source tests PASS** (28 pre-existing + 13 new). Separate local tests: 13 PASS on Python 3.13 / pypdf 5.9.0. Documentation-only commits need their own final-SHA CI confirmation.
- **Open-source reuse/licensing**: SQLite's public-domain FTS5 external-content triggers and integrity-check patterns were reviewed against [official SQLite documentation](https://www.sqlite.org/fts5.html); the parser is the existing BSD-3-Clause pypdf dependency. No external code, model weights or private PDFs were committed.
- **Limitations / NOT TESTED**: no native Android/Windows integration, no OCR execution, no OCR quality benchmark, no source-file watcher, no secure encrypted index-at-rest, no isolated hostile-PDF parser process, no full-text matched-span highlighting. Configurable character/page caps limit *indexed text*, **not decompression memory**; pypdf warns that a compressed PDF content stream may expand dramatically during extraction. SQLite FTS5 availability and private app storage must be verified on both target platforms. Database contents can contain private text and must not be uploaded or logged. F01.03 is **PARTIAL**, not accepted; all 49 feature gates remain subject to end-to-end validation.


## B01 active milestone: resource-bounded native-text indexing — 2026-10-24

**Milestone goal:** preserve the existing SQLite FTS5 search and transactional rollback while moving pypdf native-text parsing and OCR source page-count checks out of the main process. This is part of B01/F01.03 and B01/F01.01, not full feature acceptance.

**Source changes:** `bounded_parser.py` uses a fresh spawn worker with POSIX RLIMIT_AS (768 MiB default), RLIMIT_CPU, a 90-second wall-clock deadline, page/text caps and a bounded per-page IPC protocol. `LibraryIndex.index_pdf` streams results into the existing transaction; `index_ocr_text` verifies the actual page count in count-only mode. On timeout, malformed/encrypted PDF, worker crash or cap violation, the previous index must remain intact. No private PDF content is included in errors. Source SHA is rechecked before commit.

**Milestone acceptance:** generated PDFs extract correctly without modifying the original; encrypted/malformed inputs and over-limit text are rejected; stale entries do not survive successful reindex; failed reindex rolls back; worker timeout fails closed; source-only CI on the new commit passes. Six new real-subprocess regression tests complement the previous 41. Host tests do **not** prove malicious-PDF sandboxing, Android SAF or Windows runtime.

**Known security gaps:** this is resource isolation, **not** an OS sandbox (no seccomp/namespace/filesystem confinement). Windows lacks a Job Object memory cap and fails closed; Android native runtime adapter is not built. `inspection.py` and `page_tools.py` still parse in-process and must be migrated before opening untrusted PDFs. Child worker can still read the host filesystem with parent privileges. Large legitimate files may require a user-controlled per-job memory/time profile after resource preflight. Do not mark F01.01/F01.03 PASS yet.

**Research:** reviewed official pypdf extraction warning about decompression/memory (https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md), Python resource RLIMIT_AS/CPU docs (https://docs.python.org/3/library/resource.html), and existing pypdf BSD-3-Clause pin. No third-party code copied; only Python standard library primitives used. Policy 2026-10-24 permits purposeful builds for runtime milestones, but this POSIX source milestone needs no APK/EXE packaging.
