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
- No APK/EXE was built in this initial source-only checkpoint; build decisions follow the updated 2026-10-24 purpose-driven policy and require a concrete runtime/device/integration question. No OCR or user content was processed.

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

## B01 continuation — bounded read-only inspection (2026-10-24)

**Same active milestone:** resource-bounded handling of untrusted PDFs. Production `inspection.py` now dispatches metadata/page-size/text-layer sampling to a spawned POSIX child with RLIMIT_AS (768 MiB default), RLIMIT_CPU, 90-second wall-clock limit, 10,000-page default ceiling and a 32-page sampling limit. The original input is read-only and SHA-256 is checked after the child returns. The encrypted-document/password-required contract remains supported; metadata title/author are truncated to 2048 characters to bound IPC. Errors do not include extracted PDF contents.

**Acceptance tests added:** subprocess timeout must fail closed; invalid resource limits must be rejected; excessive page counts must fail without source modification; valid encrypted PDF unlock must preserve metadata. These supplement existing blank/text/malformed/metadata inspection tests. New code is in the existing B01 branch/PR #2, not main.

**Verification status:** source changes committed; CI results for the newest SHA must be checked before declaring PASS. Linux/macOS resource containment is **not** a complete security sandbox. Windows Job Objects, Android native integration, page-tools isolation, 1000+/5000-page benchmarks, real OCR, and final platform builds remain **NOT TESTED**. No APK/EXE built in this source-only milestone.


## B01 same milestone — isolated page extraction/merge (2026-10-24)

**Production scope:** `page_tools.extract_pages` and `merge_pdfs` now dispatch pypdf parsing and writing to a fresh spawned POSIX child through `page_isolation.py`. Worker applies RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE (1 GiB), wall-clock deadline and total-page cap; input SHA-256 is pinned before dispatch and rechecked before publishing. The existing atomic no-clobber hard-link output and signature/encryption refusal remain. Public methods keep their existing positional arguments and expose optional per-job limits. Windows **fails closed**, not silently unbounded.

**Acceptance:** real synthetic extraction/merge preserve input bytes and order; total-page caps and timeouts reject safely; existing destination is never overwritten; bad limits fail early; existing page range, signature and encryption tests must remain PASS. Added seven regression tests in `tests/test_page_isolation.py`. CI outcome must be recorded separately; source code presence alone is not a PASS.

**Residual risks:** child is **not** a full filesystem/syscall sandbox; an external process can race output publication or source mutation; an abrupt crash after publication but before worker acknowledgement can leave a valid newly created output even if the parent reports failure. Parent verifies hashes after return but cannot automatically remove a possibly user-owned output safely. RLIMIT_FSIZE is a per-file cap, not a total temporary-storage quota. Windows Job Objects and native Android PDF backend remain NOT TESTED. The milestone stays OPEN until security/platform gates and 1000+/5000-page benchmarks pass. No APK/EXE built for this source-only change.


## B01 same milestone — parent-validated staged output (2026-10-25)

**Risk addressed:** before this change the worker could publish the caller-visible output and then crash or time out before its acknowledgement; the parent would report failure while a new final PDF remained. In addition, parent-side SHA revalidation happened after worker publication.

**Production change:** `page_isolation.run_page_operation` now gives the child a randomly named, private same-filesystem staging directory, not the requested destination. The child performs its bounded PDF write and fsync there. Only after a positive worker response, nonempty staged file check and independent parent SHA-256 revalidation does the parent atomically `os.link` the staged result to the final no-clobber destination. Worker termination precedes staging cleanup. This applies to both extract and merge; existing outputs and originals remain protected.

**Regression additions:** verify only the parent publishes the final file; simulated final-link failure leaves no final file or staging directory; simulated source change after worker success blocks publication; timeout cleanup is checked. Existing synthetic PDF round-trip tests remain. This is a correctness/cleanup improvement, **not** a sandbox or Windows support.

**Acceptance gate:** new and existing tests plus source CI must pass on the final SHA; any failure is FAIL rather than inferred success. At the time of committing this note the latest CI result is **NOT TESTED / pending verification**. Physical Android/Windows runs, 1000+/5000-page benchmarks, OCR and Windows Job Objects remain **NOT TESTED**. No APK/EXE build was needed for this POSIX-source fix. Parent process termination immediately after successful final hard-link can still leave a valid published output; filesystem crashes and malicious host processes remain outside this guarantee.

**Research:** used Python standard-library `tempfile.TemporaryDirectory` and `os.link` documented behavior, no third-party code copied; see https://docs.python.org/3/library/tempfile.html and https://docs.python.org/3/library/os.html#os.link.

## B01 same active milestone — Windows Job Object page-worker slice (2026-10-25)

**Goal / acceptance for this slice:** extraction and merging on Windows must run in a spawned worker whose PDF parsing cannot begin before an OS-enforced memory cap is attached; the worker must be killed when the owning Job Object closes; no output may be published until the worker has acknowledged success **and exited with code 0**. Both Windows and POSIX must preserve source bytes, page order, timeout and no-clobber behavior. Windows native CI must run and fail if either page-worker or Job Object test suite fails.

**Production:** `engine/pdfstudio/windows_job.py` implements Win32 CreateJobObjectW, SetInformationJobObject (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), OpenProcess and AssignProcessToJobObject using Python stdlib ctypes. `page_isolation.py` creates a spawn gate and assigns the process to its Job Object before releasing the gate; unsupported/failed assignment rejects the operation instead of falling back to unbounded parsing. The parent now waits for a clean worker exit before publishing the staged PDF; an acknowledged worker that crashes or hangs cannot publish its output. POSIX RLIMIT behavior is preserved. Windows memory cap is committed-memory containment, not equivalent to POSIX RLIMIT_AS, and there is no Windows RLIMIT_FSIZE/CPU analogue here.

**Verification:** Python source syntax, real Windows extract/merge synthetic round trips, parent-only publication, timeout, existing-output protection, and actual Win32 Job membership/kill-on-close are exercised on free GitHub-hosted `windows-latest` CI. Linux runs the full existing suite. Windows test commands are separate CI steps to prevent the second command masking a failure in the first. Initial Windows job had a **masked failure** caused by Windows short 8.3 vs long temp path spelling in a test assertion; corrected with `Path.resolve()` and separated the commands so CI correctly propagates failures. On source SHA `3c178aa04d06fd89bb0032d6811b64f4a959323d`, [CI #40150066176](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40150066176): Ubuntu **63 tests run (61 PASS, 2 Windows-only skipped)**, Windows **10 page-worker PASS + 2 Job Object PASS**. Confirm overall run conclusion before treating it as final CI PASS.

**Open gates:** This is **PARTIAL** Windows support for B01 page operations only. `bounded_parser.py` (FTS/native text) and `inspection.py` still fail closed on Windows until the same guard is integrated and verified. No security token/filesystem sandbox, Windows executable, Android APK, real OCR, or 1000+/5000-page benchmark. Windows nested-job restrictions may cause fail-closed assignment on some hosts. The worker retains caller filesystem permissions. No user document or secret was used.

**Research/license:** Microsoft official Job Objects documentation (https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) and JOBOBJECT_BASIC_LIMIT_INFORMATION flags; implementation uses standard-library ctypes without copied third-party code. Existing pypdf dependency remains pinned under its BSD-3-Clause license. This source/runtime test needed Windows CI because POSIX-only tests cannot execute Win32 APIs. No APK/EXE build was warranted.

**Next:** Extend the verified Windows guard to read-only inspection and bounded native text extraction without regressing SQLite transactional rollback; then test large synthetic PDFs, Android adapter and full B01 gates. Keep B01 OPEN and PR #2 unmerged.

## B01 same milestone — Windows bounded inspection + native-text FTS (2026-10-25)

**Acceptance scope:** untrusted PDF metadata/text-layer inspection, native text extraction and OCR-source page-count verification on Windows must execute in a spawned worker *after* successful assignment to a memory-capped Win32 Job Object. Failed assignment, parser crash, timeout or resource-cap violation must fail closed. A successful worker must acknowledge completion and exit with code 0 before results can be trusted. SQLite FTS5 must preserve transactional rollback, original-PDF bytes and page-level provenance; the existing POSIX RLIMIT path must remain operational. This remains a slice of the same resource-bounded B01 milestone, **not full B01 acceptance**.

**Production:** `bounded_parser.py` and `inspection.py` now reuse the existing stdlib-ctypes `WindowsJob` and a spawn gate; pypdf parsing starts only after the parent attaches the worker to a memory-capped Job Object. Both paths wait for clean process exit; `bounded_parser.py` also closes its Job Object if spawning fails. `library_index.py` uses this parser for native FTS indexing and OCR page-count verification. On POSIX, existing RLIMIT_AS/CPU behavior remains. No private PDF, new third-party library or paid service was added.

**Regression:** the real Windows suite now includes bounded parser extraction/count-only/encryption/text caps/timeout, inspection/password/metadata/page caps, FTS native and OCR indexing/rollback, page tools, and Windows Job lifecycle; two new Windows-only tests deliberately make Job assignment fail and verify safe refusal. Windows CI now executes the *complete* source suite rather than just page-worker tests. The first full Windows CI [#40157197282](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40157197282) exposed a single cross-document FTS test expectation using Windows 8.3 short temp paths vs canonical long paths; the production code already stores canonical resolved paths, so the test was corrected to compare resolved paths. Linux job passed on that run; Windows job was FAIL, **not** accepted. Final source SHA before checkpoint update: `80d81636ee4e0b0ad5e019a2cb6dcd1d4df9d626`; CI [#40157275503](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40157275503) is **PENDING** until verified. Never label it PASS from source presence alone.

**Remaining gates / NOT TESTED:** hostile-file OS sandboxing (Job Objects only limit resources and lifetime, not filesystem permissions), Win32 job-nesting edge cases beyond hosted CI, real 1000+/5000-page benchmark and resume, Android native PDF adapter/SAF, real OCR Arabic/English and searchable PDF, Windows EXE, Android APK, device runtime, full F01–F10 acceptance. Continue this milestone with stress benchmarks and robust cancellation/checkpoint tests; do not merge PR #2 or touch main.

**Research and license:** reused the already-reviewed Microsoft Win32 Job Object API and Python multiprocessing primitives; existing pinned BSD-3-Clause pypdf and public-domain SQLite FTS5 unchanged. Free hosted Windows CI is necessary to exercise the Win32 API, unlike POSIX-only unit tests.


## B01 same milestone — cooperative native/OCR FTS cancellation (2026-10-25)

**Milestone:** B01 resource-bounded PDF core (OPEN). **Acceptance slice:** a caller can cancel native text indexing or OCR-text ingestion before or between page insertions, including immediately before commit, without publishing any partial SQLite FTS results or destroying the previously committed document index. Source bytes must remain untouched; existing POSIX/Windows bounded-parser paths must continue to work.

**Production:** `engine/pdfstudio/library_index.py` now accepts an optional cooperative `cancel_requested` callback on `index_pdf` and `index_ocr_text`. It raises `LibraryIndexCancelled` before worker startup, during page insertion, or immediately before transaction commit. The existing `_open()` rollback preserves document rows, external-content FTS triggers and old search results. Invalid callbacks fail early. This is cooperative cancellation at page boundaries, **not** instantaneous cancellation during a blocked pypdf page extraction or a crash-safe per-page resume scheduler.

**Regression:** four synthetic tests cover early native cancellation, OCR cancellation after one inserted page, cancellation at the pre-commit boundary, and invalid/no-op callbacks. Existing native/Windows Job Object, SHA provenance, OCR page count, FTS integrity and page-operation suites remain part of CI. Production+tests source HEAD `3b9ed56ed50f90c03f1125f1db5a7add618621ea`; [free push CI #40164675013](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40164675013) = **PASS on Linux and Windows**, including Python syntax. [PR CI #40164677865](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40164677865) must be checked independently; do not infer its status from the push run. This checkpoint commit has a separate SHA/CI run.

**Remaining / NOT TESTED:** full crash/reboot resume and cancellation inside a blocked parser call; 10/100/1000/5000-page timed memory/disk benchmark; Android SAF/device and Windows EXE; genuine Arabic/English OCR and searchable PDF; full F01–F10 acceptance. No APK/EXE was built because this change is exercised by cross-platform source CI. No new dependency, private document, paid runner, merge or main edit. SQLite rollback design reviewed against https://www.sqlite.org/lang_transaction.html and Python sqlite3 transaction documentation; no third-party code copied.

**Next:** continue the same B01 milestone with synthetic 100/1000-page benchmark evidence and interrupted-job checkpoint recovery (with explicit protection against concurrent live workers), then verify real-platform adapters. Keep PR #2 draft and B01 OPEN.

## B01 same milestone — read-only interrupted-job audit + synthetic scale instrumentation (2026-10-25)

**Acceptance slice:** after a process interruption, a fresh Journal instance must report which page-stage checkpoints are SHA-verified, missing or damaged, without mutating job state, trusting unverified artifacts, or automatically resuming a possibly still-live worker. This is **not** crash-safe automatic resumption: an explicit worker ownership/quiescence protocol remains required before re-running a 'running' job.

**Production:** `JobJournal.recovery_snapshot(job_id, stage)` verifies the source before and after inspection, rehashes every checkpoint artifact, and reports valid/invalid/missing page indexes, journal state, and a live-worker warning. It never transitions a job or authorizes resume. Two tests exercise restart, tampering, missing artifacts, changed source and paused-state read-only behavior.

**Scale instrumentation:** `benchmarks/synthetic_pdf_bench.py` creates deterministic born-digital PDFs with real extractable text, runs bounded inspection and SQLite FTS indexing, verifies the original SHA and FTS integrity, and prints JSON timings, file sizes and POSIX parent/max-child RSS high-water marks. Free CI now runs 100-page smoke on both hosts and 1000-page smoke on Linux. The generated corpus is small/simple and is **not** evidence for realistic 2GB PDFs, OCR, 5000 pages, adversarial inputs, Android or Windows EXE. Windows RSS is reported null, not guessed.

**CI status:** pending verification for the commit introducing this slice. No APK/EXE was needed: these are Python source and hosted Windows/POSIX behavior checks. Do not claim PASS until both CI jobs and benchmark output are reviewed. B01 remains OPEN; next step is real measured limits and safe worker-ownership protocol for automatic resume.

**Research:** uses existing BSD-3-Clause pypdf, SQLite and Python stdlib resource/getrusage APIs, no new dependencies or copied third-party code.


## B01 same milestone — fenced local worker sessions (2026-10-25)

**Scope and acceptance:** a fresh worker can claim a pending job; a crashed session can be resumed on the *same local host* only after the OS releases its exclusive lock. New ownership rotates a SQLite token and rejects stale checkpoint/state writes. A live worker prevents a competing claim; original SHA and existing final output block unsafe recovery. Clean session exit leaves an unfinished job paused. Previously created legacy running/paused/failed jobs with no ownership history intentionally fail closed. This is B01/B03 groundwork, not full automatic pipeline resume.

**Production:** `engine/pdfstudio/worker_lease.py` provides Python stdlib POSIX flock / Windows msvcrt nonblocking file locks; `jobs.py` persists a generation/token per job and enforces ownership under SQLite BEGIN IMMEDIATE for checkpoints and state transitions. Lock files remain in private local journal storage and are deliberately never unlinked. The lock must stay held throughout worker processing; callers must use `worker_session` and its methods. No network/distributed filesystem, old legacy worker, or uncooperative process is assumed safe. The OS lock is advisory and does not sandbox untrusted PDF parsing.

**Tests:** `tests/test_worker_lease.py` exercises clean resume, concurrent claim refusal, stale token fencing, subprocess abrupt exit, legacy refusal, changed source and existing output. Check actual Linux/Windows CI before marking PASS. No APK/EXE needed for this Python/SQLite cross-platform change; Windows CI is necessary to verify msvcrt behavior. Source references: Python fcntl/msvcrt documentation and official SQLite BEGIN IMMEDIATE transactions. No external code copied or dependencies added.

**Still NOT TESTED:** integration with a full OCR/reconstruction scheduler, reboot/disk corruption, Android SAF, real scanned files, 5000-page/2GB stress, device tests and deliverable apps. Next: review CI and connect fenced sessions to real per-page stage scheduling while preserving journal compatibility.

**Verified result (2026-10-25):** production+tests commit `dd70843b78f84b32b2d8a640014bf7ab15bfd21e` first passed Linux but failed Windows CI #40179856087 because a second Windows worker could not read the byte-range-locked file; this was a real test failure, not accepted. The implementation was corrected to lock byte 0 without reading/writing the locked range (Windows permits a lock past EOF), at `3d783ff6ec50d5a258bfa1646d17e6457448aa84`. [Push CI #40179922967](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40179922967) **PASS** (76 tests Linux + 76 tests Windows, syntax + 100-page Linux/Windows and 1000-page Linux synthetic smoke); [PR CI #40179927889](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40179927889) **PASS**. These prove local worker locking, fencing and crash-restart tests on free hosted runners, not full OCR pipeline resume or device integration. B01 remains **IN PROGRESS**, F04.03 remains **NOT TESTED end-to-end**.


## B01 same milestone — linear-time checkpoint reuse for large page counts (2026-10-25)

**Acceptance slice (IN PROGRESS):** stage resume must not rehash *all* page artifacts for every already-completed page. Retain local exclusive WorkerSession fencing, per-page SHA-256 validation before reuse, source verification at stage entry/exit, and a final whole-stage audit. If an artifact is damaged between the initial audit and the page skip, reprocess that page; never silently trust it. This is a performance/correctness improvement to the same B01 milestone, not full F04.03 acceptance.

**Production:** `JobJournal.verified_checkpoint(job_id, page_index, stage)` uses the existing composite SQLite primary key for one-page artifact lookup and SHA-256 verification. `stage_runner.run_page_stage` caches page count and calls this method instead of invoking `resumable_pages` for every page. The previous loop scanned/hashed N artifacts up to N times (quadratic on a fully resumable job); the new loop performs one full audit, one single-artifact check per reusable page, and one final full audit (linear artifact hashing in page count). Per-page SQLite connection overhead and large real-world workloads remain to be measured.

**Regression:** three added tests cover 128-page bounded lookup count (asserting no full rescans), tampering after initial snapshot before reuse, and single-page checkpoint validity/invalid arguments. Existing crash, cancellation, fenced ownership, Windows/POSIX isolation, and synthetic PDF tests remain in CI. Source+tests SHA `1a80274196b25a05456ba0f46d4ccdcc3783128c`; verify free Linux/Windows GitHub Actions before marking PASS. No APK/EXE build: this Python/SQLite change is verifiable through hosted source tests; device builds would not answer the checkpoint-complexity question.

**Remaining (NOT TESTED):** real OCR pipeline and end-to-end crash/reboot resume, real scanned Arabic/English searchable PDF, 5000-page/2GB stress with disk/RAM metrics, Windows EXE, Android APK/SAF and Xiaomi Pad 5. Keep B01 OPEN, PR #2 unmerged, and main unchanged. Next: CI verification and a real stage adapter with isolated page processing and bounded resource use.

**Research/license:** follows SQLite indexed primary-key lookup guidance (https://sqlite.org/queryplanner.html) and Python SHA-256 file hashing documentation (https://docs.python.org/3/library/hashlib.html). Uses existing Python stdlib and SQLite; no third-party code copied or dependency added.

## B01 same milestone — bounded selective native-text stage + crash resume (2026-10-25)

**Milestone:** B01 — Resource-Bounded PDF Core (OPEN). **Acceptance slice:** a trusted local caller can process a real born-digital PDF in a bounded spawned parser, extract only missing/damaged pages, durably checkpoint UTF-8 page text under an exclusive fenced worker session, verify source and artifact SHA-256, refuse a changed page count, and resume after cooperative cancellation or abrupt parent-process death. Original input and final output must remain untouched. This is **native text extraction, NOT OCR**, searchable PDF, or Android/Windows product delivery.

**Production:** `bounded_parser.IsolatedPdfReader(page_indexes=...)` now accepts validated sorted unique page indexes, rejects out-of-range selection before reporting count, enforces the existing page/text/memory/CPU/wall-time caps and Windows pre-parse Job Object gate. `jobs.STAGES` adds distinct `native_text` without reusing `prepared` or `ocr`. `native_text_stage.run_native_text_stage` holds one `worker_session` lease for a whole batch, performs an entry audit, re-verifies reusable checkpoints individually, spawns a parser only for missing/damaged pages, writes new unique UTF-8 artifacts through fsynced temporary files and atomic rename, checkpoints SHA-verified files, and audits all pages again before reporting success. Interrupted workers leave committed pages resumable; the job remains paused, never auto-completed. No new third-party dependency or private PDF.

**Regression / evidence:** seven real-pypdf integration tests (selected-page extraction, invalid selection, first run/reuse, corruption repair, cancellation/restart, abrupt subprocess crash/restart, pinned page-count mismatch) plus all existing suites. Initial SHA `0be2670595c726ba6a732e66c92fa6562e662699` **FAILED** Linux/Windows CI #40201287903 because invalid selection was reported after page count and the test expected fail-closed entry; fixed the production protocol to reject before count. Code SHA `ae08f748c015b2b6238a483b7f09e9ef6b72c6d7` [CI #40201378819](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40201378819) **PASS**: Linux 91 tests (87 pass, 4 Windows-only skipped), Windows 91/91 pass; synthetic 100-page Linux/Windows and 1000-page Linux steps also passed. CI verifies real isolated text extraction and crash recovery on hosted OSes, not OCR or physical devices.

**Known limits / NOT TESTED:** native text artifacts are not yet consumed by FTS or a complete page-processing scheduler; no Arabic/English scanned-page OCR, text overlay, searchable PDF, 5000-page/2GB realistic stress, filesystem/syscall sandbox, Android SAF/ARM runtime, Windows EXE or Xiaomi Pad 5 tests. Rehashing the entire source for each checkpoint is potentially expensive for very large files; profile and safely optimize before claiming F04.02/F04.03 acceptance. Temporary orphan artifacts after hard crashes need a bounded cleanup policy. No APK/EXE built: this milestone slice was verified with real Windows/Linux subprocess source CI, while a platform build would not test these Python-only contracts. No changes to main, no merge or paid runner.

**Next:** stay in B01. Integrate verified native-text artifacts transactionally into local FTS with provenance and rollback, then profile 1000+/5000-page source-hash overhead and interruption/restart on larger real corpora; plan genuine OCR B02 separately without mislabeling native text as OCR. Existing pypdf 6.21.2 BSD-3-Clause pin and Python/SQLite stdlib remain; no copied third-party code.


## B01 — 2026-10-25: verified native-text checkpoints into transactional FTS5

**Active milestone:** B01 Resource-Bounded PDF Core, still OPEN. This slice connects two existing production components: the fenced `native_text` page-stage journal and the offline Arabic/English SQLite FTS5 library index. It is **not OCR**, searchable-PDF creation, or full F01.03 acceptance.

**Production SHA:** `9af011d118b75e73550904b882d6701c62ec1962`. New `LibraryIndex.index_native_text_checkpoints(journal, job_id)` obtains an exclusive fenced worker lease, requires exactly one checkpoint for every page, independently verifies actual PDF page count in a bounded parser subprocess, and reads each UTF-8 artifact with a strict per-page byte cap and SHA-256 verification. FTS replacement runs in one SQLite transaction; cancellation, corruption, invalid UTF-8, incomplete pages, or source mismatch must leave the old search index unchanged. The unchanged-source shortcut is deliberately bypassed for checkpoint ingestion because a rebuilt/corrected artifact may differ even when the PDF source SHA is unchanged. Existing `index_pdf` shortcut remains intact. No external source code or new dependencies added.

**Regression SHA:** `542c9707a6db33e02aede13befcf6a227d2ede14`. Seven new synthetic PDF / real isolated parser / SQLite tests cover page-linked search, same-source checkpoint correction, corrupted/missing artifact rollback, incomplete stage rejection, forged page count rejection, invalid UTF-8 and byte cap, and cancellation rollback. Tests contain no user documents. CI run: https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40210735870 — **PENDING at checkpoint authoring**; do not call PASS without checking the completed run.

**Acceptance:** native-text checkpoint-to-search implementation PRESENT; unit/integration CI **PENDING**. F01.03 remains PARTIAL/NOT ACCEPTED. **NOT TESTED:** genuine Arabic/English OCR, scanned-page text overlay, actual 1000+/5000-page large-file indexing benchmark, Android/Windows app UI and packaged APK/EXE, hostile-PDF sandbox confinement, encrypted private-index storage, physical device verification. Source-only changes do not justify an APK/EXE build. Next: verify/fix CI on exact SHA, then continue B01 resource-bounded end-to-end page-stage/search integration and measured large synthetic workloads before opening B02 OCR. PR #2 remains unmerged; main untouched.


## B01 — 2026-10-25: OCR text checkpoint ingestion (index adapter, not OCR engine)

**Active milestone:** B01 Resource-Bounded PDF Core remains OPEN. Acceptance for this slice: a complete fenced per-page `ocr` checkpoint stage can be indexed into local SQLite FTS5 with Arabic/English search and 1-based page links, without exposing partial/corrupt results or modifying source PDFs. This is an indexing bridge for future OCR workers; it **does not render PDFs, run Tesseract, or produce searchable PDF output**.

**Production:** `f2d10b142076ade39c54fd22ea3e2e6996edf43c` refactors the existing SHA-verified native-text checkpoint ingestion into a stage-aware internal method and adds `LibraryIndex.index_ocr_checkpoints`. Both native and OCR paths retain the exclusive worker lease, bounded subprocess page-count verification, byte-capped strict UTF-8 decoding, per-artifact SHA-256 verification, transactional FTS rollback, cancellation and correction reindexing even with unchanged source PDF SHA. No third-party code/dependency added; existing OCR direct-text API is unchanged.

**Regression:** `e1fb46a71fdc3be9c663f5c03a44a29be4645e39` adds five integration tests for Arabic/English page-linked OCR-provenance search, incomplete stage rollback, tampering and invalid UTF-8 rollback, same-source correction, and cancellation. `f36d7156502d25c0334fb35c80e08ec987133244` ensures test checkpoints are unique immutable files rather than overwriting equal-length fixtures. All use synthetic PDFs/text, no private documents. [Exact test SHA CI #40220570185](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40220570185) **PENDING at this checkpoint**, so PASS/FAIL is not yet claimed.

**Remaining acceptance:** verify exact-SHA Linux and Windows CI; fix any failure. Real scanned-page OCR engine, OCR page-image rasterization, language packs, PDF text-layer overlay, 5000-page/2GB resource benchmarks, full B01/F01 acceptance, Android APK and Windows EXE remain **NOT TESTED**. Next: validate CI, then implement/verify resource-bounded OCR page adapter on a deliberately selected free, license-reviewed stack, preserving the immutable checkpoint protocol and explicit input/output caps. No new build needed for this source-only FTS bridge; PR #2 stays open, main unchanged.


## B01 — 2026-10-25: immutable checkpoint artifact enforcement

**Active milestone:** B01 Resource-Bounded PDF Core remains OPEN. This hardens the existing checkpoint protocol; it does not claim OCR execution or full feature acceptance.

**Production change:** `JobJournal.checkpoint` now rejects the journal database as an artifact, prevents one canonical artifact path from being assigned to different page/stage/job keys, and rejects changed bytes at an already-registered path for the same key. A processor must publish a new immutable path when rebuilding a page. This closes a protocol gap where a later page/job could overwrite bytes previously trusted by another checkpoint, causing avoidable resume corruption and unsafe artifact aliasing. Existing source pinning, worker fencing and SHA checks remain in force.

**Regression:** added `test_checkpoint_artifacts_are_unique_immutable_and_not_the_journal` for journal-path rejection, cross-page path aliasing, and in-place mutation rejection. Production commit `635bcdc41465a9ba974180f529c20b0d62fecc59`; test commit `bbe2c4b6f1715f3e60b90c2ccd6f5f2b1123a7f8`. Exact-head CI [#40230155398](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40230155398) is **PENDING** as of checkpoint authoring; do not infer PASS until complete.

**Acceptance:** checkpoint immutability/path ownership source and regression test PRESENT; CI **PENDING**. **NOT TESTED:** real OCR, searchable-PDF overlay, 5000-page/2GB corpus, hostile-PDF OS sandboxing, Android APK, Windows EXE, physical devices. Next: verify CI on the final checkpoint SHA, repair any regression, then continue B01 resource-bounded integration and measured large synthetic workloads before opening B02. PR #2 remains open and unmerged; `main` unchanged.


## B01 checkpoint artifact immutability — verification closeout (2026-10-25)

- Corrected legacy fixture `tests/test_foundation.py` to model recovery correctly: after an artifact's bytes are damaged, the processor publishes the repaired bytes to a **new immutable path** and the journal updates that page's checkpoint. The production protocol intentionally refuses in-place repair of a registered path.
- Final exact-code CI on `6ed57086dced008caaab414412b7b64aa9ef79dc`: [run #40230276453](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40230276453) **PASS** — Linux 104 tests, 4 platform-specific skips; Windows 104/104 PASS. New artifact-integrity regression passes on both hosts.
- Production: `635bcdc41465a9ba974180f529c20b0d62fecc59`; new regression test: `bbe2c4b6f1715f3e60b90c2ccd6f5f2b1123a7f8`; compatibility fixture: `6ed57086dced008caaab414412b7b64aa9ef79dc`.
- **Milestone status remains OPEN.** This is checkpoint protocol hardening only. Genuine OCR, searchable PDF text overlay, 5000-page/2GB realistic corpus, Android APK, Windows EXE, hostile-input OS sandbox, and device verification remain **NOT TESTED**. Next: continue B01 with reproducible large synthetic workloads and resource-bound/resume integration; do not open B02 until B01 acceptance gates are met. PR #2 remains open/unmerged; `main` unchanged.


## B01 — 2026-10-26: constant-count source hashing + 5000-page checkpoint/resume evidence

**Active milestone:** B01 Resource-Bounded PDF Core remains OPEN. This improves checkpoint throughput without weakening source integrity; it does not start B02 or claim real OCR.

**Production change:** `JobJournal` now captures a source-file identity/metadata fingerprint when a fenced worker session begins. Each page checkpoint uses cheap `stat`-fingerprint checks before/inside its SQLite transaction instead of hashing the entire input PDF twice per page. The session still verifies full source SHA-256 at entry and exit; recovery snapshots keep full source SHA checks before/after artifact audit. If source identity/metadata changes during processing, checkpointing fails closed and the job is marked `failed`; a mutated source cannot be resumed under the old pinned digest. This changes full-source SHA work from O(page_count × source_size) to a constant number of whole-file hashes per stage, while preserving per-artifact SHA verification and immutable artifact paths.

**Regression tests:** source hashing stays at exactly six whole-file SHA calls for a complete/resumed 128-page synthetic stage (entry/exit plus two full recovery audits, independent of page count); the test still verifies one indexed artifact lookup per page and forbids full rescans. A new mutation-during-processing test proves source changes fail closed, mark the job failed, reject recovery snapshots and refuse resume. Initial CI #40239221911 correctly exposed an assertion in that new test which expected a recovery snapshot from a deliberately changed source; the test was corrected to expect `SourceChangedError` (the intended safety behavior).

**Measured scale evidence:** exact final source SHA `b3bded2edca252f3b1657f26e7e11632bac180d8`, [GitHub Actions #40239585170](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40239585170) **PASS** on Ubuntu and Windows. Each platform: **105 tests PASS** (Linux has 4 Windows-only skips). Both platforms' 100-page smoke benchmark passed; Linux 1000-page benchmark passed. Linux 5000-page synthetic benchmark **PASS**: 5,000/5,000 pages indexed; 2,638,282-byte synthetic PDF; 1,280,000 bytes of UTF-8 page artifacts; 2,965,504-byte checkpoint FTS database; first checkpointed extraction 12.922 s; full resume validation skipped all 5,000 pages in 1.268 s; checkpoint-to-FTS index 1.344 s; parent peak RSS 56.96 MiB, max child peak RSS 55.96 MiB (max child is not combined process RSS); source SHA unchanged; FTS integrity PASS. CI logs provide the JSON evidence.

**Strict limitations:** this is a 2.64 MB born-digital synthetic PDF, **not** a 2 GB document, OCR, scanned Arabic/English, hostile-PDF sandbox, device/UI, or realistic worst-case corpus. F04.03 end-to-end reboot/crash acceptance and F04.06 realistic 5000-page/≥2 GB gates remain **NOT TESTED**. Android APK, Windows EXE, physical devices, and actual OCR remain NOT TESTED. No build was run because the source/SQLite benchmark directly answers the hashing and checkpoint-resume performance question; packaging cannot answer it.

**Next:** remain in B01. Add an interruption/resume benchmark for the actual native-text stage at 5000 pages and establish a larger-file, disk-backed stress profile; then address OCR only after B01 resource and integrity gates are independently accepted. PR #2 remains open/unmerged; `main` unchanged.

**Google Drive Handoff sync:** Google Drive/Docs write tools are not available in this automation runtime. `PDF Studio — HANDOFF` was **not updated**; synchronization status is **FAILED / NOT AVAILABLE**, not claimed successful.


## B01 follow-up — 2026-10-26: 5000-page mid-stage interruption and selective resume

- Extended the same B01 scale benchmark to cancel the real native-text stage after 2,500 of 5,000 pages, verify the journal is paused, resume only missing pages, rerun a completed stage to verify all checkpoints, and index all artifacts into FTS5. No private corpus or third-party dependency.
- Exact benchmark/code SHA `e1d88f264a8e68da0c3dfcd2e4e5d5b8db6664e0`: [CI #40239849443](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40239849443) **PASS** on Linux + Windows; 105 tests on each (4 Linux-only skips). Linux 5000-page measured result: interruption after 2,500 pages in 6.360 s; resume processed only remaining 2,500 pages in 7.721 s while skipping 2,500 verified pages; a final complete-stage reuse skipped all 5,000 pages in 1.280 s; checkpoint-to-FTS indexing took 1.393 s. 5,000/5,000 indexed, FTS integrity PASS, original SHA unchanged; synthetic PDF 2,638,282 bytes, text artifacts 1,280,000 bytes, checkpoint FTS DB 2,965,504 bytes; parent peak RSS 56.25 MiB and max child peak RSS 56.25 MiB (not summed process RSS).
- These results support the checkpoint/resume slice only. This is still a 2.64 MB born-digital synthetic file, not OCR or a realistic 2 GB corpus; device/UI, hostile-input OS sandboxing, true crash/reboot recovery on Android/Windows, and full F04.03/F04.06 remain **NOT TESTED**. B01 stays OPEN; B02 is not opened.
- Latest benchmark documentation commit: `d003e573ee8a39d06e5fd32da4fca5abd891dbf2`. PR #2 remains draft/open and unmerged; `main` unchanged. Google Drive `PDF Studio — HANDOFF` sync remains **FAILED / NOT AVAILABLE** because this runtime exposes no Google Drive/Docs write connector; no successful sync is claimed.


## B01 follow-up — 2026-10-26: abrupt parent-process crash at 2,500/5,000 pages

- Replaced the benchmark's cooperative cancellation with a deliberate `os._exit(23)` of the native-text stage parent after 2,500 durable per-page checkpoints. The harness waits for that process to exit before auditing the journal and resuming, so it does not mistake a live worker for a safe resume. It verified exactly 2,500 valid artifacts and 2,500 missing pages after the crash, processed only the missing half on resume, then skipped all 5,000 artifacts on a subsequent completed-stage pass and indexed all pages into FTS5.
- Exact code/benchmark SHA `bce39a3cd53fe309183301e241ad14a95bc39e49`: [CI #40240190103](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40240190103) **PASS** on Linux and Windows, 105 tests per OS (4 Linux-only skips). Linux 5000-page result: parent crashed with expected exit code 23 after 2,500 durable pages in 6.578 s; resume processed 2,500 remaining pages in 7.963 s and skipped 2,500; completed-stage reuse skipped all 5,000 in 1.284 s; checkpoint-to-FTS indexing 1.401 s. 5,000/5,000 searchable pages, FTS integrity PASS, source SHA unchanged. Synthetic PDF 2,638,282 bytes; artifacts 1,280,000 bytes; FTS database 2,965,504 bytes; parent peak RSS 56.68 MiB, max child peak RSS 56.18 MiB (not combined).
- This validates process-crash recovery for the synthetic native-text stage on the Linux CI host, not OS reboot, Android/Windows runtime, real OCR, hostile-file sandboxing, or a realistic 2 GB PDF. F04.03 remains partial and F04.06 remains **NOT TESTED**; B01 stays OPEN and B02 is not opened.
- Benchmark docs updated at `bc1d57a774e936ef7866adfb7cf52d4047c51b7f`. PR #2 remains draft/open and unmerged; `main` untouched. Google Drive Handoff write connector is unavailable; sync remains **FAILED / NOT AVAILABLE**.


## B01 — 2026-10-26: explicit byte budget for native-text checkpoints

**Milestone:** B01 Resource-Bounded PDF Core remains OPEN. This slice caps the total bytes referenced by a job's `native_text` checkpoints before continuing work and checks each newly encoded UTF-8 page before writing it. The default is 96 MiB, aligned with the existing 20-million-character stage cap's worst-case UTF-8 expansion while allowing headroom. Callers may set a lower or higher positive integer budget explicitly.

**Production SHA:** `84281a9d6e2ae5a3cc9c5d96aa4a136465c9879c`. **Regression SHA:** `081a6eb8fe8d6b0beb0d69178f4d5b0c327980a9`. Tests cover rejecting a page before writing when the budget is too small, enforcing the budget against already registered checkpoint files during resume, and rejecting invalid limits. The budget counts journal-referenced files (including damaged files that still consume disk); it does not yet account for orphan artifacts left by a hard crash, so safe orphan cleanup remains a separate TODO.

**CI:** [run #40248279787](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40248279787) **PASS** on the exact production+test+checkpoint SHA `72ba7450499b3f57a7f32b6206fe6ace97fd3eda`: Windows **108/108 tests PASS**; Linux **108 tests ran, 4 Windows-only tests skipped**, remaining tests PASS. Both hosts passed the 100-page smoke benchmark; Linux passed 1000-page and 5000-page crash/resume/FTS benchmarks. The Linux 5000-page synthetic PDF was 2,638,282 bytes; 2,500 durable checkpoints survived an abrupt parent exit (code 23), 2,500 missing pages resumed in 8.585 s, all 5,000 were reused in 0.681 s on the next pass, and FTS integrity passed. Parent/max-child peak RSS were 57.71/56.71 MiB (separate high-water marks, not aggregate). This confirms the byte-cap regression and the existing scale suite on the tested commit; it is not evidence for a 2 GB real PDF or OCR.

**Acceptance / limits:** this is an explicit cap on checkpoint artifact growth, not a 2 GB PDF acceptance test. Genuine OCR, searchable PDF overlay, realistic 2 GB corpus, reboot recovery, hostile-input sandboxing, Android APK/SAF, Windows EXE and device UI remain **NOT TESTED**. No app build was run: this Python/SQLite storage-limit contract is directly testable through source CI. PR #2 stays open/unmerged; `main` is unchanged.

**Next within B01:** safe orphan cleanup is implemented and verified on Linux/Windows. Continue with realistic large-file disk/RAM measurements, bounded parser behavior, and stronger worker ownership/recovery evidence. Do not open B02 until B01's resource, integrity and recovery gates are independently accepted. Google Drive Handoff sync is unavailable in this runtime and has not been claimed.


## B01 follow-up — 2026-10-26: safe cleanup of orphaned native-text artifacts

**Milestone:** B01 Resource-Bounded PDF Core remains OPEN. The native-text stage now serializes cooperating writers and cleanup with a persistent local artifact-directory lock, removes only stale unreferenced `page-*.txt` / `.text-*.tmp` regular files after a 24-hour grace period, protects all journal-referenced paths and hard-link aliases, rechecks file identity/size/mtime before unlink, and fsyncs the directory after successful rename/removal on POSIX. Recent orphan bytes and failed deletions count against the existing checkpoint byte budget; this prevents an abrupt crash from silently escaping disk accounting. Cleanup does not recurse and is not a hostile-directory security sandbox.

**Production + regression commit:** `5e7a420936fcf13aa3d0f34f124baa642d5eb5a6`; cross-platform path assertion fix: `baec89cdd1303709540db36359c0f746ceb7db91`.

**Exact-head CI:** [run #40258626157](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40258626157) **PASS** on Linux + Windows at `baec89cdd1303709540db36359c0f746ceb7db91`. Windows: **114/114 tests PASS**. Linux: **114 tests, 4 Windows-only tests skipped, remaining tests PASS**. Both hosts passed the 100-page smoke; Linux passed 1000-page and 5000-page checkpoint/crash/resume/FTS checks. The first attempt exposed only a Windows 8.3-vs-long-temp-path mismatch in a test assertion; the assertion now compares normalized basenames and the exact-head run passes.

**Measured Linux 5000-page evidence:** synthetic born-digital PDF `2,638,282` bytes; 2,500 checkpoints persisted before intentional parent exit code 23; resume processed the remaining 2,500 pages in `7.126 s`; a completed-stage pass reused all 5,000 in `0.785 s`; checkpoint index step `0.808 s`; 5,000 pages indexed and FTS integrity **PASS**. Source SHA remained unchanged. Parent/max-child peak RSS `53.28/53.96 MiB` are separate process high-water marks, not aggregate memory. This is not OCR and not a realistic 2 GB corpus.

**Acceptance status:** F04.03 and F04.06 are **IN PROGRESS**, not PASS. Realistic >=2 GB files, scanned PDFs, OS reboot recovery, hostile-input sandboxing, Android/SAF, Windows GUI/EXE and device acceptance remain **NOT TESTED**. No APK/EXE build was useful for this Python/filesystem/SQLite contract; the cross-platform source suite directly exercises it. PR #2 remains draft/open/unmerged; `main` unchanged. Google Drive Handoff write connector remains unavailable in this runtime; no successful sync is claimed.

**Next:** remain in B01; measure realistic disk/RAM behavior and strengthen safe recovery/ownership, then independently recheck the B01 exit gates before opening B02.


## B01 follow-up — 2026-10-26: end-to-end page-worker timeout

**Production change:** `engine/pdfstudio/page_isolation.py` now starts the wall-clock deadline before spawning the worker. Process startup and Windows Job Object assignment consume the same configured timeout; the parent polls only for the remaining budget. Previously, startup time was excluded, so a slow spawn could exceed the caller's timeout without being counted.

**Regression test:** `tests/test_page_isolation.py::test_timeout_includes_worker_startup` deliberately delays process startup past a short timeout and verifies the operation fails closed, publishes no final PDF, and removes its staging directory.

**Commits:** production `da3ad53c21c84803b3d40dd9116c6a4a72a2bbc9`; regression `655cb8f20d5e67e124bc464ec5a02b9245023917`.

**Exact-code CI:** [run #40268195202](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40268195202) and [run #40268199015](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40268199015) both **PASS** on Windows and Ubuntu for SHA `655cb8f20d5e67e124bc464ec5a02b9245023917`. Each host ran **115 tests PASS**; Linux skipped four Windows-only tests. The 100-page smoke passed on both hosts. Linux's 5000-page synthetic native-text/checkpoint crash-resume/FTS benchmark passed: 2,638,282-byte born-digital PDF; 2,500 durable checkpoints before intentional exit code 23; resume processed 2,500 pages in 10.858 s, reused all 5,000 in 0.782 s on the next pass, and indexed FTS in 2.297 s; source SHA unchanged and FTS integrity PASS. Parent/max-child peak RSS were 53.04/54.15 MiB (separate high-water marks, not summed).

**Limits:** the timeout test proves the configured deadline includes worker startup on CI; it is not an Android/Windows GUI test or a security sandbox. The 5000-page input is still a 2.64 MB synthetic born-digital PDF—not OCR, a scanned corpus, or a realistic >=2 GB file. OS reboot recovery, physical-device behavior, and F04.06 realistic-large-file acceptance remain **NOT TESTED**. No APK/EXE build was justified for this Python worker-timeout contract.

**Google Drive handoff:** Drive/Docs write connector is unavailable in this runtime. `PDF Studio — HANDOFF` was **not updated**; sync status is **FAILED / NOT AVAILABLE**, not successful. PR #2 remains draft/open/unmerged; `main` unchanged.

**Next within B01:** keep the resource-bounded core milestone open; proceed with a disk-backed large-file profile and bounded parser/recovery evidence before considering B02.



## B01 follow-up — 2026-10-26: end-to-end deadline includes parent-side source hashing

**Active milestone:** B01 Resource-Bounded PDF Core remains OPEN / PARTIAL. This closes a wall-clock gap in the existing page-operation timeout contract; it does not complete F01/F04 acceptance and does not open B02.

**Production SHA:** `01570ecaac953138bd971d92e5577889ce99dd89`. `run_page_operation` now starts its monotonic deadline before source resolution/validation and applies that same deadline to parent-side streaming SHA-256 preflight and post-worker source revalidation. Hashing reads bounded 1 MiB chunks rather than relying on an unbounded parent-side full-file hash. If the deadline expires during either parent hash, the operation fails closed before publishing a destination. The worker's own parsing/hashing remains bounded by the parent wall deadline and existing POSIX resource limits / Windows Job Object memory containment. This is resource containment, **not** a filesystem/security sandbox.

**Regression SHA:** `82342f3bbc01b7c277ad48921d769347e0a72799`. New `test_deadline_includes_parent_source_hashing` injects a slow preflight hash and proves the operation times out before worker spawn, leaves the source unchanged, publishes no output and leaves no staging directory. The existing source-mutation-after-worker regression was updated to intercept the new deadline-aware parent hash helper rather than the child-only hash symbol. An initial CI run `#40278551686` failed because that existing test still patched the old helper; the regression was corrected and the final exact-head CI below passes.

**Exact-head CI:** [run #40278859043](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40278859043) **PASS** on Windows and Ubuntu. Windows: **116/116 tests PASS**; Ubuntu: **116 tests ran, four Windows-only tests skipped, all remaining PASS**. Both hosts passed the 100-page synthetic smoke. Linux 1000-page smoke: 526,269-byte PDF, 0.976 s native-text FTS indexing, 630,784-byte SQLite DB, parent/max-child peak RSS 35.88/35.48 MiB. Linux 5000-page crash/resume/FTS integration: 2,638,282-byte born-digital synthetic PDF; 2,500 durable pages before deliberate parent exit code 23; resume processed only the remaining 2,500 in 8.429 s; a subsequent complete-stage pass reused all 5,000 in 1.671 s; checkpoint-to-FTS indexing 1.369 s; artifacts 1,280,000 bytes; FTS DB 2,965,504 bytes; source SHA unchanged; FTS integrity PASS; parent/max-child peak RSS 53.11/53.96 MiB (separate process high-water marks, not summed). Windows 100-page smoke: 52,655-byte PDF, 0.394 s indexing, 102,400-byte SQLite DB; RSS not measured on Windows.

**Acceptance / limits:** the regression proves deadline handling for injected slow parent-side hashing and the CI suite continues to prove extraction/merge and synthetic crash-resume behavior. It is **not** a realistic 2 GB PDF measurement, OCR, scanned Arabic/English, reboot recovery, Android/Windows GUI, or physical-device acceptance. F04.02/F04.03/F04.06 remain partial or NOT TESTED as shown in `docs/ACCEPTANCE.md`. No APK/EXE build was warranted: this Python-only wall-deadline and source-integrity question is directly tested by source CI; packaging would not test the parent hashing contract.

**Google Drive handoff:** no Google Drive/Docs write connector is available in this runtime. `PDF Studio — HANDOFF` was **not updated**; sync is **FAILED / NOT AVAILABLE**, not successful.

**Next within B01:** add a disk-backed large-file profile and continue bounded-parser / recovery evidence, while preserving SQLite checkpoints, immutable artifacts, source SHA checks, no-clobber publication and worker fencing. Do not open B02 until B01 resource/integrity/recovery gates are independently accepted. PR #2 remains draft/open/unmerged; `main` unchanged.


## B01 follow-up — 2026-10-26: stable source pin at job creation

**Risk addressed:** create_job previously streamed the initial SHA-256 without checking whether the source file's identity/metadata changed during that read. A concurrent writer could leave a pending SQLite job pinned to a digest that did not describe a stable file snapshot; later resume would fail source verification, but only after the unusable job had already been created.

**Production change:** JobJournal.create_job now records the source stat fingerprint immediately before and after the streaming digest. It rejects a changed/unreadable source with SourceChangedError before inserting the job row. This is a race-detection guard, not a filesystem lock and not a defense against a hostile writer able to manipulate the file concurrently.

**Regression:** tests/test_foundation.py::test_create_job_rejects_source_mutation_during_digest_without_journaling mutates a synthetic input immediately after the digest helper returns and asserts job creation fails and SQLite contains zero job rows.

**Exact-head CI:** [run #40292114548](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40292114548) **PASS** on Ubuntu and Windows for source SHA `bfa770fcc8fbe48a022a8e58ee4686f446924c68`. Both hosts ran **117 tests**; Ubuntu skipped four Windows-only tests, and all remaining tests passed. The new mutation-during-digest regression passed on both hosts.

**Acceptance / limits:** F04.03 remains IN PROGRESS. Existing checkpoint/crash-resume and FTS results remain evidence for their prior exact SHAs; they do not prove this new guard until CI completes. No APK/EXE build is justified: this change concerns the Python journal's initial source pin and is directly testable with cross-platform source tests. Physical Android/Windows UI, OS reboot recovery, OCR, and realistic >=2 GB PDF acceptance remain NOT TESTED.

**Google Drive handoff:** the Drive/Docs write connector is unavailable in this runtime. PDF Studio — HANDOFF was not updated; sync status remains FAILED / NOT AVAILABLE.


**Exact-head measured benchmark for SHA bfa770fcc8fbe48a022a8e58ee4686f446924c68:** Linux 100-page synthetic PDF (52,655 bytes): FTS indexing 0.219 s, SQLite 102,400 bytes, parent/max-child peak RSS 31.75/31.62 MiB. Linux 1000-page PDF (526,269 bytes): indexing 1.017 s, SQLite 630,784 bytes, parent/max-child RSS 36.05/35.59 MiB. Linux 5000-page PDF (2,638,282 bytes): 2,500 durable checkpoints before deliberate exit code 23; interruption 11.153 s; resume processed the remaining 2,500 in 12.342 s; subsequent reuse of all 5,000 verified pages 1.267 s; checkpoint-to-FTS indexing 3.057 s; text artifacts 1,280,000 bytes; SQLite 2,965,504 bytes; source SHA unchanged and FTS integrity PASS; parent/max-child RSS 53.03/54.02 MiB. Windows 100-page smoke passed in 0.287 s indexing; Windows RSS was not measured. RSS figures are separate process high-water marks, not an aggregate memory total.

**Interpretation:** this validates the new initial source-pin regression plus synthetic born-digital checkpoint/resume on the exact code SHA. The 5000-page file is only 2.64 MB; realistic >=2 GB files, scanned Arabic/English OCR, hostile-input sandboxing, OS reboot recovery, Android SAF/UI, and Windows GUI/EXE remain NOT TESTED. F04.03 remains IN PROGRESS, F04.06 remains IN PROGRESS. No APK/EXE build was justified for this Python journal/source-integrity change. Google Drive PDF Studio — HANDOFF sync remains FAILED / NOT AVAILABLE because no Drive/Docs write connector is present.


## B01 follow-up — 2026-10-26: configurable output-byte budget for isolated page tools

**Risk addressed:** page extraction/merge previously relied on a fixed 1 GiB POSIX file-size limit, while Windows Job Objects only contained memory. The worker also wrote a full temporary PDF and then linked a second staged copy, which could temporarily double output-disk usage.

**Production change:** `extract_pages()` and `merge_pdfs()` now accept `max_output_bytes` (positive integer; default 1 GiB). A shared capped binary writer checks each write before it reaches disk on both POSIX and Windows. The isolated worker writes once into its private staging directory and removes a partial file on any write, fsync, or source-verification failure. POSIX additionally applies `RLIMIT_FSIZE` up to the configured cap, respecting the runner's inherited hard limit. The parent still publishes the finished file only after clean worker exit, source re-hash, and no-clobber hard-link publication. This is an output-file ceiling and resource-containment improvement, **not** a free-space preflight or hostile-input security sandbox.

**Regression coverage:** `tests/test_page_tools.py` now checks that extraction and merge both fail closed at a deliberately tiny 128-byte ceiling, leave both source PDFs unchanged, publish no destination, and clean the staging directory. Invalid zero, negative, boolean and non-integer limits are rejected before processing.

**Exact code/test CI:** [run #40306948376](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40306948376) passed on Ubuntu and Windows with 119 tests per host. The follow-up merge-specific regression is included in [run #40307275391](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40307275391), which passed on both hosts with **120/120 tests**; Ubuntu skipped four Windows-only cases and all remaining tests passed. Both CI runs use the repository's pinned `pypdf==6.21.2`; no new dependency was added.

**Measured synthetic scale evidence from exact follow-up run #40307275391:** Linux 100-page PDF 52,655 bytes, FTS indexing 0.226 s; 1000-page PDF 526,269 bytes, indexing 0.948 s; 5000-page PDF 2,638,282 bytes. The 5000-page process was intentionally terminated after 2,500 durable checkpoints; resume processed the remaining 2,500 in 8.890 s, a subsequent pass reused all 5,000 in 1.663 s, and checkpoint-to-FTS indexing took 1.339 s. Source SHA stayed unchanged and FTS integrity passed. SQLite index was 2,965,504 bytes and text artifacts totaled 1,280,000 bytes. Parent/max-child RSS high-water marks were 53.10/53.66 MiB (separate process measurements, not summed).

**Acceptance / limits:** B01 remains OPEN / PARTIAL. The 5000-page file is a 2.64 MB born-digital synthetic PDF, not a realistic >=2 GB file, scanned corpus, OCR test, or disk-full simulation. The cap regression uses a tiny output ceiling; actual near-cap and large-disk tests remain NOT TESTED. Free-space preflight, OS reboot recovery, Android SAF/UI, Windows GUI/EXE, hostile-file sandboxing and physical-device acceptance remain NOT TESTED. F04.02 remains IN PROGRESS and F04.06 remains IN PROGRESS. No APK/EXE build was justified: the changed contract is in the Python worker/file-write path and is directly exercised by cross-platform source/integration tests.

**Google Drive handoff:** no Drive/Docs write connector is available in this runtime. The specified `PDF Studio — HANDOFF` document was not updated; sync remains **FAILED / NOT AVAILABLE**. PR #2 remains Draft/open/unmerged; `main` is unchanged.

**Next within B01:** add an explicit free-space estimate/reservation policy and realistic disk-backed large-file evidence, then independently recheck resource, integrity and recovery exit gates. Do not open B02 yet.


## B01 follow-up — 2026-10-26: free-space preflight and staged output reservation

**Decision record (ADR-0004, recorded here to keep the batch checkpoint consolidated).** Use Python's standard library instead of adding a disk-inspection dependency or invoking an external platform command. `shutil.disk_usage()` is documented for Unix and Windows; POSIX uses `os.posix_fallocate()` to reserve the staging range, while Windows extends the staging file before the worker starts. Sources: [Python shutil.disk_usage](https://docs.python.org/3/library/shutil.html#shutil.disk_usage), [Python os.posix_fallocate](https://docs.python.org/3/library/os.html#os.posix_fallocate), [Python os.ftruncate](https://docs.python.org/3/library/os.html#os.ftruncate). No third-party code was copied and no dependency was added.

**Production behavior**
- Estimate output as `min(max_output_bytes, max(1 MiB, 2 × total input bytes + 1 MiB per source))`; this is deliberately conservative but heuristic.
- Check free bytes on the destination filesystem before source hashing and recheck after hashing.
- Require a default 16 MiB safety margin, configurable through `free_space_margin_bytes` (zero is allowed for controlled tests).
- Before spawning the parser worker, create and preallocate `result.pdf` inside the private staging directory on the same filesystem as the destination. The worker reuses this reserved file; the capped writer tracks the logical output end, truncates unused reservation after serialization, and deletes the staged file on failure.
- A failed free-space query or reservation fails closed before publishing an output. The existing no-clobber hard-link publication and source SHA revalidation remain in place.

**Source and test references**
- `engine/pdfstudio/page_tools.py`: final tested source is included at exact CI SHA `6e2e07d4ee891c0a17adcba04e1ad6c6d0258e35`; intermediate production commits `e693e51eda48d480fed33b658f4cd9c575525194`, `4b87c04cc785bf4d22e0d4b3e2bd160992b39b0d`, and `6e2e07d4ee891c0a17adcba04e1ad6c6d0258e35`.
- `engine/pdfstudio/page_isolation.py`: reservation/preflight implementation `1e81bc542cbf802da208f8e14b78c80b0da7cfd3`, normalized reservation failure handling `d1c9cec4b701ace1a1fcbff8bfc9a496cc5755ee`.
- `tests/test_page_isolation.py`: regression tests `41def60f2f1562c7bd3c76ba239902a6426cf7ea` cover early low-space rejection before hashing/worker spawn, allocated staging length, reservation failure cleanup, and invalid margins.
- `benchmarks/disk_space_preflight_bench.py` (`710b24c01339ac8d36e58817e0c75fb32d8dd652)) and `.github/workflows/source-check.yml` (`1fbbde66ef4dd42713a07a1baad3b26afe273f88)) add a generated 64 MiB disk-backed extraction smoke on Ubuntu and Windows.

**Exact-code CI:** [run #40323380061](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40323380061) **PASS** on Ubuntu and Windows for code/test SHA `6e2e07d4ee891c0a17adcba04e1ad6c6d0258e35`. Each OS ran **123 tests**; Ubuntu skipped four Windows-only cases and all remaining tests passed. The 64 MiB benchmark passed on both hosts:
- Linux: input `67,109,445` bytes; estimated reservation `135,267,466` bytes; free-space margin `16,777,216` bytes; output `67,109,392` bytes; elapsed `0.838 s`; source SHA unchanged; one output page; zero staging leftovers.
- Windows: same input, estimate, margin and output size; elapsed `6.830 s`; source SHA unchanged; one output page; zero staging leftovers.
- These free-space values describe the transient hosted runners, not a guaranteed minimum for users' devices.

The same exact CI run also passed the 5000-page native-text/checkpoint crash-resume/FTS benchmark on Linux: 2,638,282-byte born-digital synthetic PDF; 2,500 durable checkpoints before intentional parent exit code 23; remaining 2,500 resumed in `7.346 s`; next pass reused all 5,000 in `0.758 s`; checkpoint-to-FTS indexing `2.852 s`; source SHA unchanged and FTS integrity PASS; parent/max-child RSS `53.10/53.79 MiB` (separate process high-water marks, not summed).

**Acceptance and limitations:** F04.01, F04.02 and F04.06 remain **IN PROGRESS**, not PASS. The 64 MiB fixture is a generated PDF with a large comment stream, not a realistic scanned corpus. The estimate can be exceeded by unusual PDF rewrites, and another process can consume free space after preflight; failures still must not publish a partial final PDF. A real >=2 GiB corpus, deliberate disk-full race, Arabic/English OCR, OS reboot recovery, hostile-PDF sandboxing, Android SAF/UI, Windows GUI/EXE and physical-device acceptance remain **NOT TESTED**.

**Build decision:** no APK/EXE was built. This change is confined to the Python filesystem/worker contract, and the cross-platform tests plus disk-backed integration benchmark directly answer the question; application packaging would not add evidence for this contract.

**Google Drive handoff:** the specified `PDF Studio — HANDOFF` document was not updated because this runtime has no Google Drive/Docs write connector. Sync remains **FAILED / NOT AVAILABLE**; no successful synchronization is claimed.

**Next within B01:** keep B01 — Resource-Bounded PDF Core open. Continue realistic large-file/disk-full evidence and bounded parser/recovery checks, then independently recheck B01 exit gates. Do not open B02 yet.


## 2026-10-26 — verify staged-file identity before PDF publication (same B01 milestone)

**Risk addressed:** the parent previously accepted a nonempty staging path after worker exit. A worker defect or exploit could replace the preallocated result path with a symlink or a different inode; a size-only check would not prove that the file being published was the file the parent reserved.

**Production change:** source commit [`da1c10b597abb30707291db6ce1d31a3a90ed2ac`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/da1c10b597abb30707291db6ce1d31a3a90ed2ac) makes the parent capture the reserved file's device/inode identity. After the worker has exited, the parent requires the staged path to remain a regular file with that identity, checks the configured size ceiling and minimum PDF header length, opens it with no-follow semantics where available, verifies the opened descriptor identity, checks the `%PDF-` header, and rechecks size/mtime before publication. The parent remains the only process publishing the caller-visible output with no-clobber hard-link semantics.

**Regression coverage:** test commit [`41ab8a584878de386dd7183e3a438f49f5f353ae`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/41ab8a584878de386dd7183e3a438f49f5f353ae) adds tests for reserved inode identity, replacement-inode rejection, non-PDF-header rejection, and the integration gate failing without final output or staging leftovers.

**Exact-head verification:** [CI run #40339576523](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40339576523) **PASS** on Ubuntu and Windows. Ubuntu passed the complete POSIX suite plus 100-page, 64 MiB disk-backed, 1000-page and 5000-page checkpoint/resume/FTS checks. Windows passed the complete Windows suite plus 100-page and 64 MiB disk-backed checks. This run validates source/tests only; no Android APK or Windows EXE was built.

**Limits / NOT TESTED:** the guard validates staged-file identity and a PDF header, not a second independent full PDF parse. The worker is still not an OS sandbox and retains caller filesystem privileges; same-user adversarial races around publication, realistic >=2 GiB/scanned files, deliberate concurrent disk exhaustion, Arabic/English OCR, Android SAF/UI, Windows GUI/EXE and physical devices remain NOT TESTED. F08.01 is **IN PROGRESS**, not accepted; the full page-tool list is not implemented.

**Build decision:** no APK/EXE build was warranted. The behavior under test is the Python parent/worker filesystem contract, and the exact-head cross-platform source/integration CI directly exercises the new guard.

**Google Drive handoff:** the specified `PDF Studio — HANDOFF` Google Doc could not be updated because this runtime has no Drive/Docs write connector. Sync is **FAILED / NOT AVAILABLE**; no successful sync is claimed.

**Next within B01:** continue the same Resource-Bounded PDF Core milestone with publication-race and disk-exhaustion evidence, then independently recheck B01 exit gates. Do not open B02 yet.


## 2026-10-26 — reject symlink aliases for persistent worker locks (same B01 milestone)

**Milestone:** B01 — Resource-Bounded PDF Core remains **OPEN**. This hardening closes a concrete gap in the local SQLite worker-ownership and artifact-directory coordination paths; it does not advance B01 to acceptance.

**Production change:** `engine/pdfstudio/worker_lease.py` now routes both persistent lock files through `_open_lock_file()`. On POSIX, opening requires `O_NOFOLLOW` and fails closed if the host lacks it; the opened descriptor must be a regular file. New lock files request mode `0600`; Windows does not use POSIX permission bits. On Windows, the helper rejects existing symlink/reparse paths before opening and validates the opened regular file against a fresh path inspection. This is defense against ordinary symlink aliases, **not** a claim that every same-user Windows path-replacement race is eliminated.

**Regression tests:** `tests/test_worker_lease.py` adds one test for the SQLite job lock and one for the artifact-directory lock. Each creates a symlink to a separate sentinel file, requires lock acquisition to fail, and verifies the target bytes remain unchanged. Hosts that cannot create symlinks report an explicit test skip.

- Production commit: [`c53a0f610f694b7be37d87710a666270b7ec703d`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/c53a0f610f694b7be37d87710a666270b7ec703d).
- Test commit: [`82c953df23702533a25f32d6869407ceefb17842`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/82c953df23702533a25f32d6869407ceefb17842).
- Runtime/API reference reviewed: [Python 3.12 `os` documentation](https://docs.python.org/3.12/library/os.html), including `os.open`, `os.fstat`, and `os.lstat`. No third-party implementation or dependency was copied or added.

**Verification:** GitHub Actions run [#40347102724](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40347102724) completed **PASS** on Ubuntu and Windows for the PR merge checkout `a0e072eee1a4de364e5191293626af798e942ea9`, which includes the current checkpoint head `b6cbc6775e96aacf702afbf1b91a59a7f6e5b67c`. Both hosts ran **128 tests**; both new symlink-rejection regressions passed on both hosts. Ubuntu also passed the 100/1000/5000-page synthetic checkpoint/FTS checks and the 64 MiB disk-backed extraction; Windows passed the 100-page smoke and 64 MiB extraction. The 5000-page PDF is a 2.64 MB born-digital synthetic fixture, not an OCR/scanned or >=2 GiB workload.

**Build decision:** no APK/EXE build. This change concerns local lock-path semantics and can be tested directly in Python source/integration tests; app packaging would not answer the symlink-rejection question more strongly.

**Acceptance / remaining:** F04.03 remains **IN PROGRESS**. These tests do not prove OS-reboot recovery, multi-user/distributed locking, complete hostile-PDF sandboxing, or every Windows same-user race. Keep the persistent lock inode; never unlink lock files. B01 remains the active milestone; do not open B02.

**Google Drive handoff:** **FAILED / NOT AVAILABLE** in this runtime because no Google Docs/Drive write connector is exposed. The specified `PDF Studio — HANDOFF` document was not updated; no sync success is claimed.


## 2026-10-26 — reject hard-link aliases for persistent worker locks (same B01 milestone)

**Milestone:** B01 — Resource-Bounded PDF Core remains **OPEN**. This is a narrow ownership-integrity hardening; it does not constitute full crash/reboot acceptance.

**Production change:** commit [`06313571b5faf92402d75ecdcdc5227dbae55924`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/06313571b5faf92402d75ecdcdc5227dbae55924) makes `_open_lock_file()` reject a lock whose opened file reports `st_nlink != 1`, in addition to existing regular-file and symlink/reparse checks. This prevents two distinct lock names from silently sharing one inode through a hard-link alias. It does not prevent all same-user Windows path races and is not a sandbox.

**Regression tests:** commit [`dd0106ff36bbb58c4b9e5abdd0220451bccc4eb4`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/dd0106ff36bbb58c4b9e5abdd0220451bccc4eb4) adds two synthetic tests: worker-lock hard-link rejection and artifact-directory-lock hard-link rejection. Both assert the unrelated target bytes and two-link identity remain unchanged after rejection. Hosts that cannot create hard links explicitly skip the test.

**Exact-checkpoint CI:** push run [#40367605089](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40367605089) on checkpoint SHA `087dd23c547ad01f531823aede8cadef11ccc59a` completed **PASS** on Ubuntu and Windows. Both hosts ran **130 tests**; Ubuntu skipped four Windows-only tests, and both hard-link regressions passed on both hosts. Ubuntu also passed 100/1000/5000-page synthetic native-text checkpoints, interruption/resume/FTS integrity and the 64 MiB disk-backed extraction. The 5000-page synthetic input was 2,638,282 bytes; it interrupted after 2,500 pages, resumed the remaining 2,500 in 6.062 s, reused all 5,000 completed checkpoints in 0.769 s, and preserved the source SHA. Parent/max-child peak RSS were 53.22/53.95 MiB (not summed). Windows passed the 100-page smoke and 64 MiB disk-backed extraction; 1000/5000-page benchmarks are Linux-only. These are synthetic born-digital workloads, not OCR or realistic >=2 GiB PDFs.

**Build decision:** no APK/EXE build. This change concerns Python local filesystem lock semantics; direct cross-platform source tests are the relevant evidence and application packaging would not answer the hard-link question more strongly.

**Google Drive handoff:** **FAILED / NOT AVAILABLE** in this runtime because no Google Docs/Drive write connector is exposed. The specified `PDF Studio — HANDOFF` document was not updated; no sync success is claimed.

**Next within B01:** obtain exact-head Ubuntu/Windows CI results, then continue with publication-race and concurrent disk-exhaustion tests plus realistic large-file evidence. Keep B01 open; do not start B02.


### Verification follow-up — exact checkpoint PASS (2026-10-26)

- The pending note above is superseded by exact-head push CI [#40367605089](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40367605089), checkpoint SHA `087dd23c547ad01f531823aede8cadef11ccc59a`: **PASS** on Ubuntu and Windows.
- Both hosts: `python -m compileall -q engine tests` PASS; complete unit/source suite **130 tests PASS** (Ubuntu reports four Windows-only skips; Windows runs all 130). Both hard-link regression cases passed on both hosts.
- Ubuntu synthetic 1000-page run: 526,269-byte PDF, FTS integrity PASS, source unchanged, 0.532 s indexing, 36.06 MiB parent and 35.77 MiB max-child peak RSS.
- Ubuntu synthetic 5000-page interrupted/resume run: 2,638,282-byte PDF; interruption after 2500 pages (10.093 s), resume remaining 2500 (6.062 s), verified reuse of all 5000 (0.769 s), FTS integrity PASS, source unchanged, 53.22 MiB parent / 53.95 MiB max-child peak RSS.
- Windows: 100-page smoke and 64 MiB disk-backed output reservation PASS. No Windows 1000/5000-page benchmark in this workflow.
- Limits remain: generated native-text PDF only, not real Arabic/English OCR, not a realistic >=2 GiB corpus, not OS reboot recovery or a hostile-PDF sandbox, and not Android/Windows GUI/device acceptance. B01 remains OPEN; F04.03/F04.06 remain IN PROGRESS.


## B01 — 2026-10-26: publication-race guard and mid-write disk-full cleanup

**Active milestone:** B01 — Resource-Bounded PDF Core remains OPEN. This slice strengthens the existing extraction/merge publication contract; it does not open B02 or claim complete F08.01/F04 acceptance.

**Production:** commit [`2ceb64d06edb31e925c4a80732a36598e647e80e`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/2ceb64d06edb31e925c4a80732a36598e647e80e) adds `_publish_verified_staging_file`. It revalidates the reserved staging inode immediately before publication, uses atomic `os.link` no-clobber semantics, then checks that the caller-visible destination is a regular file with the originally reserved device/inode and revalidates its PDF header/size. If the staging path is swapped to a different regular inode at the link boundary, the unexpected hard link is removed only while it still aliases the unexpected staging inode; an unrelated racing destination is never removed.

**Regression tests:** commit [`eae2cf49fc00d34651a5bb9f989757b9a2835f29`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/eae2cf49fc00d34651a5bb9f989757b9a2835f29) adds three deterministic tests:
- a concurrent process creates the final path after preflight; publication fails with `FileExistsError` and preserves its bytes;
- the staged inode is replaced at the link boundary; the operation fails and leaves no caller-visible output;
- simulated `ENOSPC` after a partial writer output cleans the staging file and preserves the input PDF.

**Exact code+test CI:** [run #40376149983](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40376149983) **PASS** on Ubuntu and Windows. Each host ran 133 source tests; Ubuntu skipped four Windows-only tests. Both passed the 100-page smoke and 64 MiB disk-backed extraction. Linux also passed the 1000-page smoke and 5000-page interrupted/resume/FTS integration. The 5000-page fixture was 2,638,282 bytes of synthetic born-digital text, not OCR or a realistic >=2 GiB scanned PDF.

**Limits:** the disk-full test injects `ENOSPC` deterministically; it is not a real concurrent disk-exhaustion experiment. Post-link checks narrow publication races but do not create a complete same-user filesystem sandbox or prove all Windows path races impossible. F04.01/F04.02/F04.05 and F08.01 remain **IN PROGRESS**; real large/scanned corpus, actual disk-full contention, Android/Windows GUI and device acceptance remain **NOT TESTED**.

**Build decision:** no APK/EXE. This is a Python filesystem contract directly tested on both operating systems; app packaging would not answer the race/cleanup test more strongly.

**Google Drive handoff:** **FAILED / NOT AVAILABLE**. No Google Docs/Drive write connector is exposed in this runtime, so the specified `PDF Studio — HANDOFF` document was not updated. No sync success is claimed.

**Next within B01:** run a real bounded disk-pressure experiment only if it can be isolated safely on a free runner, then continue source-level resource/worker recovery gates. Keep B01 OPEN; do not start B02.


## 2026-10-26 — B01 page-operation slice: bounded page rotation

**Milestone:** B01 — Resource-Bounded PDF Core (still OPEN). This slice adds a real page transformation without replacing the existing extraction/merge contract.

- Production: **rotate_pages(source, output, pages="all", degrees=90)** supports clockwise 90/180/270-degree rotation, zero-based integer iterables or the existing one-based inclusive range syntax. It reuses B01's isolated worker, source SHA preflight/recheck, page/output caps, free-space estimate, staging inode verification and atomic no-clobber publication.
- Safety: encrypted and potentially signed PDFs remain fail-closed through shared preflight. The input PDF is never written; invalid angles/ranges and an existing destination are rejected.
- Tests added: selected-page rotation preserves page order, mediabox dimensions and input bytes; invalid angles/ranges fail without output; existing output remains untouched.
- Dependency review: uses the existing pinned pypdf==6.21.2 public PageObject.rotate API; no upstream implementation was copied and no dependency was added.
- Exact production/test SHA: 88d2afb428a4df17ab9fcb559ce8759180a03bcc. PR CI [#40385100816](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40385100816) **PASS** on Ubuntu and Windows; push CI [#40385093079](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40385093079) **PASS** on both hosts. The full source suite ran 136 tests per host; syntax checks PASS.
- Linux synthetic benchmarks from PR CI: 100 pages indexed in 0.222 s (52,655-byte PDF); 1,000 pages in 0.963 s (526,269-byte PDF); 5,000 pages indexed in 4.251 s. The 5,000-page job deliberately stopped after 2,500 pages, then resumed the remaining 2,500 in 8.418 s; the next reuse pass verified/skipped all 5,000 in 1.685 s. SQLite FTS integrity PASS, source SHA unchanged; PDF 2,638,282 bytes and SQLite 2,965,504 bytes. Parent peak RSS 53.20 MiB and max child peak RSS 54.12 MiB are separate high-water marks, not summed.
- Disk-backed smoke: synthetic 64 MiB input/output, 135,267,466-byte reservation estimate, no staging leftovers, source SHA unchanged, PASS on both OS CI jobs.
- These fixtures are synthetic born-digital PDFs; results do **not** establish OCR quality, a realistic 2 GiB scanned workload, hostile-PDF sandboxing, Android SAF/UI, Windows GUI, or device acceptance. Rotation is source-tested, not yet visually validated in either app.
- **NOT TESTED:** UI controls, visual render/orientation on Android or Windows, crop/deskew/reorder/delete/split, real scanned/large PDFs, OCR engine, and full F08.01 acceptance.
- Google Drive Handoff sync: FAILED / NOT AVAILABLE because this runtime exposes no Google Docs/Drive write connector; the specified document was not updated and no sync success is claimed.
- Next: verify current SHA on Ubuntu + Windows, fix any regression, then keep B01 active for worker ownership/recovery and realistic large-file tests. No APK/EXE build is useful for this isolated Python page-transform API.

## 2026-10-26 — B01 page-operation slice: bounded CropBox cropping

- Added `crop_pages(source, output, box, pages="all")` with the box expressed as absolute PDF user-space coordinates `(left, bottom, right, top)`. Coordinates must be finite (including safe rejection of integers too large to convert to finite floats), form a positive rectangle, and remain within every selected page's MediaBox.
- Crop changes only the PDF CropBox; it is reversible and **does not erase underlying content or provide redaction**. Encrypted and apparently signed inputs remain fail-closed through shared preflight. Original bytes are hash-checked and never written.
- Reuses the existing isolated worker, wall deadline, memory/CPU/output limits, disk preflight, same-filesystem staging identity and atomic no-clobber publication.
- Added source tests for selected-page geometry/order, invalid/non-finite/out-of-bounds boxes, existing-output preservation and source-byte integrity; added an isolated-worker integration test.
- **PASS — exact source + test SHA `ccee41dd7c350ac0b268213e77fb61fa5a78a2e6`:** CI [#40387213997](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40387213997) completed successfully on Ubuntu + Windows. Both hosts ran 140 tests; Ubuntu skipped four Windows-only tests. The crop-specific geometry, invalid-box, no-clobber and isolated-worker tests all passed. F08.01 remains IN PROGRESS because reorder/delete/split and real-reader/UI acceptance are not complete.
- **NOT TESTED:** rendered appearance in Android/Windows PDF readers, real scanned/large corpus, crop UI, and any destructive content removal. CropBox is not a secure redaction mechanism.
- No new dependency or copied third-party implementation. Uses the already pinned pypdf dependency. No APK/EXE build: the question is PDF CropBox semantics and fail-closed source behavior, directly answered by Python integration tests.
- Google Drive `PDF Studio — HANDOFF` sync remains FAILED / NOT AVAILABLE in this runtime because no Drive/Docs write connector is exposed; no successful sync is claimed.
- Continue B01; do not open B02 until B01 acceptance is complete.

## 2026-10-26 — B01 page operations: delete and reorder

- Added `delete_pages()`: selected pages are removed into a new PDF, the source is preserved, and deleting every page is rejected so no zero-page output can be published.
- Added `reorder_pages()`: requires a complete permutation of all zero-based indexes or an equivalent one-based inclusive range string. Missing, duplicate, boolean, negative and out-of-range indexes fail closed.
- Both operations reuse the isolated worker, bounded CPU/RAM/time/output, disk preflight, source SHA revalidation, private staging and atomic no-clobber publication.
- Added unit/integration regressions for page order/count, source-byte preservation, invalid selections, no zero-page output and actual worker dispatch.
- **PASS — exact source + test SHA `ba919b993dabeed298595d17702365e00358673d`:** [CI #40388250022](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40388250022) completed on Ubuntu + Windows, 145 tests per host (four Windows-only tests skipped on Ubuntu). Delete/reorder unit and isolated-worker integration regressions passed. Linux 5000-page synthetic checkpoint/resume/FTS also passed; this is not a realistic >=2 GiB or OCR corpus. F08.01 remains IN PROGRESS because split operation and Android/Windows UI/visual acceptance are still NOT TESTED.
- No APK/EXE build: the question is the page-list transformation contract, directly answered by source and isolated-worker tests. No new dependency or copied third-party implementation.
- Google Drive `PDF Studio — HANDOFF` sync remains FAILED / NOT AVAILABLE in this runtime; no Drive write is claimed.
- Continue B01; do not open B02.


## 2026-10-26 — B01 page operations: bounded explicit-range split

**Active milestone:** B01 — Resource-Bounded PDF Core remains **OPEN**. This is the next linked F08.01 slice; it does not open B02 or claim full page-tool acceptance.

**Production change:** `split_pdf(source, output_dir, ranges, base_name="split")` accepts 1–100 explicit page groups. A group may use the existing one-based inclusive range syntax or zero-based integer indexes. Every part is created by the existing isolated `extract_pages` worker, with a per-part timeout/output cap and an aggregate output-byte cap. The source SHA is checked across the split. Final filenames are preflighted as a set, staged in a private same-filesystem directory, then published with no-clobber hard links only after all parts succeed. On a later failure, no final parts are published; a publication rollback removes only outputs whose recorded identity, size, mtime and digest still match this operation. A path that changes concurrently is preserved. No new dependency or copied implementation was introduced.

**Milestone acceptance criteria for this slice:** (1) split outputs reopen and preserve requested page order; (2) original source bytes remain unchanged; (3) a pre-existing target is never overwritten and blocks the whole split before work starts; (4) an invalid later range leaves no earlier final part; (5) unsafe names and more than 100 parts fail closed; (6) aggregate output limits reject without final outputs; (7) exact-head source CI passes on Ubuntu and Windows.

**Test additions:** five synthetic tests in `tests/test_page_tools.py` cover multi-part page fidelity/source preservation, all-target preflight/no-clobber, rollback after a later invalid range, path-name/part-count validation, and aggregate output cap. Tests use generated blank PDFs only; no user documents are included.

**Performance limitation:** this first range-based splitter invokes the bounded extraction worker once per part and therefore reparses/re-hashes the source per part. It is intentionally bounded and capped at 100 parts, but it is not yet the preferred path for very large documents with many parts. A future same-worker single-pass split, split-by-bookmarks/size, and richer progress/cancel reporting remain outside this slice.

**NOT TESTED:** Android/Windows UI and device acceptance, visual rendering, bookmark/size split, realistic >=2 GiB/scanned PDFs, OCR, and crash/reboot recovery during a multi-output publication. No APK/EXE build is useful for this Python page-selection contract; source and isolated-worker integration tests directly answer it.

**Google Drive handoff:** FAILED / NOT AVAILABLE in this runtime because no Google Docs/Drive write connector is exposed; the specified PDF Studio handoff was not updated.

**Exact source/test verification:** production/test/docs commit [`bf01939f24cd96d01b92828083cd31e43098b24e`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/bf01939f24cd96d01b92828083cd31e43098b24e) passed [CI #40404012358](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40404012358) and duplicate run [#40404004103](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40404004103) on Ubuntu + Windows. Both completed successfully. `compileall` passed; Ubuntu ran 150 tests with four Windows-only skips, and Windows ran all 150 tests. All five split-specific tests passed on both hosts.

**Measured synthetic checks:** Linux 100-page smoke PASS; 1000 pages / 526,269-byte PDF indexed in 0.540 s, FTS integrity PASS, source unchanged, parent peak RSS 35.78 MiB / max child 35.55 MiB; 5000 pages / 2,638,282-byte PDF passed checkpoint interruption after 2500 pages, resume of the remaining 2500, reuse verification of all 5000, FTS integrity and unchanged source (resume 7.778 s; reuse 0.650 s; parent peak RSS 52.93 MiB / max child 53.78 MiB). Both Ubuntu and Windows passed the 64 MiB disk-backed output reservation/extraction smoke with zero staging leftovers. These are generated born-digital/comment-stream fixtures, not OCR/scanned or realistic >=2 GiB documents.

**Build decision:** no APK/EXE build. The tested behavior is the cross-platform Python page-range/output transaction; source/integration CI directly answers the split correctness and rollback question.

**Next:** keep B01 active. The next page-tool gaps are split by bookmarks/size and a more efficient single-pass splitter; Android/Windows UI and visual/device acceptance, realistic large/scanned files, OCR and crash/reboot recovery remain NOT TESTED. Do not start B02.


### 2026-10-27 — bounded page-selection input materialization (CI PASS)

- Hardened B01 page-operation APIs against oversized or infinite Python iterables: extraction, rotation, crop, delete, reorder and split consume at most the configured page/group cap plus one sentinel before rejecting input. Split's outer group count is capped at 100 before materialization; each integer-index group is capped by `max_pages`.
- The one-based page-range parser checks expanded selection size before allocating range/repeated-page entries, including `all`, and accepts an explicit `max_selections` ceiling. It also rejects overlong comma-separated selection text before token-list allocation. Intentional duplicate-page semantics remain supported within the configured cap.
- Regression tests assert rejection after consuming only cap+1 items and before worker spawn/publication; coverage includes infinite group and page-index generators, repeated ranges and oversized range text.
- Exact code/test SHA `5f875f0a7cdd2baabfeced429a626e6e55288e48`: [CI #40418270025](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40418270025) **PASS** on Ubuntu and Windows. Each host passed 154 source tests; Ubuntu skipped four Windows-only tests. Both hosts passed the 100-page smoke and disk-backed 64 MiB extraction. Linux also passed the 1000-page benchmark and the 5000-page checkpoint/crash/resume/FTS test: 2500 durable checkpoints survived the intentional process exit; resume processed the remaining 2500, then verified reuse skipped all 5000. Linux synthetic input was 2,638,282 bytes (born-digital), with source SHA unchanged and FTS integrity PASS; parent/max-child peak RSS was 53.05/53.96 MiB (not summed). Windows 100-page smoke was 0.315 s; Windows RSS is not measured by this benchmark. The 64 MiB fixture is synthetic, not scanned/OCR or a realistic >=2 GiB PDF. These results do not establish hostile-input sandboxing, Android/Windows GUI, or device acceptance. B01 remains OPEN. Google Drive Handoff sync is NOT AVAILABLE in this runtime.

## 2026-10-27 — bounded top-level bookmark split (B01 remains OPEN)

**Active milestone:** B01 — Resource-Bounded PDF Core. This slice closes the bookmark-based split sub-gate only; it does not open B02 or claim full F08.01 acceptance.

**Production changes**
- Added `inspect_top_level_bookmark_starts()` in `engine/pdfstudio/inspection.py`. PDF outline parsing runs in a child process with POSIX resource limits or a Windows Job Object, wall timeout, page-count cap, and a hard 100 top-level-bookmark cap. It rejects encrypted/signed inputs using the existing fail-closed preflight, rejects malformed/out-of-range destinations, and never sends bookmark titles or document text over IPC.
- Added `split_pdf_by_bookmarks()` in `engine/pdfstudio/page_tools.py`. Direct/top-level bookmarks define split boundaries; nested outline entries are ignored to avoid unexpectedly multiplying parts. Pages before the first bookmark become a separate preface part. Filenames remain numeric and never incorporate untrusted document titles.
- The bookmark plan returns the source SHA-256; the existing range splitter checks that pinned digest before staging any output, then retains its per-part/aggregate byte caps, private staging directory, source rechecks, no-clobber publication and rollback behavior.
- Uses the existing pinned `pypdf==6.21.2` public APIs. No new dependency or copied upstream implementation was introduced.

**Acceptance criteria for this slice**
1. Split at top-level bookmark starts while preserving page order and all page bytes/content as supported by pypdf.
2. Ignore nested outline lists; include preface pages as a distinct first part when the first bookmark is not page 1.
3. Never use bookmark titles as output paths.
4. Reject missing/malformed outlines, excessive bookmark counts, encrypted/signed inputs, and a source whose SHA changes after planning without publishing outputs.
5. Keep inspection bounded and keep final publication on the existing no-clobber split transaction.
6. Pass the exact source/test SHA on Ubuntu and Windows.

**Regression coverage:** three tests verify preface + chapter grouping and nested-bookmark handling, missing-outline rejection without outputs, and stale source-SHA rejection before publication.

**Exact source/test head:** `4d1420a2cfd7ff7cdf78021bf44eab6b667578b7`. GitHub Actions [#40432289835](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40432289835) **PASS** on Ubuntu + Windows. Each host passed 157 tests; Ubuntu skipped four Windows-only tests. Python syntax checks passed. The synthetic 100-page smoke and disk-backed 64 MiB extraction passed on both hosts. Linux 1000-page indexing passed (526,269-byte synthetic PDF; 0.986 s); the 5000-page checkpoint test passed with 2500 durable checkpoints before the intentional exit, 2500 resumed pages, 5000 verified pages, SQLite FTS integrity PASS and unchanged source SHA. The 5000-page fixture is a 2.64 MB born-digital synthetic PDF, not a scanned or realistic >=2 GiB document. Linux process high-water marks were 53.00 MiB parent / 54.10 MiB maximum child (not summed); Windows RSS is not measured by this benchmark.

**Build decision:** no APK/EXE build. The question is bounded Python outline parsing and split-transaction correctness; the cross-platform source/integration suite directly exercises those behaviors. Packaging either app would not strengthen this test.

**NOT TESTED / remaining in B01:** split by target byte size; a more efficient single-pass multi-part split; Android/Windows page-tool UI and visual acceptance; realistic large/scanned PDFs including >=2 GiB where resources permit; OCR Arabic/English; crash/reboot during multi-output publication; device acceptance and hostile-PDF sandboxing. Keep B01 OPEN and do not start B02 until the remaining B01 gates have evidence.

**Google Drive handoff:** **FAILED / NOT AVAILABLE**. This runtime exposes no Google Docs/Drive write connector, so the specified `PDF Studio — HANDOFF` document was not updated. No sync success is claimed; development continues.

## 2026-10-27 — B01 staged-output integrity gate (PASS; milestone remains OPEN)

**Active milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity`.

### Production change
- `_publish()` now reopens every staged PDF with the pinned pypdf parser before publication, checks the PDF header and readable page tree, and verifies the exact expected page count.
- The contract is applied to extraction, merge, rotation, CropBox crop, deletion and reorder. A malformed output or page-count mismatch fails closed; the staging file is removed and source SHA verification remains mandatory.
- This is structural/page-count validation only. It does not prove OCR text accuracy, visual fidelity, preservation of every content stream, or secure redaction.

### Regression evidence
- Added tests for a structurally valid one-page PDF supplied where two pages were expected, and for malformed PDF bytes. Both require staging cleanup and unchanged source.
- Updated the injected ENOSPC regression to pass the new expected-page contract; the initial CI failure was a test helper missing that required argument, not a production regression.
- Exact production/test checkpoint: `6126c6e035cc640fa9d986526d0194c91a955f3d`.
- GitHub Actions [#40443505192](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40443505192): **PASS** on Ubuntu and Windows. Each host ran 159 tests; Ubuntu skipped four Windows-only tests. Syntax checks passed.
- 64 MiB disk-backed synthetic extraction: **PASS** on both hosts; input 67,109,445 bytes, output 67,109,392 bytes, one output page, source SHA unchanged, zero staging leftovers.
- Linux synthetic 5,000-page checkpoint/crash/resume/FTS integration: **PASS**. The 2,638,282-byte born-digital PDF was interrupted after 2,500 durable checkpoints; resume processed the remaining 2,500, then verified all 5,000 artifacts on the reuse pass. FTS integrity and source SHA checks passed. Indexing 3.104 s; resume 6.934 s; completed-stage reuse 1.216 s; checkpoint FTS indexing 0.932 s. Parent/max-child RSS high-water marks were 52.92/53.95 MiB (separate measures, not summed).
- Windows 100-page native-text smoke: **PASS**, 0.294 s indexing; Windows RSS is not measured by this benchmark.

### B01 milestone acceptance and remaining gates
- **PASS:** cross-platform source suite, structural/page-count validation for all six direct page operations, no-clobber/cleanup regressions, synthetic 100-page smoke on both hosts, 64 MiB disk-backed smoke on both hosts, Linux 1,000/5,000-page synthetic checkpoint/resume/FTS checks.
- **IN PROGRESS:** complete F08.01 page-operation acceptance; split by target byte size and a more efficient single-pass multi-part split; end-to-end output text-layer/page-order checks; realistic scanned and >=2 GiB corpus where resources permit; genuine disk-pressure contention; crash/reboot during multi-output publication.
- **NOT TESTED:** Arabic/English OCR and searchable overlays, Android SAF/native UI, Windows GUI/EXE, Xiaomi Pad 5 behavior, visual rendering, hostile-PDF OS sandbox, and full F01–F10 product acceptance. Synthetic born-digital results are not substitutes for these gates.
- **Build decision:** no APK/EXE build. This change is in the Python processing engine and the question is output parseability/page-count correctness; the exact cross-platform source suite directly exercises it. Packaging would not add evidence for this contract.
- **Google Drive `PDF Studio — HANDOFF` sync: FAILED / NOT AVAILABLE.** This runtime exposes no Google Docs/Drive write connector, so the specified document was not updated. No sync success is claimed.
- **Next:** continue B01 and close its remaining source/resource gates before opening B02. Keep PR #2 draft/open; do not merge or modify `main`.

## 2026-10-27 — staged-path identity race during failed PDF publication

**Active milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity` (OPEN).

- **Production change:** `_publish()` now pins the staging file's device/inode before writing, rejects non-regular paths, opens with `O_NOFOLLOW` where the host provides it, checks descriptor/path identity before and after writing, and validates through a separately opened descriptor. On failure it removes the staging path only if that path still names the exact inode observed at entry; a replacement file is preserved.
- **Regression:** added `test_publish_preserves_replacement_path_when_writer_fails`. It deterministically moves the original staging inode away, places unrelated bytes at the old path, injects a write failure, and verifies cleanup preserves both files. The fixture closes the open descriptor before rename so it is portable to Windows.
- **Production commit:** [`94c04c5e35f0f430fad72d55a0f66d89a9efc805`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/94c04c5e35f0f430fad72d55a0f66d89a9efc805).
- **Test checkpoint:** [`ef370cc4b03f692569e5a5ffda9436d219b22dd4`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/ef370cc4b03f692569e5a5ffda9436d219b22dd4).
- Initial PR source-check run [#40454363285](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40454363285) **FAILED** because the regression fixture incorrectly treated a file descriptor's integer `.name` as a path; production syntax checks passed and the new test exposed this fixture-only defect. Corrected the fixture to use its known staging path in test commit [\`2663794596dcffae2d71974d2884ead795579ead\`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/2663794596dcffae2d71974d2884ead795579ead). Exact rerun [#40454542353](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40454542353): **PASS** on Ubuntu + Windows; 160 tests per host (four Windows-only skips on Ubuntu), syntax checks and synthetic scale/disk-backed checks passed.
- **Scope/limits:** this narrows a staging-path replacement race and prevents cleanup from deleting a different inode. It is not an OS sandbox and does not claim protection from every same-user Windows filesystem race. Existing output structural/page-count checks remain in place.
- **B01 status:** **OPEN / IN PROGRESS**. Split-by-target-size, more efficient multi-part splitting, text-layer fidelity, realistic large/scanned corpus, Arabic/English OCR, reboot recovery, Android/Windows GUI and device acceptance remain unaccepted or NOT TESTED.
- **Build decision:** no APK/EXE build; this is a Python filesystem-race contract directly covered by source tests. Packaging would not answer the test question.
- **Google Drive `PDF Studio — HANDOFF` sync:** **FAILED / NOT AVAILABLE**. No Google Docs/Drive write connector is exposed in this runtime; the Drive document was not changed and no sync success is claimed.

## 2026-10-27 — target-byte PDF splitting (source CI PASS; B01 remains OPEN)

**Active milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity`.

### Production implementation
- Added `split_pdf_by_size()` in `engine/pdfstudio/page_tools.py`. It obtains the page count and source SHA through the existing isolated inspection worker, then finds ordered page-group boundaries using bounded binary-search extraction probes.
- Each candidate is written by the existing resource-limited page worker and checked against the target part-byte ceiling. The aggregate byte cap is accounted for while planning; a single page that cannot fit fails closed instead of being dropped.
- The final parts are produced by the existing staged multi-part publisher, which pins the planned source SHA, preflights output names, validates every PDF/page count, publishes without clobbering and rolls back only outputs still matching this operation.
- Probe cleanup checks the candidate's regular-file device/inode identity before unlinking. No new dependency was added.
- The splitter is bounded by the configured page cap and existing 100-part limit, but it re-serializes candidate ranges; **single-pass optimization remains open**.

### Regression and CI evidence
- Added tests for per-part byte caps, ordered complete page coverage using distinct page widths, unchanged source SHA, no leftover staging directories, too-small single-page caps (no final output), aggregate-cap failure with no partial publication, invalid cap values and no-clobber behavior.
- Production change: [`a9f4e20b239a6ee7c0ebf0eee30fd48abffe2858`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/a9f4e20b239a6ee7c0ebf0eee30fd48abffe2858); initial regression tests: [`b81173aa5a22cc128acbd330a9ccc798338545bf`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/b81173aa5a22cc128acbd330a9ccc798338545bf); strengthened ordering and aggregate-cap tests: [`2f5b0345ad4305311d02d3e616e77db9a55eb403`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/2f5b0345ad4305311d02d3e616e77db9a55eb403).
- Exact final code/test CI [#40466583497](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40466583497): **PASS** on Ubuntu + Windows, 164 tests per host; Ubuntu skipped four Windows-only tests. Syntax checks PASS. The ordered multi-part and aggregate-cap/no-partial-publication regressions passed on both hosts.
- 100-page native-text smoke and 64 MiB disk-backed synthetic extraction: **PASS** on both hosts; source SHA unchanged and no staging leftovers.
- Linux 1,000/5,000-page synthetic index/checkpoint-resume/FTS checks: **PASS**. The 5,000-page fixture is 2.64 MB of born-digital synthetic text; it is not OCR, a scanned corpus or a realistic >=2 GiB document.
- No APK/EXE build: this round's question was whether byte-bounded page splitting preserves complete ordered pages and fails closed under caps; source/integration tests directly exercise it. Packaging would not answer that question.

### B01 acceptance and remaining gates
- **PASS:** source-level split-by-size cap/order/source-preservation tests on Ubuntu and Windows; existing page operation suite; synthetic cross-platform scale/disk-backed checks and Linux checkpoint/resume/FTS.
- **IN PROGRESS:** efficient single-pass/multi-part splitting, comprehensive output text-layer and page-order verification across operation types, genuine disk-pressure contention, and interrupted multi-output publication.
- **NOT TESTED:** realistic scanned/large PDFs including >=2 GiB where resources permit, Arabic/English OCR, crash/reboot acceptance, Android SAF/native UI, Windows GUI/EXE, visual fidelity and Xiaomi Pad 5 behavior. Synthetic born-digital fixtures do not satisfy these gates.
- **Google Drive `PDF Studio — HANDOFF` sync: FAILED / NOT AVAILABLE.** No Google Docs/Drive write connector is exposed in this runtime; the specified document was not updated and no sync success is claimed.
- **Next:** keep B01 OPEN and finish the remaining B01 resource/output-integrity gates before opening B02. PR #2 stays draft/open; do not merge or modify `main`.



## 2026-10-27 — native-text preservation at the PDF publication boundary (source CI PASS; B01 remains OPEN)

**Active milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity`. This change stays inside B01; B02 is not opened.

### Production change
- `_publish()` now computes a SHA-256 digest for each page's extractable native text before serialization, then reopens the generated PDF and compares per-page digests in page order after structural/page-count validation.
- A changed or missing text layer on a page that was extractable before writing fails closed and removes the staging inode. Only per-page digests are retained; extracted document text is not persisted, logged or sent to services.
- Image-only pages and pages where pypdf text extraction raises are deliberately marked unverified by this check. It is a native-text preservation guard, **not OCR, a visual comparison, signature validation or an OS sandbox**.

### Regression evidence
- Added a born-digital synthetic text PDF round-trip test that verifies reordered extraction preserves both text and page order.
- Added a negative regression using an injected writer that silently drops a text layer; publication is rejected and staging is removed while the source remains intact.
- Production implementation: [`9f2ed8c571ea20ba5c8518ec18380462ec19adf0`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/9f2ed8c571ea20ba5c8518ec18380462ec19adf0).
- Test fixture contract correction: [`08caabd15ac47dc47415b1969fa60d6d3afcd9fb`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/08caabd15ac47dc47415b1969fa60d6d3afcd9fb). The first CI attempt [#40479528200](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40479528200) exposed two existing fake-writer fixtures without a `pages` interface; the fixtures were corrected to conform to the `PdfWriter` contract. This was a test-double error, not a production regression.
- Exact final source/test head [`08caabd15ac47dc47415b1969fa60d6d3afcd9fb`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/08caabd15ac47dc47415b1969fa60d6d3afcd9fb): [CI #40479737258](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40479737258) **PASS** on Ubuntu and Windows; 166 tests per host, four Windows-only tests skipped on Ubuntu. Syntax checks PASS; 100-page and 64 MiB disk-backed smoke PASS on both hosts; Linux 1,000/5,000-page synthetic checkpoint/resume/FTS PASS. The 5,000-page fixture is 2.64 MB born-digital synthetic data, not a scanned or realistic >=2 GiB corpus.
- No APK/EXE build: this round's question was whether native text survives page serialization and whether publication rejects a dropped text layer. The source suite directly tests that contract on both hosts; packaging would not add evidence.

### B01 acceptance and remaining gates
- **PASS:** cross-platform page-tool source suite and new native-text round-trip/text-loss regressions on the exact SHA above.
- **IN PROGRESS:** optimized single-pass size splitting, broader text/page-order fidelity checks across complex PDFs and operations, disk-pressure contention and interrupted multi-output publication.
- **NOT TESTED:** image-only/scanned pages under real OCR; Arabic/English OCR quality; unsupported/complex font encodings not extractable by pypdf; realistic scanned/large PDFs including >=2 GiB; OS reboot recovery; hostile-PDF sandboxing; Android SAF/native UI; Windows GUI/EXE; visual fidelity and Xiaomi Pad 5 runtime.
- **Google Drive `PDF Studio — HANDOFF` sync: FAILED / NOT AVAILABLE.** No Google Docs/Drive write connector is exposed in this runtime; the specified Drive document was not updated and no sync success is claimed.
- **Next:** continue B01 output-integrity and resource gates before opening B02. PR #2 remains draft/open; no merge, auto-merge or `main` changes.

## 2026-10-27 — identity-safe staging reservation cleanup (B01 remains OPEN)

**Active milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity`. No B02 work started.

### Production change
- `_reserve_staging_file()` captures the created descriptor's device/inode before allocation, uses `O_NOFOLLOW` where supported, verifies descriptor identity/size and that the staging pathname still names the same regular file after allocation.
- On allocation failure, cleanup removes only the original reservation inode. If the path was replaced concurrently, the replacement is preserved instead of being unlinked by name.
- This narrows a cleanup race; it is not an OS sandbox and does not claim protection against every same-user filesystem race.

### Regression and CI evidence
- Production commit: [`9183094f87a30316c5466edeb90fa243d5e4dc43`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/9183094f87a30316c5466edeb90fa243d5e4dc43).
- Final portable regression-test commit: [`59ed0017e25102559e772760990d36a701436957`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/59ed0017e25102559e772760990d36a701436957). The injected ENOSPC case replaces the reservation path and verifies the unrelated replacement survives cleanup.
- Initial CI [#40494304096](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40494304096) exposed a Windows-only test-fixture issue: the fixture attempted to rename an open file. The fixture was corrected to close its injected descriptor before the deterministic path replacement; this was a test portability failure, not a production-code failure.
- Exact final source/test head [`59ed0017e25102559e772760990d36a701436957`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/59ed0017e25102559e772760990d36a701436957): [CI #40494627830](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40494627830) **PASS** on Ubuntu and Windows, 167 tests per host. Syntax checks PASS; 100-page smoke and 64 MiB disk-backed extraction PASS on both hosts; Linux 1,000/5,000-page synthetic checkpoint/crash/resume/FTS PASS. The 5,000-page fixture is 2.64 MB born-digital synthetic data; it is not a realistic scanned/2 GiB corpus. Windows process RSS is not measured.
- No APK/EXE build: the question was whether failed reservation cleanup can delete a concurrently substituted path. The cross-platform source regression directly tests this filesystem contract; packaging would not add evidence.

### B01 acceptance and remaining gates
- **PASS:** identity-bound reservation cleanup regression and full source CI on Ubuntu/Windows.
- **IN PROGRESS:** optimized size splitting, broader real-PDF text/page-order fidelity, disk-pressure contention and interrupted multi-output publication.
- **NOT TESTED:** actual OS reboot recovery; realistic scanned/large PDFs including >=2 GiB; Arabic/English OCR quality; hostile-PDF sandboxing; Android SAF/native UI; Windows GUI/EXE; visual fidelity and Xiaomi Pad 5 runtime.
- **Google Drive `PDF Studio — HANDOFF` sync: FAILED / NOT AVAILABLE.** No Google Docs/Drive write connector is exposed in this runtime; the specified Drive document was not updated and no sync success is claimed.
- **Next:** continue B01 output-integrity and resource gates before opening B02. PR #2 remains draft/open; no merge, auto-merge or `main` changes.



## 2026-10-27 — Reject out-of-range SQLite checkpoints (same B01 milestone)

**Risk found:** the normal checkpoint writer enforces `0 <= page_index < page_count`, but SQLite's table constraint only enforces non-negative indices. A malformed or manually edited journal row beyond the job's page count could be reported as a valid recovery page and appear in `resumable_pages()`.

**Production fix:** `JobJournal.recovery_snapshot()` now marks out-of-range rows invalid; `resumable_pages()` never returns them; `verified_checkpoint()` rejects an index at or beyond the immutable job page count. This is defensive journal validation, not automatic recovery authorization. Production commit: [`d5af56ebc3c9a38eafff694e90c747f12148746e`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/d5af56ebc3c9a38eafff694e90c747f12148746e).

**Regression test:** inserted a synthetic out-of-range row directly into SQLite to bypass the public API; recovery reports it as invalid, still reports the real missing pages, resume candidates exclude it, and single-page verification rejects it. Test commit: [`cde91cb1c485dbbbf713820f245b6c99d5e9dbfe`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/cde91cb1c485dbbbf713820f245b6c99d5e9dbfe).

**Exact CI:** [run #40520639306](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40520639306) **PASS** on Ubuntu and Windows, 168 tests per OS. Linux also passed the synthetic 1000-page check and 5000-page abrupt-process interruption/resume/FTS check: 2,500 durable pages were reused and only 2,500 remaining pages reprocessed; source SHA and FTS integrity stayed valid. Both hosts passed the 64 MiB disk-backed extraction with unchanged source SHA and zero staging leftovers. The 5000-page PDF is 2.64 MB born-digital synthetic data, not OCR or a realistic >=2 GiB scan.

**Status:** B01 remains OPEN / PARTIAL. Real Arabic/English OCR, realistic >=2 GiB scanned documents, OS-reboot recovery, hostile-PDF sandboxing, Android SAF/UI, Windows GUI/EXE and device acceptance remain NOT TESTED. No APK/EXE build was warranted for this Python/SQLite journal-contract change; source CI directly exercised the relevant behavior on both operating systems.

**Google Drive Handoff:** FAILED / NOT AVAILABLE in this runtime because no Google Docs/Drive write connector is exposed. The specified PDF Studio Handoff was not updated; this failure is recorded here rather than claiming sync success. No user documents or secrets were added to the public repository.


## 2026-10-27 — Atomic reservation of final output paths (same B01 milestone)

- **Milestone:** `B01 — Resource-Bounded PDF Core & Output Integrity` remains **OPEN / PARTIAL**. No B02 work started.
- **Risk:** `create_job()` checked whether the output file existed before hashing the source, but did not reserve that destination in SQLite. Two simultaneous creators could both pass the filesystem check and journal different jobs targeting the same not-yet-created final path.
- **Production fix:** the final insert now runs under `BEGIN IMMEDIATE`, rechecks that the destination has not appeared, and rejects any output path already reserved by a journaled job. This serializes `JobJournal.create_job()` reservations; the final output publisher must still retain its independent atomic no-clobber check.
- **Regression:** a barrier-synchronized two-thread test creates jobs against one shared destination. Exactly one creator succeeds, the other receives `FileExistsError`, and SQLite contains exactly one reservation for that path.
- **Code + test commit:** [`3c9db016f72d4b908ac277231d2e19c8f9932593`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/3c9db016f72d4b908ac277231d2e19c8f9932593).
- **Exact CI:** PR [#40529062317](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40529062317) and push [#40529054693](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40529054693) **PASS** on Ubuntu + Windows: 169 tests per host (Ubuntu skips four Windows-only tests). The concurrent reservation regression passed on both. 100-page, 64 MiB disk-backed extraction and Linux 1000/5000-page checkpoint/crash/resume/FTS benchmarks also passed.
- **Limits:** the 5000-page fixture is a 2.64 MB born-digital synthetic PDF, not OCR, a realistic >=2 GiB scan, reboot recovery or hostile-input sandbox testing. Android SAF/UI, Windows GUI/EXE and physical-device acceptance remain **NOT TESTED**.
- **Build decision:** no APK/EXE build; this Python/SQLite transaction contract is directly exercised on both host OSes, and application packaging would not provide stronger evidence for this change.
- **Google Drive Handoff:** **FAILED / NOT AVAILABLE**; this runtime has no Google Docs/Drive write connector. The named PDF Studio Handoff was not modified. No user documents or secrets were added to the public repository.


## 2026-10-27 checkpoint addendum — Windows output-path aliases and journal migration

- **Milestone:** B01 — Resource-Bounded PDF Core & Output Integrity remains **OPEN / PARTIAL**; do not open B02.
- **Production commit:** `c94da98af37b2ec1bb52b38f0decf19ce5116762` introduced indexed `output_key` reservations. Follow-up hardening at `2f620d58650c7e1676c2f7e2b600ef735456339a` serializes the old-journal schema migration with `BEGIN IMMEDIATE`, preventing two processes from racing the `ALTER TABLE`/backfill.
- **Behavior:** retain the user's original output path in `jobs.output`, but compare a platform-normalized key in SQLite. On Windows, `Output-Case-Alias.PDF` and `output-case-alias.pdf` now contend for one reservation; the indexed key avoids scanning all job rows. Existing B00/B01 journals are migrated/backfilled without dropping job history. The index is intentionally non-unique so pre-existing legacy aliases do not prevent opening the journal; new reservations fail closed when any matching key exists.
- **Regression tests:** Windows case-alias reservation; legacy six-column journal migration/backfill and duplicate reservation rejection. The first CI attempt `40538386354` exposed a test-only Windows cleanup error because the fixture left its SQLite connection open. The fixture now explicitly closes it; this was not a production-journal failure.
- **Exact final code/test CI:** [#40538725107 PASS](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40538725107), Ubuntu + Windows, **171 tests per host**. Linux reports five platform skips, including the Windows-only path-case test; Windows executed that regression and it passed.
- **Scale evidence from the same CI:** synthetic 100-page smoke PASS on both OSes; 64 MiB disk-backed extraction PASS on both (source SHA unchanged, zero staging leftovers); Linux synthetic 1000- and 5000-page checkpoint/crash/resume/FTS PASS. The 5000-page fixture is a 2.64 MB born-digital synthetic PDF, not a scanned/OCR or >=2 GiB corpus. Peak RSS is measured only on Linux and parent/max-child values are separate high-water marks, not summed.
- **Acceptance still NOT TESTED:** real Arabic/English OCR execution and searchable overlay; realistic >=2 GiB scanned PDFs; OS reboot recovery; hostile-PDF OS sandbox; Android SAF/UI/APK/device acceptance; Windows GUI/EXE/device acceptance; full F01–F10.
- **Build decision:** no APK/EXE build. This change is a Python/SQLite path-reservation and schema-migration contract, directly covered by source tests on both target hosts; packaging would not answer a stronger runtime question.
- **Google Drive Handoff:** `PDF Studio — HANDOFF` remains **FAILED / NOT AVAILABLE** because this runtime exposes no Google Docs/Drive write connector. The specified Drive document was not updated; no successful sync is claimed.
- No paid tools/runners, user documents, secrets, merge, auto-merge, or `main` changes.


## 2026-10-27 follow-up — Win32 trailing-dot/space output aliases

- **Milestone:** \`B01 — Resource-Bounded PDF Core & Output Integrity\` remains **OPEN / PARTIAL**; B02 stays closed.
- **Risk addressed:** ordinary Win32 file APIs treat trailing dots/spaces in path components as aliases in common path namespaces. Distinct strings can therefore refer to one destination even after case-folding. Reference: Microsoft Learn, [Naming Files, Paths, and Namespaces](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).
- **Production + regression commit:** [\`45ca1ea3f2cc8c6b9b7941deb8fc6346f47de68c\`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/45ca1ea3f2cc8c6b9b7941deb8fc6346f47de68c). The Windows destination key now strips trailing dots/spaces from ordinary path components before indexed reservation comparison; extended/device namespace prefixes retain separate semantics. User-visible \`jobs.output\` is unchanged.
- **Regression:** reserve \`Trailing-Alias.pdf\`, then verify both \`Trailing-Alias.pdf.\` and \`Trailing-Alias.pdf \` fail with \`FileExistsError\` rather than creating a second SQLite reservation. This test runs on Windows, where the relevant Win32 semantics apply.
- **Exact CI:** [source-only run #40558639455](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40558639455) **PASS** on Ubuntu + Windows; 172 tests per host. Ubuntu skipped six Windows/platform-specific cases; Windows executed the new trailing-dot/space regression and passed it.
- **Measured scale evidence from the same run:** synthetic 100-page smoke and 64 MiB disk-backed extraction PASS on both hosts; Linux 1000-page and 5000-page abrupt-process checkpoint/resume/FTS PASS. The 5000-page born-digital fixture is 2.64 MB; interruption after 2,500 checkpoints resumed only the remaining 2,500 and later reused all 5,000. Linux parent/max-child peak RSS was 52.89/54.00 MiB (separate high-water marks, not summed). This is not OCR or realistic >=2 GiB scan evidence.
- **Build decision:** no APK/EXE build; this is a Python/SQLite path-key contract, directly tested on both target host OSes.
- **Still NOT TESTED:** real Arabic/English OCR, realistic >=2 GiB scanned PDFs, OS-reboot recovery, hostile-input sandbox, Android SAF/UI/device, Windows GUI/EXE/device and full F01–F10.
- **Google Drive Handoff:** **FAILED / NOT AVAILABLE** because this runtime has no Google Docs/Drive write connector. The specified \`PDF Studio — HANDOFF\` was not updated; no sync success is claimed.
- No paid tools/runners, user documents, secrets, merge, auto-merge or \`main\` changes.

## 2026-10-27 checkpoint addendum — immutable checkpoint hard-link identity (B01 remains OPEN)

- **Milestone:** B01 — Resource-Bounded PDF Core & Output Integrity remains **OPEN / PARTIAL**. Do not open B02.
- **Production commit:** [`b5721ff174a87c3de81af8f8cd4e154a35ea3d13`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/b5721ff174a87c3de81af8f8cd4e154a35ea3d13). **Regression-test commit:** [`68b2a7850f72a992805ffcdaf2122dd152308f4c`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/68b2a7850f72a992805ffcdaf2122dd152308f4c).
- **Risk addressed:** string-path uniqueness did not detect two hard links to the same physical checkpoint file. Reusing that file for another page/job could let later writes corrupt bytes already trusted by an earlier checkpoint.
- **Implementation:** added a separate SQLite `checkpoint_artifact_identity` map keyed by the existing (job, page, stage) key, indexed by device/inode identity when the filesystem exposes a useful inode. Existing checkpoint rows are backfilled transactionally on journal open; missing legacy artifacts remain represented with a NULL identity and continue to fail normal SHA verification. The original checkpoint schema and artifact paths remain intact. Reuse of the same file identity across different pages/jobs is rejected; changing bytes in-place at an existing checkpoint is also rejected. Where inode identity is unavailable, the existing resolved-path check remains the fallback.
- **Regression coverage:** a real temporary hard link is submitted as a second page and as another job; both attempts must fail while the original checkpoint remains resumable. The legacy-journal migration test verifies the identity map is backfilled without losing checkpoint data. Existing same-path mutation now explicitly verifies fail-closed behavior.
- **Exact source/test CI:** [run #40576225759 PASS](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40576225759), Ubuntu + Windows, **173 tests per host**. Ubuntu skipped six platform-specific cases; Windows executed the hard-link regression. The 100-page and 64 MiB disk-backed extraction checks passed on both hosts. Linux 1000/5000-page checkpoint/crash/resume/FTS checks passed; the 5000-page born-digital synthetic PDF (2,638,282 bytes) interrupted after 2,500 checkpoints and resumed the remaining 2,500, then reused all 5,000 verified checkpoints.
- **Evidence limits / NOT TESTED:** this is synthetic born-digital data, not OCR or a realistic >=2 GiB scan. Real Arabic/English OCR, OS-reboot recovery, hostile-PDF OS sandboxing, Android SAF/UI/APK/device, Windows GUI/EXE/device, and full F01–F10 acceptance remain NOT TESTED.
- **Build decision:** no APK/EXE build. This is a Python/SQLite checkpoint-integrity contract directly tested on both target hosts; packaging would not provide stronger evidence.
- **Google Drive Handoff:** `PDF Studio — HANDOFF` synchronization remains **FAILED / NOT AVAILABLE** because no Google Docs/Drive write connector is exposed in this runtime. The specified Drive document was not updated; no sync success is claimed.
- No paid tools/runners, user documents, secrets, merge, auto-merge, or `main` changes.


## 2026-10-28 — Revalidate checkpoint artifact identity during recovery (B01 remains OPEN)

- **Risk addressed:** previously, a checkpoint file could be replaced at the same path by a different file with byte-identical contents. SHA-256 alone still matched, so `verified_checkpoint()`, `resumable_pages()` and the read-only recovery snapshot could treat the replacement as the original immutable artifact.
- **Production + regression commit:** [`9a7e7d40b0b3d2b5feaf3a66d85b24d12eae54ee`](https://github.com/amroalhamedi2022-hue/PDF-Studio/commit/9a7e7d40b0b3d2b5feaf3a66d85b24d12eae54ee).
- **Implementation:** verification now compares the recorded device/inode identity with identity both before and after hashing. Recovery and resumable-page selection reject identity changes even when the bytes still hash correctly. Re-registering a replaced file at the same checkpoint path cannot silently refresh the stored identity. Filesystems that expose no useful inode retain the documented SHA/path fallback.
- **Regression:** `test_replaced_checkpoint_path_with_identical_bytes_is_not_trusted` replaces a checkpoint file with a different inode containing the same bytes, then verifies that single-page validation, resumable-page selection and recovery all fail closed.
- **Exact CI:** [run #40594184983 PASS](https://github.com/amroalhamedi2022-hue/PDF-Studio/actions/runs/40594184983), Ubuntu + Windows, **174 tests per host**; Ubuntu skips six platform-specific cases. The new byte-identical replacement regression passed on both.
- **Measured synthetic evidence from that run:** 100-page native-text smoke PASS on Linux/Windows (indexing 0.217s / 0.337s); 1000-page Linux indexing 0.751s; 5000-page abrupt-process interruption/resume/FTS PASS on Linux. The 5000-page fixture is 2,638,282 bytes, interrupted after 2,500 checkpoints, resumed the remaining 2,500, then reused all 5,000 verified checkpoints; source SHA and FTS integrity stayed valid. Parent/max-child peak RSS was 54.31 MiB (separate high-water metrics, not summed). Disk-backed 64 MiB extraction PASS on both hosts (0.737s Linux / 3.077s Windows), unchanged source SHA and zero staging leftovers.
- **Evidence limit / NOT TESTED:** the benchmark is born-digital synthetic text, not OCR or a realistic >=2 GiB scan. Real Arabic/English OCR, OS-reboot recovery, hostile-PDF OS sandboxing, Android SAF/UI/APK/device, Windows GUI/EXE/device and full F01–F10 acceptance remain NOT TESTED. B01 stays OPEN / PARTIAL; B02 stays closed.
- **Build decision:** no APK/EXE build; this Python/SQLite integrity contract was directly exercised on both host OSes, so packaging would not answer a stronger question.
- **Google Drive Handoff:** `PDF Studio — HANDOFF` sync remains **FAILED / NOT AVAILABLE** because this runtime exposes no Google Docs/Drive write connector. The specified Drive document was not updated; no sync success is claimed.
- No paid tools/runners, user documents, secrets, merge, auto-merge or `main` changes.
