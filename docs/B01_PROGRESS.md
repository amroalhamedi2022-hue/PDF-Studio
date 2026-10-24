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
