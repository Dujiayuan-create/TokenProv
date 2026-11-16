# Architecture

TokenProv tracks a request from authorization to settlement across a user, an orchestrator, a broker, and one or more providers. The latency-critical call path is `U -> H -> B -> P`; evidence persistence, ledger updates, settlement, and deferred proof work have separate timing boundaries.

## Records and audit

Records use typed, canonical JSON serialization, domain-separated SHA-256 commitments, and Ed25519 signatures. Token charges use integer arithmetic under a versioned pricing policy.

1. **Authorization (`R_auth`).** A signed authorization fixes the request's permissions and accounting context.
2. **Lifecycle (`R_life`).** Each execution attempt has a distinct call identifier and exactly one terminal lifecycle event in an authenticated DAG. Closure identifies all terminal attempts and the subset accepted by providers. A failure before acceptance does not require a provider receipt.
3. **Handoffs (`R_hand`).** Both orchestrator-to-broker and broker-to-provider handoffs are signed and countersigned. The second handoff references the first, preserving their causal relationship.
4. **Provider evidence (`R_prov`).** Signed receipts and call evidence repeat the shared fields checked by `R_link`, including request, call, authorization, deployment, configuration, handoffs, input/output commitments, usage, pricing, and charge.
5. **Settlement (`R_set`).** The auditor checks signed closure, exact accepted-call/receipt coverage, typed Merkle evidence, recomputed charges, and unique call/settlement nullifiers.

The main auditor is in [tokenprov/audit/auditor.py](../tokenprov/audit/auditor.py). The `SignedLogPlus` comparison uses its own auditor in [tokenprov/audit/signedlog.py](../tokenprov/audit/signedlog.py). Controlled security experiments submit corrupted records to these implementations and compare their decisions with independently retained experiment truth.

## Serving integration

[tokenprov/server_eval/](../tokenprov/server_eval/) connects the protocol harness to OpenAI-compatible HTTP services. All compared systems use the configured service and matched workload inputs. Requests run concurrently; calls within each request run sequentially. System order is shuffled within each repetition.

The ordinary provider adapter signs the response and usage observed by the local harness. This is a research sidecar identity, not a signature or attestation issued by the model vendor. Registering a model-file hash records a deployment identity; an ordinary HTTP response cannot establish that those files produced the response.

Synthetic inputs are calibrated with the configured local tokenizer and chat template. Actual response usage must match the requested prompt-token target. Stock OpenAI-compatible services do not provide authenticated per-call KV cache state, so that state is recorded as unavailable. Reusing a response must be represented as a lifecycle event rather than counted as a fresh provider execution.

## Evidence, ledger, and deferred work

Each cell/system/repetition initializes deployment identities, registrations, an evidence repository, and a ledger. Individual requests retain separate protocol state. Setup costs are recorded separately from the request's online clock.

The compact path stores one immutable, content-addressed evidence bundle per request. The bundle contains the objects needed for independent protocol review, including provider witnesses and handoff openings. Witness observations reference this bundle rather than writing duplicate witness payloads.

`LocalAuthenticatedLedger` provides signed membership, a monotonic sequence, a hash chain, and `fsync` durability on one host. Authorization, closure, and settlement remain three authenticated records even when persisted together in a durable batch. This implementation supplies neither Fabric consensus nor an external payment network.

Deferred work fixes each provider witness commitment before proving. Sampling occurs per request; for selected `TokenProv-Full` requests, each accepted call creates one proof job when a backend is enabled. A bounded queue applies backpressure after the online clock has stopped. All witnesses remain available, including those from unsampled requests; retention metadata does not automatically delete them.

The host auditor's full cross-record linkage checks and the optional circuit's narrower constraints are separate verification layers. Their trust and execution boundaries are detailed in [ASSURANCE.md](ASSURANCE.md).
