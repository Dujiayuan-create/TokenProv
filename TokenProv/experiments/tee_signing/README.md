# TEE-based isolated receipt signing (Windows)

`benchmark.py` compares ordinary Windows CNG software keys with keys that require
VBS isolation. It uses ECDSA-P256/SHA256, verifies the `Virtual Iso` property and
rejects software fallback. Only unnamed, ephemeral, nonexportable private keys
are created. The script makes no boot, certificate-store or security-setting
changes and needs no compiler or custom enclave DLL.

Windows with working VBS key protection is required to run the benchmark. The
code imports and `--help` remain usable on other platforms. Unsupported hosts
fail before signing. There is no emulated isolation mode.

From the repository root, using the Python environment in which TokenProv is installed:

```powershell
python experiments/tee_signing/benchmark.py `
  --source-run results/server_runs/YOUR_REAL_RUN `
  --output-root results/tee_signing --receipts 1024 --rounds 3 --seed 42
python experiments/tee_signing/report.py results/tee_signing/YOUR_NEW_RUN
```

The source run must include actual successful, non-warmup `TokenProv-Full`
provider receipts marked `REAL_MODEL_SERVING`, their witness references and
evidence bundles. At least `--receipts` eligible calls are required; the default
is 1,024. Bundle hashes, witness commitments, shared fields and prompt/output
charge arithmetic are checked before measurement. The selection is sorted by
request and call identifier, then shuffled reproducibly within each session.
`--warmups` defaults to 20. Each round creates one fresh key per mode, alternates
mode order and records its first signing operation separately.

Each benchmark run creates a fresh timestamped directory under `--output-root`;
source-run directories cannot be used as output roots. The source evidence is
read-only. Outputs contain public keys, saved signed receipt examples, raw
timings, session properties and explicit correctness/scope controls. Treat
receipt contents according to the source workload's data-handling requirements.

`report.py` uses the normal `cryptography` dependency to verify saved CNG public
key blobs and ECDSA signatures on any platform. It recomputes session statistics
from raw rows, validates dynamic round/receipt/control counts and writes JSON,
LaTeX and methodology notes to a fresh `RUN/report/` directory. Use `--output`
to choose another new report directory. Only the saved example signatures can
be independently reverified; raw rows retain the verifier status for all other
operations.

Optional plots use a normal matplotlib installation:

```bash
python -m pip install matplotlib
python experiments/tee_signing/report.py RUN --output NEW_REPORT --plots
```

Means and sample SD are computed across independently keyed session means;
paired increments match rounds. A single round has no estimable sample SD.
Plot limits and output prose derive from the current observations.

This experiment isolates **private-key operations**. Receipt preparation and
accounting remain in the host process. Changed-message
and signature controls test stale-signature rejection. Explicit accepted scope
controls demonstrate that a host can freshly sign modified accounting. They
must not be counted as blocked attacks. Reported local key properties do not
constitute remote attestation, and this component does not measure protected
model execution or full provider assurance.
