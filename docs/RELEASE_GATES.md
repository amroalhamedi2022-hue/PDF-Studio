# Release and Packaging Gates

**B00–B10:** no Android APK/AAB, Windows EXE/MSI installer, GitHub Actions packaging, automatic release or distribution. Allowed: source-only tests, static checks, lint, pure-code benchmarks, license and dependency scans.

**Final Ready** requires all locked F01–F10 requirements PASS or explicit owner-approved exception in docs/ACCEPTANCE.md, independent recheck, security tests, major stress corpus benchmarks, visual QA, UX/accessibility and license clearance.

**B11:** only after Final Ready, run final packaging workflow by explicit manual invocation, then device-level install/launch/save/reopen/export/OCR/print and regression checks on Xiaomi Pad 5 and Windows 11. Rebuild only for a confirmed final defect; never declare successful device testing if unavailable.

**Delivery:** verifiable APK/EXE artifact or linked GitHub workflow artifact, sha256, version, commit SHA, CI runs, status of every feature, explicit NOT TESTED limitations. Never end work solely because packaging succeeds.
