# Assurance boundaries

Each component establishes a specific property under its own trust assumptions. A successful protocol audit alone does not establish that a provider executed a particular model, generated necessary calls, or reported truthful usage.

| Component | What is checked | Boundary |
| --- | --- | --- |
| Offline smoke | Record construction, protocol audit, persistence, counts, and fixture witness openings | Uses synthetic responses and a fixture proof worker |
| Ordinary serving | Signed call records, lifecycle/handoff linkage, coverage, and exact arithmetic settlement | A local sidecar signs service-reported output and usage |
| Local ledger/repository | Authenticated ordering, signed membership, hashes, immutable evidence, and local durability | A single host; no distributed consensus or payment finality |
| Groth16 accounting circuit | Scoped Poseidon opening, token-charge arithmetic, and one request-identifier equality | Requires circuit/key provenance and host-side mappings |
| Windows isolated signing | Private-key signing operations using CNG VBS key protection | Accounting and receipt construction remain outside the protected key boundary |
| Confidential runtime | Verified session and receipt bindings under approved CPU/GPU and workload/device policies | Requires a real protected deployment and trusted verifiers/adapters |

## Provider receipts and accounting

The default serving integration receives an ordinary OpenAI-compatible response and signs its contents with a key managed by the research harness. The signature establishes integrity and identity for that locally recorded statement. It is not a vendor-issued receipt, remote attestation, or independent proof that the response's token usage is correct.

The auditor checks all shared fields required by the host protocol relation, including authorization, lifecycle, handoffs, deployment, input/output commitments, usage, and charge. Exact settlement prevents inconsistent arithmetic, omitted accepted calls, duplicate coverage, and nullifier reuse within the implemented state. The broader truth of a provider's statement requires an appropriate trusted execution or proof mechanism.

Stock HTTP interfaces do not expose authenticated per-call KV cache transitions. Unavailable state is explicitly represented; aggregate cache counters do not fill this gap. Model and tokenizer hashes are registration identities until a trusted execution path binds them to actual loading and inference.

## Scoped Groth16 proofs

The circuit in [tools/zk/circuits/tokenprov_accounting.circom](../tools/zk/circuits/tokenprov_accounting.circom) proves a Poseidon commitment opening, integer-scaled prompt/output token-charge arithmetic, and equality of one request identifier across the scoped accounting inputs. It supports prompt and output components; unsupported nonzero accounting components are rejected by the worker.

The full call-field linkage relation is checked by the host auditor. The trusted worker checks how the SHA-256 witness commitment, receipt fields, and circuit-private values correspond. These host checks are outside the circuit. The combined path is not an end-to-end zero-knowledge proof of full `R_link`, model inference, token origin, or GPU execution.

[tools/zk/proof_worker.mjs](../tools/zk/proof_worker.mjs) performs actual snarkjs proving and verification when configured with a compatible compiled circuit, proving key, and verification key. It checks witness openings, job binding, public signals, and verification-key identity. Errors reject the job; they do not silently switch to a simulated proof. A disabled backend records no ZK proving cost.

Follow [tools/zk/README.md](../tools/zk/README.md) for setup. Generated keys and build outputs are excluded from source. A reproducible benchmark setup is suitable for measuring the benchmark, but is not a production trusted ceremony. Security claims require an independently appropriate setup and artifact verification.

## Windows isolated signing

The optional Windows experiment uses CNG key protection with `NCRYPT_REQUIRE_VBS_FLAG` and checks the key's `Virtual Iso` property. It compares isolated signing with an ordinary software-key control and externally verifies signatures. It does not require executing the model on that Windows host.

Only private-key operations are isolated. The host serializes and hashes the receipt and can ask the signer to sign freshly modified accounting. Scope controls intentionally demonstrate this accepted behavior. Changed signed data and invalid signatures are different cases from a fresh signature over an altered statement. Do not combine them into a count of blocked attacks.

This experiment measures neither inference attestation, token-origin verification, an enclave that validates accounting, nor GPU confidential execution. Its commands and required input artifacts are described in [experiments/README.md](../experiments/README.md).

## Full confidential runtime integration

[tokenprov/assurance/tee_runtime.py](../tokenprov/assurance/tee_runtime.py) provides a separate runtime that loads a local model and generates tokens inside the intended protected deployment. [tee.py](../tokenprov/assurance/tee.py) implements the relying-party session and receipt checks; [hardware.py](../tokenprov/assurance/hardware.py) supplies platform-verification integration.

Enrollment requires CPU and GPU attestation, challenge/session-key binding, approved measurements and policies, and a workload/device identity adapter. The adapter must verify protected launch identity, immutable program/model/tokenizer artifacts, the actual execution device, and the required protected CPU/GPU path. Self-reported hashes, an attested GPU count, or a script returning `verified: true` cannot establish these properties.

The integration uses actual CPU quote and NVIDIA attestation verification interfaces. The built-in CPU parser accepts TDX quote v4; other formats require a real external verifier. Missing or rejected evidence, tools, policies, and adapters cause enrollment to fail. The example [runtime configuration](../configs/tee/runtime.yaml) and [relying-party policy](../configs/tee/policy.yaml) contain placeholders that must be replaced with approved deployment values.

### Adapter JSON contracts

Configure external commands as argv lists. The runtime invokes them with `shell=False`, sends one JSON object on stdin, and requires a JSON object on stdout with exit status zero. Send diagnostics to stderr. Timeouts, malformed JSON, and stdout longer than 8,388,608 characters are rejected.

| Configuration key | JSON stdin fields | JSON stdout |
| --- | --- | --- |
| Runtime `workload_evidence_command` | `schema_version: 1`, `session_binding`, `manifest`, `model_path` | Platform-authenticated launch/device evidence object, saved as `session.workload_evidence` and bound into CPU report data |
| Optional runtime `cpu_attester_command` | `schema_version: 1`, `report_data` | Raw CPU evidence object; the built-in TDX verifier expects a base64 `quote` field |
| Optional policy `cpu_verifier_command` | `schema_version: 1`, `evidence`, `expected_report_data` | `verified: true`, approved `platform` and `measurement`, matching `report_data`, `debug: false`, `tcb_status: "UpToDate"` |
| Policy `workload_verifier_command` | `schema_version: 1`, `binding`, `expected`, `session`, `verified_cpu`, `verified_gpu`, `policy_file` | Identical `binding` and `expected`, plus all required verification flags set to `true` |

`session_binding` supplies the challenge/session identity fields and collected GPU token. CPU `report_data` is a 64-byte value encoded as hexadecimal. For the built-in TDX verifier, the allowlisted `measurement` is SHA-256 of the first 520 bytes of the authenticated TD report body; it is not the MRTD field alone.

The workload verifier's `expected` object contains `session_digest`, `runtime_public_key`, `program_hash`, `deployment_digest`, and `policy_sha256`. `binding` is its typed digest under `tee-workload-verification/v1`. Required result flags are `verified`, `workload_identity_verified`, `immutable_artifacts_verified`, `gpu_device_binding_verified`, and `secure_cpu_gpu_path_verified`. The verifier must derive these decisions from authenticated platform evidence and its approved local policy.

The source release does not provision a confidential VM, measured image, protected launcher, GPU confidential-computing mode, workload adapter, or deployment trust roots. Unit-test substitutes test rejection and binding logic; they establish no hardware result. Installing Python dependencies or setting `tee_url` does not complete this deployment.

The runtime's inference profile and serialization can differ from ordinary HTTP batching. Use matched model engines and scheduling profiles for performance comparisons. Receipt previous/next fields track a signed receipt chain, not proven KV cache state. Current relying-party chain state does not provide persistent global rollback prevention or cross-process payment deduplication; those properties require external state/checkpoint integration. Runtime timestamps do not independently prove physical time.

An approved protected deployment supports reliance on its software and hardware TCB. It does not constitute a mathematical proof of inference or establish the business necessity of a call.
