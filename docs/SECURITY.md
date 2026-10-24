# Security and Privacy Policy

- Original user PDF is immutable unless user explicitly chooses destructive overwrite and successful verification. Default: save-as a different path, with crash-safe temp/rename and rollback planned.
- No automatic network use, analytics, document telemetry or model upload. User-opted cloud and LAN processing require explicit data-flow approval and independently scoped permissions.
- Public repository includes synthetic tests only. Do not commit patient files, private PDFs, tokens, keys, real document captures, fonts with uncertain rights or model weights.
- Threat model: malicious PDF streams, bombs / decompression exhaustion, embedded scripts, unsafe external links, path traversal, malicious fonts, unbounded image render dimensions, OCR model downloads, remote peers.
- Planned mitigations: isolated parser processes, CPU/RAM/temp quotas, deny network except explicit opt-in, temp permissions, secret and log redaction, checksums and content-source pinning.
- Redaction is real content removal with independent re-extraction verification; a black rectangle never qualifies.
- Digitally signed PDFs: modifications may invalidate signatures; require warning, copy, and independent verification.
- PDF/A conformance must be independently validated. Merely naming output .pdfa is insufficient.
- Encryption and securely wiping temporary data have filesystem-specific limitations; document those before claiming secure erase.
- Dependabot/advisory/license checks, pinned dependencies, SBOM and signing are future gates; none are falsely reported as already working in B00.
