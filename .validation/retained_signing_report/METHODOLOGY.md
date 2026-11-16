# VBS-isolated receipt signing

3 independently keyed sessions per mode; 1024 source receipts and 20 warmups per session. First operations are recorded separately. The report recomputes session means and sample SD across those means. One round has no estimable sample SD.

ECDSA-P256/SHA256 signatures use the same Windows CNG API in both modes. The protected mode requires VBS key isolation and rejects software fallback; keys are ephemeral and nonexportable. Saved public examples are independently verified with cryptography on any platform. The raw log records verification status for all measured operations; only the saved examples can be reverified from this report.

Changed-message checks reject stale signatures. Accepted scope controls demonstrate that host-modified accounting can be freshly signed. Accounting, token origin and receipt preparation remain outside the isolated key operation. Reported local key properties are not remote attestation; this experiment does not measure protected inference.
