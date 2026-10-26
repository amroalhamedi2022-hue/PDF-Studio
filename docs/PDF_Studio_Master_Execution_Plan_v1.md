# PDF Studio — Master Execution Plan v1.0
**Date:** 2026-10-24  
**Status:** APPROVED SCOPE / NOT IMPLEMENTED  
**Working repository:** `amroalhamedi2022-hue/pdf-studio` (pending creation/verification by owner)  
**Platforms:** Android (Xiaomi Pad 5 first; phones) + Windows 11 (MSI Sword 15 first).  
**Mission:** Offline-first end-to-end PDF viewing, OCR, large-file processing, reconstruction, editing, archival, workflow automation, and quality assurance.

> Scope lock: all items in this document are required unless the owner explicitly approves a documented change. Do not claim an item done based on UI, mock data, or code presence alone. No external cloud transmission without explicit consent. **Updated build policy 2026-10-24:** do not build just to produce more artifacts, but permit APK/EXE test builds whenever a specific functional milestone, device behavior, integration, or performance question cannot be answered by source tests alone. Record the reason, exact SHA, results and follow-up; preserve a separate final release gate.


> **2026-10-24 automation governance override (user approved):** PDF Studio is now included in the **existing scheduled AquaWord + PDF Studio development task**, not a standalone sixth task. This explicit user decision supersedes only the earlier line in Execution Principles saying “One project-specific automation only; never alter existing AquaWord automation.” The apps and code remain completely separate, with distinct GitHub repositories, branches, PRs, CI, test logs, security reviews and delivery artifacts. Both retain the same free-only, no-auto-merge, source-first, **purpose-driven APK/EXE builds permitted when needed to verify real behavior (user clarification 2026-10-24)**, final device recheck and individually verified handoff rules. Do not combine modules or reuse binaries across the two projects. Neither project's completion should block delivery of the other's verified final files. No functional F01-F10 requirements or B00-B11 gates are waived.

## 0. Execution principles (binding)
- Search authoritative open-source alternatives *before* implementing or modifying any feature. Evaluate technical quality, maintenance, license of code + model weights, CVEs, dependencies, and Android/Windows viability; record decision as ADR. Adapt proven implementations legally; never blindly copy.
- GitHub is authoritative: `main` protected by convention; work on `feature/*` branches and PRs, no auto-merge, no unapproved rewrite of history, no changes in other project repos, especially AquaWord.
- Use only the existing user-approved combined AquaWord + PDF Studio automation; do not create a separate automation. Keep each app's repository, source, branches, PRs, CI, artifacts, and handoff strictly separate. Do not touch unrelated projects.
- Source-level CI: tests, lint, dependency/license/SBOM/security scans run fast by default. Allow selectively triggered packaging for milestone integration/device validation when it answers a documented test question; do not package pointlessly on every push. Production release remains a separate gated build (`workflow_dispatch`, `final-ready` approval) only after final acceptance.
- End of each batch: concrete changed paths, commits/PR, tests with PASS/FAIL/NOT TESTED, measured performance when applicable, known limitations, and next batch. Never claim device validation from source checks.
- Public repo means no private scans, user files, passwords, private models/tokens, test secrets, or personal PDFs in history.
- Prioritize safety and document integrity: never overwrite original automatically, atomic saves, recovery, hash checks, visible warnings on signatures, permissions and redaction.

## 1. Product and UX
### 1.1 Branding
- Exact brand: **PDF Studio** (not PDF OCR Studio).
- Icon: 3D Liquid Glass, translucent layers, blue/purple/pink/orange gradients, explicit PDF/document motif + intelligent scanning accents, readable as Android adaptive icon and Windows icon; **PDF Studio below the central icon within the approved brand composition**. Supply PNG, adaptive foreground/background, SVG/vector if accurately reconstructable, and ICO as legitimate derivatives; do not treat a raster screenshot as editable vector. Preserve consistency with established icon family, while making the PDF identity distinctive.
- UI design: Android tablet-first responsive layout + phone layout, Windows keyboard/mouse interface; dark/light/system modes, RTL/LTR, Arabic/English interface, visual accessibility, undo indicators, long-task progress.
### 1.2 Navigation
- Library / Open PDF / OCR / Edit / Reconstruct / Tools / Batch & Workflows / Jobs / Settings.
- On-document tabs: View, Text/OCR, Layout, Edit, Pages, Compare/QA, Export.
- Always visually distinguish original content, recognized text, editable replacement, and AI-suggested content.

## 2. Architecture (proposed, must be benchmarked in batch 0)
```
pdf-studio/
  android/                 # Kotlin Android UI, document access via SAF, Android OCR adapters
  windows/                 # Windows UI and local processing host
  engine/                  # cross-platform schemas, job semantics, parsers/adapters
  pipelines/               # OCR, enhancement, layout, reconstruction, export
  tests/                   # deterministic unit/integration and golden PDF corpus
  benchmarks/              # performance & accuracy measurements
  docs/                    # plan, acceptance, ADRs, source/reuse and licenses
  .github/workflows/       # fast source-check workflow and targeted milestone builds; separately gated FINAL release
```
- Separation by interface: `Document`, `Page`, `TextSpan`, `BoundingBox`, `LayoutBlock`, `Table`, `Formula`, `Image`, `Annotation`, `EditOperation`, `Job`, `Checkpoint`, `ExportProfile`. Explicit versioned JSON/SQLite storage; source PDF hashes and per-page provenance.
- Windows heavy OCR backend: Python OCRmyPDF/Tesseract, PaddleOCR/PP-StructureV3 or VL where feasible, Docling optional, qpdf/pikepdf/PDFBox as appropriate; local service and bounded worker pool. The UI technology is to be selected via ADR (Qt/PySide6 or comparable) after reviewing licensing/distribution.
- Android: native Kotlin + Compose for UI; appropriate rendering/editing PDF engine and Tesseract Android binding, native models only if actual benchmarks prove viable. Offload heavyweight reconstruction to *user-authorized* local Windows LAN peer optionally; no cloud dependency. Not all Windows models must be embedded on-device.
- PDF processing: avoid rasterizing born-digital pages by default; retain original vector/image content where possible; do not silently weaken existing signatures or permissions. PDF/A output is an explicit archival profile with validation.
- Queue/state: SQLite journal + per-page durable artifacts, bounded memory and cache, checkpoint and resume, atomic final export, crash/cancel cleanup. Encryption for confidential temp data when feasible and deletion controls.
- Explicit capability levels: Native offline (both), Advanced local Windows, Optional LAN delegated, Optional cloud opt-in only if later implemented with user approval.

## 3. Locked feature requirements (feature IDs are permanent)
### PDF foundation and library — F01
- F01.01 Open PDF and images; inspect metadata, page dimensions, text layer, scanned/hybrid pages, encryption, signatures, corruption, password handling.
- F01.02 Render/pan/zoom/navigate/thumbnails/page jump/bookmarks/outlines; full-text search within and across files, selection, copy and highlight.
- F01.03 Local PDF library, folders, tags, recent files, full-text indexing, incremental reindexing, filter/search, duplicate detection; export/import library index.
- F01.04 Android SAF folder and file access, Windows drag/drop/context actions, keyboard shortcuts, autosave recovery.

### OCR fundamentals — F02
- F02.01 Arabic, English and mixed Arabic/English with RTL, diacritics-aware and hamza-normalized searchable index; extend with installed model packs.
- F02.02 Text overlay respecting word-level bounding boxes, orientation and reading order; preserve original page appearance in searchable mode.
- F02.03 Modes: only images/no current text, skip existing text, redo existing OCR, deliberately force OCR with destructive-rasterization warning.
- F02.04 Page selection, range, all pages, batch/folder jobs, language auto-detection (confidence shown), manual language override, DPI/model/quality profiles.
- F02.05 Text viewer, select/copy, full-text export, page-to-text links, OCR error correction and rerun only selected blocks/pages.

### Scanned image enhancement — F03
- F03.01 Orientation detection/rotation, deskew, perspective correction, page boundaries/crop, dewarp/curvature for books, split facing pages.
- F03.02 Denoise/despeckle, shadow/background cleanup, contrast/binarization, adaptive grayscale, blur assessment, blank-page detection.
- F03.03 Before/after compare and preview, reversible recipe, preview vs export profile; never destructively modify original without explicit save-as.

### Massive PDF processor — F04
- F04.01 Preflight: page count, estimated memory/disk, type, signatures/encryption, unsupported content, free space.
- F04.02 Disk-backed batches + bounded workers + resource caps for CPU/RAM/GPU and temporary disk; configurable page batch size.
- F04.03 Checkpoint per page/block and pipeline stage; pause/cancel/resume after crash/reboot; idempotent stages and fingerprint checks for changed inputs.
- F04.04 Retry only failed pages; isolated recovery; progress pages/jobs/ETA (qualified estimate); actionable error log.
- F04.05 Streaming assembly, atomic final export, preserve page order, verify counts and text layer, hash original and output; restore/cleanup temp cache safely.
- F04.06 Test corpus: 10/100/1000/5000 pages, realistic file sizes including >=2 GB where hardware/storage permits; no artificial file/page hard limits, document legitimate resource constraints.

### Document intelligence — F05
- F05.01 Multi-column layout, reading order, paragraphs, headings, list structure, footnotes, header/footer, page numbers, table of contents/bookmarks recovery.
- F05.02 Table detection, multi-page tables, row/col/cell reconstruction; export CSV/XLSX; cell review UI.
- F05.03 Printed/math formula detection and LaTeX export; equation/block alignment review.
- F05.04 Images/figures/charts/diagram and caption recovery; chart structure extraction on supported cases, never invent unseen values.
- F05.05 Handwritten notes and degraded/old paper as best-effort specialized mode with visible confidence/limitations.
- F05.06 OCR confidence heatmap, alternative engine compare, suggested corrections with side-by-side original crop, audit history.

### Editing and annotation — F06
- F06.01 Text editing for born-digital content *where supported*, safe insertion/cover-and-replace mode where direct glyph editing isn't possible, with limitations disclosed.
- F06.02 New text/boxes/fonts/style/positioning; image add/replace/delete/crop; hyperlinks; page background/watermarks/stamps.
- F06.03 Highlight/underline/strikeout/freehand/draw/shapes/notes/comments/sticky notes; annotation management.
- F06.04 Form fill / basic form creation and compatible export; digital signature creation/verification subject to security/library capabilities.
- F06.05 Undo/redo, change history, autosave snapshots, edit confirmation, compare version vs original; backups.
- F06.06 True redaction removal including hidden text, attachments/metadata and validation; NOT merely black painted rectangles.

### Reconstruction — F07
- F07.01 **Preserve mode**: original visual page + invisible selectable text layer.
- F07.02 **Hybrid Edit mode**: select OCR region, edit recognized text, locally repaint/reconstruct text region with appropriate fonts/background; visual diff and manual confirmation.
- F07.03 **Full Rebuild mode**: rebuild pages from layout blocks, real text, images/tables/formulas, RTL typography and font substitutions.
- F07.04 Export reconstructed content to editable DOCX and to rebuilt PDF, with structured Markdown/HTML/JSON and source-region/page traceability.
- F07.05 Side-by-side compare, geometry drift/overlap detection, missing-element detection, manual adjustment, flag low-confidence complex pages. No promise of perfect pixel match.

### Page and PDF tools — F08
- F08.01 Merge, split by pages/ranges/size/bookmarks, extract and reorder, delete, duplicate, insert, rotate, crop, resize, blank page removal, page numbering.
- F08.02 Compress images/streams, optimize size, presets visual-quality vs size, preserve vector modes, linearize/fast web view where supported.
- F08.03 Convert PDF↔image where appropriate; images to PDF, PDF to PDF/A, DOCX, TXT, Markdown, HTML, JSON, CSV, XLSX for extracted tables, selected page extraction; clearly label lossy conversion.
- F08.04 Password open/encryption with legitimate authorization, permission inspection, document info/metadata management, attachment management, signature safety warnings.
- F08.05 PDF structural validation and repair for recoverable corrupt PDFs; report irrecoverable cases with no silent content loss.

### Automation and sharing — F09
- F09.01 Multi-file queues; folder scan and watch (platform-permitted), smart rename using document text, profiles/presets and saved workflows.
- F09.02 Workflow operations with conditionals (scanned? text? language? size?) and previews, dry-run, queue schedule on Windows, re-run failed jobs.
- F09.03 Export/share via Android Share Sheet/SAF and Windows explorer; offline transfer to Windows processing host via mutually authenticated LAN pairing, optional and user-initiated.
- F09.04 Opt-in cloud AI/API adapters only after security/data-processing design and separate owner approval. Default no network processing.

### Security, accessibility and reliability — F10
- F10.01 Privacy by default, permission minimization, no analytics or automatic upload of document text/content.
- F10.02 Encrypted temp storage for sensitive workloads where feasible, safe disposal, log redaction, no personal content in telemetry/reports; sandbox untrusted PDFs and enforce resource limits.
- F10.03 Password handling, signature-preservation warnings, PDF injection/active content sanitization, license and dependency vulnerability scanning.
- F10.04 Keyboard and stylus support, accessibility labels, touch targets, RTL/LTR layout, light/dark/system settings, visible battery/memory impact.
- F10.05 Crash recovery, backup/restore settings/jobs, deterministic export naming and overwrite confirmation.

## 4. Execution batches and exit gates
Every batch requires implementation, relevant automated tests, evidence links, issue updates, and source-only CI. Batch names may evolve but requirement IDs may not silently disappear.

| Batch | Contents | Exit gate |
|---|---|---|
| B00 Research and foundations | Inventory source candidates, license/SBOM and model-weights review, platform architecture ADRs, repo scaffolding, golden test corpus, acceptance matrix, brand assets, source-only CI | Signed-off architecture + all feature IDs linked to tests/issues; no APK/EXE |
| B01 PDF Core | F01 library/view/search, safe file IO, metadata, initial F08 page primitives | Open/inspect/search without corrupting originals; corrupt/password tests |
| B02 Searchable OCR | F02 full, core F03 preprocessing, preserve F07.01 | AR+EN mixed validation, selectable aligned searchable PDF, golden comparison |
| B03 Massive Documents | F04 full, batch pipeline F09.01; crash/power-loss simulations | Resume + page integrity 10/100/1000 and stress up to 5000/2GB when available |
| B04 Structure & Intelligence | F05 tables/equations/layout/handwriting/confidence + structured exports | Accuracy metrics, text order and table/formula corpus checks; flag unsupported cases |
| B05 Full PDF Toolkit | F08 full, conversion, compress, repair, advanced annotations/F06.03 | Validity/roundtrip/permission/signature/regression tests |
| B06 Native Editing | F06 text/image/forms/undo/redo/redaction and safety | Semantic edit roundtrips, redaction proof; no fake editable text |
| B07 Reconstruction | F07 Hybrid + Full Rebuild, DOCX/HTML/MD etc | Diff review, editable text objects and table fidelity on benchmarks |
| B08 Workflows & Security | F09 and F10, local LAN delegated processing, offline controls | Authorization test, no silent upload, security/license checks, reproducible jobs |
| B09 UI and Polish | Tablet/phone/desktop layouts, themes, bilingual RTL, icon, onboarding, dialogs | UX/a11y tests, images and copy match design references |
| B10 Independent Acceptance | Requirements coverage and full source recheck, no regressions, performance and stress, test audit | All requirements PASS or documented limitation with explicit user acceptance; sign off to package |
| B11 Final Deliverables | Gated final Android APK and Windows EXE/installer packaging (milestone test builds may have preceded it); real-device install/open/save/print/roundtrip checks | Signed/checksummed actual artifacts, QA log, full summary and source pointers; repair only if failed |

## 5. Acceptance matrix and quantitative benchmarks
- For each `Fxx.yy`, maintain a row in `docs/ACCEPTANCE.md` with status `TODO/IN PROGRESS/PASS/FAIL/BLOCKED/NOT TESTED`, test source path, test output or CI link, applicable platform(s), last commit and issue number.
- OCR: manually corrected Arabic/English ground truth corpus across fonts, mixes and scan quality; compute CER and WER by language and document type; no unverified global 99–100% marketing claim.
- Layout: measure detection/reading order/table cell structure/formula transcription; preserve evidence snippets and error-category distributions.
- Editability: select and modify a reconstructed word, save, reopen, inspect PDF text operators/text extraction and visible placement. Hybrid region edit must not hallucinate or silently erase neighboring content.
- Large files: synthetic + real scanned+born-digital+hybrid; 10, 100, 1000, 5000 pages and one >=2 GB file (when feasible). Record device OS/CPU/RAM/temp disk, peak RSS, processing duration/pages per minute, hashes, failures, and recovery time. `5000 pages` and `2 GB` are **targets**, never presumed proven limits.
- Security: adversarial malformed PDFs; encryption & permission checks; remove redacted text/images/metadata; digital signature invalidation alerts; temp cleanup and failure recovery; validate that user content is never sent to remote services absent explicit action.
- Exports: open with independent PDF parser; verify page count, bookmark integrity, selectable text, tables/formulas, image preservation, PDF/A conformance when selected; visually compare raster snapshots at fixed DPI.
- Final status: Android real Xiaomi Pad 5 tests and Windows MSI Sword 15 tests must be distinguished from host/integration tests. Source tests alone do not prove device behavior.

## 6. Project operating model
- Repository `amroalhamedi2022-hue/pdf-studio`, created PUBLIC by the owner. Verify access before writing. Do not touch `aquaword-android` or AquaWord automation.
- Create `docs/RESEARCH_AND_LICENSES.md`, `docs/ARCHITECTURE_DECISIONS.md`, `docs/ACCEPTANCE.md`, `docs/PROGRESS.md`, `docs/TEST_MATRIX.md`, `docs/SECURITY.md` and `docs/RELEASE_GATES.md` in B00.
- Branch convention: `feature/b00-foundation`, then `feature/b01-pdf-core`...; separate PRs with traceability. `main` only after owner-approved PR merges.
- Reporting every productive batch: files and LOC/functionality, cited source inspirations, dependency licenses, new tests and measured results, current gates, blockers, what's next.
- No paid OCR or hosted AI APIs by default. Research alternatives without copying code without attribution or license check; avoid dependencies that force inappropriate disclosure or payments.
- Scheduling: independent PDF Studio automation hourly at an offset to avoid overlapping AquaWord runs; no edits to old automations. If repo is not ready, report precise blocker instead of claiming development.
- End: once all accepted and final binaries are generated and actually validated, supply files/verified links, SHA256, version, commit, tests evidence, and a concise but complete delivery summary. Only then stop PDF Studio task; leave AquaWord unchanged.

## 7. Known constraints and honest limitations
- Optical OCR and full pixel-faithful editable reconstruction for arbitrary PDFs cannot be guaranteed. For low-confidence areas, require manual review.
- Android memory/model size may make large vision-language models impractical; route advanced workloads to optional local Windows engine, not compulsory internet service.
- Some PDF edits and PDF/A conversions modify documents in ways that invalidate digital signatures; warn and save copies.
- Open source licenses are not interchangeable: OCRmyPDF MPL-2.0, Tesseract Apache-2.0, PaddleOCR Apache-2.0 source, Docling MIT source (model licenses separately), qpdf Apache-2.0; PyMuPDF AGPL/commercial, Stirling PDF mixed/open-core. Each redistributed library/model must be reviewed at pinned version before importing.

## 8. Sources verified during planning
- OCRmyPDF source / modes: https://github.com/ocrmypdf/OCRmyPDF ; https://ocrmypdf.readthedocs.io/en/stable/advanced.html
- Tesseract Arabic models: https://github.com/tesseract-ocr/tessdoc/blob/main/Data-Files.md
- PaddleOCR source and document parsing: https://github.com/PaddlePaddle/PaddleOCR ; https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md
- Docling: https://github.com/docling-project/docling
- qpdf: https://github.com/qpdf/qpdf
- PDFBox: https://pdfbox.apache.org/
- pikepdf: https://github.com/pikepdf/pikepdf
- PyMuPDF licensing: https://pymupdf.io/licensing
- Stirling PDF licensing: https://github.com/Stirling-Tools/Stirling-PDF/blob/main/LICENSE

## 9. Immediate next action
1. Owner creates PUBLIC repository `amroalhamedi2022-hue/pdf-studio` and grants the existing GitHub connector repository access if needed.
2. Check latest state; create `feature/b00-foundation`; commit this plan without modifying any other project.
3. Complete B00 license/architecture spike, tests/references and source-only CI; proceed batch-by-batch through gates.

**No source code or binary implementation has yet been created by this planning file.**