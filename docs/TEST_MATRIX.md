# PDF Studio — Test Strategy

| Tier | Inputs | Objective | Status |
|---|---|---|---|
| B00 source | synthetic fake-header PDF marker; temp SQLite/page artifacts | copy-on-write guard, changed-source refusal, tamper detection, Arabic search key | LOCAL PASS 8/8; CI pending |
| OCR Arabic | scanned RTL Arabic (consented/synthetic), variants/diacritics | CER/WER, position, actual selectable PDF text | NOT TESTED |
| OCR English/mixed | scanned typed EN and bidi pages | CER/WER, orientation, ordering, alignment | NOT TESTED |
| Document layout | multicolumn, headers, tables, formula, handwritten examples | reading order and element accuracy | NOT TESTED |
| PDF editing | genuine born-digital and scanned PDF, form, signature, annotations | save/reopen/object extraction, no silent content loss | NOT TESTED |
| Massive | 10/100/1000/5000 pages, >=2 GB where practical | peak RSS/disk, throughput, interruption/resume, page integrity | NOT TESTED |
| Security | encrypted, malformed, signed, active content, redactions | refusal/warning, no leak, safe removal | NOT TESTED |
| Exports | PDF/A, DOCX, Markdown/HTML/JSON, table CSV/XLSX | independent reader/validator and diff | NOT TESTED |
| Android/Windows device | Xiaomi Pad 5, Android phone, MSI Sword 15 | UI/performance/print/share/install | NOT TESTED |

Do not use fixture fake header to claim parsing a valid PDF. B00 tests establish only journal mechanics. Independent test corpus and precise pass thresholds to be set before each integration phase.
