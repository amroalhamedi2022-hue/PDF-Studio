# PDF Studio — Acceptance Matrix v1

Source of truth: [approved master plan](PDF_Studio_Master_Execution_Plan_v1.md). Status is TODO until a tested implementation has documented results. **B00 checkpoint primitives are NOT full F04.03 acceptance.**

Statuses: TODO, IN PROGRESS, PASS, FAIL, BLOCKED, NOT TESTED. A PASS requires test evidence/commit, not a static UI or mock. No feature may disappear from this list without owner-approved scope change.

| ID | Locked requirement | Batch | Status | Verification |
|---|---|---|---|---|
| F01.01 | Open PDF and images; inspect metadata, page dimensions, text layer, scanned/hybrid pages, encryption, signatures, corruption, password handling. | B01 | TODO | NOT TESTED |
| F01.02 | Render/pan/zoom/navigate/thumbnails/page jump/bookmarks/outlines; full-text search within and across files, selection, copy and highlight. | B01 | TODO | NOT TESTED |
| F01.03 | Local PDF library, folders, tags, recent files, full-text indexing, incremental reindexing, filter/search, duplicate detection; export/import library index. | B01 | TODO | NOT TESTED |
| F01.04 | Android SAF folder and file access, Windows drag/drop/context actions, keyboard shortcuts, autosave recovery. | B01 | TODO | NOT TESTED |
| F02.01 | Arabic, English and mixed Arabic/English with RTL, diacritics-aware and hamza-normalized searchable index; extend with installed model packs. | B02 | TODO | NOT TESTED |
| F02.02 | Text overlay respecting word-level bounding boxes, orientation and reading order; preserve original page appearance in searchable mode. | B02 | TODO | NOT TESTED |
| F02.03 | Modes: only images/no current text, skip existing text, redo existing OCR, deliberately force OCR with destructive-rasterization warning. | B02 | TODO | NOT TESTED |
| F02.04 | Page selection, range, all pages, batch/folder jobs, language auto-detection (confidence shown), manual language override, DPI/model/quality profiles. | B02 | TODO | NOT TESTED |
| F02.05 | Text viewer, select/copy, full-text export, page-to-text links, OCR error correction and rerun only selected blocks/pages. | B02 | TODO | NOT TESTED |
| F03.01 | Orientation detection/rotation, deskew, perspective correction, page boundaries/crop, dewarp/curvature for books, split facing pages. | B02 | TODO | NOT TESTED |
| F03.02 | Denoise/despeckle, shadow/background cleanup, contrast/binarization, adaptive grayscale, blur assessment, blank-page detection. | B02 | TODO | NOT TESTED |
| F03.03 | Before/after compare and preview, reversible recipe, preview vs export profile; never destructively modify original without explicit save-as. | B02 | TODO | NOT TESTED |
| F04.01 | Preflight: page count, estimated memory/disk, type, signatures/encryption, unsupported content, free space. | B03 | IN PROGRESS | Page extraction/merge now estimate output bytes as min(output cap, max(1 MiB, 2×source bytes + 1 MiB per source)), check free space before and after SHA preflight, and reserve the same-filesystem staging file with a default 16 MiB free-space margin. Exact CI #40323380061 PASS on Ubuntu/Windows with a 64 MiB synthetic disk-backed PDF. This is a conservative heuristic, not a guarantee against concurrent disk consumption or estimate overruns; full PDF inspection preflight, realistic >=2 GiB files and scanned corpora remain NOT TESTED. See docs/B01_PROGRESS.md. |
| F04.02 | Disk-backed batches + bounded workers + resource caps for CPU/RAM/GPU and temporary disk; configurable page batch size. | B03 | IN PROGRESS | B01 POSIX RLIMIT / Windows Job Object containment, end-to-end wall deadline, configurable 1 GiB default output-byte cap, free-space preflight and same-filesystem staging reservation for extract/merge. Exact CI #40323380061 PASS on Ubuntu + Windows: 123 tests per host (four Windows-only skips on Ubuntu). The 64 MiB synthetic disk-backed extraction reserved an estimated 135,267,466 bytes with a 16,777,216-byte margin and produced a 67,109,392-byte PDF; source SHA stayed unchanged and no staging leftovers remained. Realistic >=2 GiB/scanned workloads, disk-full race behavior, GPU/temp-disk quotas beyond this output policy and device acceptance remain NOT TESTED. See docs/B01_PROGRESS.md. |
| F04.03 | Checkpoint per page/block and pipeline stage; pause/cancel/resume after crash/reboot; idempotent stages and fingerprint checks for changed inputs. | B03 | IN PROGRESS | Exact-head CI #40292114548 PASS on Ubuntu and Windows (117 tests each; four Windows-only tests skipped on Ubuntu). Initial job creation rejects source mutation during streaming SHA-256 before inserting a job row. Synthetic 5000-page crash/resume and FTS integrity PASS; OS reboot/device recovery NOT TESTED. See docs/B01_PROGRESS.md. |
| F04.04 | Retry only failed pages; isolated recovery; progress pages/jobs/ETA (qualified estimate); actionable error log. | B03 | TODO | NOT TESTED |
| F04.05 | Streaming assembly, atomic final export, preserve page order, verify counts and text layer, hash original and output; restore/cleanup temp cache safely. | B03 | TODO | NOT TESTED |
| F04.06 | Test corpus: 10/100/1000/5000 pages, realistic file sizes including >=2 GB where hardware/storage permits; no artificial file/page hard limits, document legitimate resource constraints. | B03 | IN PROGRESS | Exact CI #40323380061 PASS: 100-page smoke on both OSes, 1000/5000-page checkpoint-crash-resume/FTS on Linux, and a separate 64 MiB disk-backed extraction on both OSes. The 5000-page PDF is 2.64 MB born-digital synthetic data; the 64 MiB fixture is a generated large comment stream, not a realistic scanned corpus. >=2 GiB, Arabic/English OCR, disk-full races and device workloads remain NOT TESTED. See docs/B01_PROGRESS.md. |
| F05.01 | Multi-column layout, reading order, paragraphs, headings, list structure, footnotes, header/footer, page numbers, table of contents/bookmarks recovery. | B04 | TODO | NOT TESTED |
| F05.02 | Table detection, multi-page tables, row/col/cell reconstruction; export CSV/XLSX; cell review UI. | B04 | TODO | NOT TESTED |
| F05.03 | Printed/math formula detection and LaTeX export; equation/block alignment review. | B04 | TODO | NOT TESTED |
| F05.04 | Images/figures/charts/diagram and caption recovery; chart structure extraction on supported cases, never invent unseen values. | B04 | TODO | NOT TESTED |
| F05.05 | Handwritten notes and degraded/old paper as best-effort specialized mode with visible confidence/limitations. | B04 | TODO | NOT TESTED |
| F05.06 | OCR confidence heatmap, alternative engine compare, suggested corrections with side-by-side original crop, audit history. | B04 | TODO | NOT TESTED |
| F06.01 | Text editing for born-digital content *where supported*, safe insertion/cover-and-replace mode where direct glyph editing isn't possible, with limitations disclosed. | B05–B06 | TODO | NOT TESTED |
| F06.02 | New text/boxes/fonts/style/positioning; image add/replace/delete/crop; hyperlinks; page background/watermarks/stamps. | B05–B06 | TODO | NOT TESTED |
| F06.03 | Highlight/underline/strikeout/freehand/draw/shapes/notes/comments/sticky notes; annotation management. | B05–B06 | TODO | NOT TESTED |
| F06.04 | Form fill / basic form creation and compatible export; digital signature creation/verification subject to security/library capabilities. | B05–B06 | TODO | NOT TESTED |
| F06.05 | Undo/redo, change history, autosave snapshots, edit confirmation, compare version vs original; backups. | B05–B06 | TODO | NOT TESTED |
| F06.06 | True redaction removal including hidden text, attachments/metadata and validation; NOT merely black painted rectangles. | B05–B06 | TODO | NOT TESTED |
| F07.01 | **Preserve mode**: original visual page + invisible selectable text layer. | B02/B07 | TODO | NOT TESTED |
| F07.02 | **Hybrid Edit mode**: select OCR region, edit recognized text, locally repaint/reconstruct text region with appropriate fonts/background; visual diff and manual confirmation. | B02/B07 | TODO | NOT TESTED |
| F07.03 | **Full Rebuild mode**: rebuild pages from layout blocks, real text, images/tables/formulas, RTL typography and font substitutions. | B02/B07 | TODO | NOT TESTED |
| F07.04 | Export reconstructed content to editable DOCX and to rebuilt PDF, with structured Markdown/HTML/JSON and source-region/page traceability. | B02/B07 | TODO | NOT TESTED |
| F07.05 | Side-by-side compare, geometry drift/overlap detection, missing-element detection, manual adjustment, flag low-confidence complex pages. No promise of perfect pixel match. | B02/B07 | TODO | NOT TESTED |
| F08.01 | Merge, split by pages/ranges/size/bookmarks, extract and reorder, delete, duplicate, insert, rotate, crop, resize, blank page removal, page numbering. | B01/B05 | IN PROGRESS | Extract/reorder and merge primitives remain source-tested. New parent publication guard checks the reserved staging file's device/inode identity, regular-file type, size cap and `%PDF-` header before no-clobber publication; replacement-inode/non-PDF rejection and no-publication cleanup tests PASS on exact-head CI [#40339576523](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40339576523), Ubuntu + Windows (test commit `41ab8a584878de386dd7183e3a438f49f5f353ae`). Test source: `tests/test_page_isolation.py`. Full F08.01 acceptance is NOT TESTED: split-by-size/bookmarks, delete/duplicate/insert/rotate/crop/resize/blank-page removal/page numbering and platform UI are not yet implemented or validated. See `docs/B01_PROGRESS.md`. |
| F08.02 | Compress images/streams, optimize size, presets visual-quality vs size, preserve vector modes, linearize/fast web view where supported. | B01/B05 | TODO | NOT TESTED |
| F08.03 | Convert PDF↔image where appropriate; images to PDF, PDF to PDF/A, DOCX, TXT, Markdown, HTML, JSON, CSV, XLSX for extracted tables, selected page extraction; clearly label lossy con | B01/B05 | TODO | NOT TESTED |
| F08.04 | Password open/encryption with legitimate authorization, permission inspection, document info/metadata management, attachment management, signature safety warnings. | B01/B05 | TODO | NOT TESTED |
| F08.05 | PDF structural validation and repair for recoverable corrupt PDFs; report irrecoverable cases with no silent content loss. | B01/B05 | TODO | NOT TESTED |
| F09.01 | Multi-file queues; folder scan and watch (platform-permitted), smart rename using document text, profiles/presets and saved workflows. | B03/B08 | TODO | NOT TESTED |
| F09.02 | Workflow operations with conditionals (scanned? text? language? size?) and previews, dry-run, queue schedule on Windows, re-run failed jobs. | B03/B08 | TODO | NOT TESTED |
| F09.03 | Export/share via Android Share Sheet/SAF and Windows explorer; offline transfer to Windows processing host via mutually authenticated LAN pairing, optional and user-initiated. | B03/B08 | TODO | NOT TESTED |
| F09.04 | Opt-in cloud AI/API adapters only after security/data-processing design and separate owner approval. Default no network processing. | B03/B08 | TODO | NOT TESTED |
| F10.01 | Privacy by default, permission minimization, no analytics or automatic upload of document text/content. | B08–B10 | TODO | NOT TESTED |
| F10.02 | Encrypted temp storage for sensitive workloads where feasible, safe disposal, log redaction, no personal content in telemetry/reports; sandbox untrusted PDFs and enforce resource l | B08–B10 | TODO | NOT TESTED |
| F10.03 | Password handling, signature-preservation warnings, PDF injection/active content sanitization, license and dependency vulnerability scanning. | B08–B10 | TODO | NOT TESTED |
| F10.04 | Keyboard and stylus support, accessibility labels, touch targets, RTL/LTR layout, light/dark/system settings, visible battery/memory impact. | B08–B10 | TODO | NOT TESTED |
| F10.05 | Crash recovery, backup/restore settings/jobs, deterministic export naming and overwrite confirmation. | B08–B10 | TODO | NOT TESTED |

## Batch B00 checkpoint (separate from user-facing features)

- [x] Repository initialization and approved plan committed on feature branch.
- [x] Initial SQLite journal, SHA verification and Arabic index normalization unit-tested locally (8 tests). These are scaffolding and do NOT make OCR or PDF editing work.
- [ ] GitHub Actions evidence reviewed (CI execution/status needed).
- [ ] Owner review / architecture sign-off and required icon derivatives.

## Test coverage contract

Every Fxx.yy must have at least one reproducible automated check or honest manual acceptance protocol with real evidence. NOT TESTED cannot be converted to PASS on code presence alone.
