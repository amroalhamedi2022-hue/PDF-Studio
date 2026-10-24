# B00 — Research, Reuse and Licensing Register
Updated: 2026-10-24. Research references are **candidates**, not bundled code. Versions, vulnerabilities and model-specific weight licenses require rechecking at integration time.

| Candidate | Role and engineering decision | License/concern | Source |
|---|---|---|---|
| OCRmyPDF | Preferred Windows searchable-PDF adapter; preserve existing content and OCR in selected modes. No Android bundling assumption. | MPL-2.0; modifications and bundled dependency notices; Python >=3.11 in current docs | https://github.com/ocrmypdf/OCRmyPDF |
| Tesseract / tessdata | Primary fully offline AR+EN OCR baseline for benchmarking and Android adapter study | Apache-2.0 code; review model/traineddata licenses separately | https://github.com/tesseract-ocr/tesseract |
| PaddleOCR / PP-OCRv5 Arabic | Compare against Tesseract for Arabic/mixed OCR and structural analysis; do not presume newer PP-OCRv6 covers Arabic | Apache-2.0 code; weights, Paddle runtime, GPU support and runtime footprint must be checked | https://github.com/PaddlePaddle/PaddleOCR |
| PaddleOCR-VL/PP-Structure | Advanced layout/table/formula/handwriting candidates for optional local Windows engine only after profiling | model cards, model weight terms, dependencies and RAM/GPU before adoption | https://github.com/PaddlePaddle/PaddleOCR |
| Docling | Structured document understanding and reading order; optional and independently benchmarked | MIT source; every downloaded model weight requires its own review | https://github.com/docling-project/docling |
| qpdf | PDF low-level structural operations and recovery; not renderer or text extractor | Apache-2.0 | https://github.com/qpdf/qpdf |
| pikepdf | Python integration to qpdf; metadata/pages/security primitives | MPL-2.0; wheels include other third-party libraries/notice obligations | https://github.com/pikepdf/pikepdf |
| pypdf | Lightweight source-only Python PDF API and preflight testing, not a PDF renderer | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| Apache PDFBox | Candidate page/text/annotation backend for native ecosystem | Apache-2.0 | https://pdfbox.apache.org/ |
| Stirling PDF | Feature checklist/UI workflow inspiration only. **Never copy proprietary portions.** | Open-core; directories carry distinct licenses | https://github.com/Stirling-Tools/Stirling-PDF/blob/main/LICENSE |
| PyMuPDF | Fast candidate renderer/edit operations but **not default selection** until legal review | AGPL/commercial license impacts redistribution | https://pymupdf.io/licensing/ |
| PySide6 / Qt | Windows native UI candidate; analyze LGPL-3/commercial modules and redistribution | Binary, plugin and distribution obligations differ | https://doc.qt.io/qtforpython-6/licenses.html |
| AndroidX PDF | Native Android display candidate; platform support API/feature maturity to be verified | Review relevant AndroidX artifacts on integration | https://developer.android.com/jetpack/androidx/releases/pdf |
| Tesseract4Android | Android offline OCR adapter candidate; benchmark native model size, throughput and lifecycle | License/maintenance/version/ABI review pending | https://github.com/adaptech-cz/Tesseract4Android |

## B00 decisions
- **Reuse, don't rebuild engines**: project owns orchestration, safety, user interface, QA, adapters. OCR engines remain upstream dependencies or subprocesses with clear provenance.
- Select *model per script/task* on measured corpus, with manual override and confidence; no fictitious universal best engine.
- No model files, third-party library source, secret PDFs or user content are committed during B00.
- Workload tiers: Android local for normal size, Windows local for heavy OCR/reconstruction, optional explicitly authorized LAN delegation, cloud disabled by default.
- No claims about language accuracy or maximal file size without device benchmarks.

## Integration checklist (mandatory per dependency)
Version/commit/tag; SPDX code license; model-card and weights license; transitive redistribution; supply-chain provenance; advisory/CVE scan; maintenance; parser security; memory/CPU/OS benchmarks; test data; notices; rollback path. Record verdict in an ADR before importing.
