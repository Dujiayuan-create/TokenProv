# Component and experiment coverage

The source release supports three separate activities: validating protocol code,
running fresh experiments, and regenerating paper artifacts from retained data.
CPU unit tests and figure reproduction do not imply that a full protected model
service has been deployed. This matrix covers the selected paper results, not
every abandoned exploratory script in the original working directory.

## Core components

| Component | Implementation | Validation / deployment |
| --- | --- | --- |
| Authorization | [authorization.py](../tokenprov/user/authorization.py) | Protocol and mutation tests |
| Lifecycle DAG | [dag.py](../tokenprov/lifecycle/dag.py) | Lifecycle, closure and audit tests |
| Bilateral handoffs | [bilateral.py](../tokenprov/handoff/bilateral.py) | Boundary binding and tampering tests |
| Provider receipts and shared-field linkage | [receipts.py](../tokenprov/provider/receipts.py), [auditor.py](../tokenprov/audit/auditor.py) | Honest and corrupted transcripts; ordinary HTTP sidecar signatures |
| Merkle evidence and storage | [merkle.py](../tokenprov/common/merkle.py), [repository.py](../tokenprov/evidence/repository.py) | Membership, persistence and integrity tests |
| Exact settlement and local ledger | [core.py](../tokenprov/settlement/core.py), [local.py](../tokenprov/ledger/local.py) | Coverage, arithmetic, nullifier and durability checks; no Fabric consensus |
| Deferred scoped proofs | [deferred.py](../tokenprov/assurance/deferred.py), [proof_worker.mjs](../tools/zk/proof_worker.mjs) | Queue/binding/rejection tests; actual proofs require external compiled assets |
| TEE-based signing component | [benchmark.py](../experiments/tee_signing/benchmark.py) | Actual Windows protected-key operations; public examples retained |
| Full confidential runtime | [tee_runtime.py](../tokenprov/assurance/tee_runtime.py), [hardware.py](../tokenprov/assurance/hardware.py) | Session/binding/rejection tests; actual CPU/GPU deployment and trusted adapters still required |

## Fresh experimental methods

| Paper section | Executable entry | Required environment |
| --- | --- | --- |
| B1/B2/B4 live-serving comparison | `python -m tokenprov.server_eval run --config configs/server-eval.yaml` | Existing model endpoint and matching tokenizer; configure each model |
| B3 protocol call/event scaling | `python -m tokenprov.server_eval protocol --config configs/smoke.yaml --calls 1,8,32,128,512 --event-factors 4,8 --repetitions 1` | CPU only; the paper's original scan has one observation per configuration |
| B3 serving concurrency | Main serving entry with the YAML concurrency list changed to `[1, 8, 32]` | Real model service; match request counts/output lengths when comparing |
| C1--C3 | [run_c_assurance_server.py](../experiments/run_c_assurance_server.py), [report_c_assurance.py](../experiments/report_c_assurance.py) | Retained real witnesses, Node, Circom/snarkjs and matching proving assets; no active LLM needed during replay |
| C signing | [Signing benchmark/report](../experiments/tee_signing/README.md) | Supported Windows host for fresh isolated signing; report verification is portable |
| D current bytes and finalization times | Main serving entry and [metrics.py](../tokenprov/server_eval/metrics.py) | Real-serving cohort; request outputs retain stage timings and bytes |
| D historical FullTrace storage comparison | `python experiments/paper_protocol.py --experiment storage --output results/storage-new` | CPU/local storage; five seeds and seven call counts by default |
| E current valid-signature adaptive attacks | `python -m tokenprov.server_eval security --config configs/smoke.yaml --trials 100 --queries 16 --calls 8 --seeds 42` | CPU only; the retained current suite uses one 100-episode cohort per scheme |
| E historical attacks, splicing, localization and collusion | `python experiments/paper_protocol.py --experiment security --output results/security-new` | CPU only; five fixtures × 100 repetitions by default |
| F rule-gating ablation | Same historical security command; emits `G1_security_ablation.csv` | CPU only; gating over the first failed relation |
| F cumulative validation cost | `python experiments/paper_protocol.py --experiment ablation --output results/ablation-new` | CPU; install `.[benchmarks]`; five seeds × 1,000 samples/variant |
| Qualitative provenance cases | Main serving entry with a custom `trace_file` and token-exporting model service | Matching tokenizer and token IDs from the service; excluded from formal performance cohort |

Historical storage/security/ablation driver bodies are in
[protocol_drivers/](../experiments/protocol_drivers/). A single portable CLI sets
their fresh output root and measurement settings. These retained methods use
the current core implementation; serialization changes, filesystem behavior and
hardware can change fresh numeric results. Their historical IDs are retained to
make data provenance inspectable. The storage suite also exports its original
call-count timing rows. The security suite includes evidence-boundary controls
that intentionally accept and a source-local-check surrogate; do not count those
as adversarial protocol rejections.

The [paper guide](../paper/README.md) maps each result to final CSV/JSONL inputs,
plots, tables and offline regeneration commands. It preserves the final scientific
scope and all retained rounds. It does not distribute model weights, private keys,
production trusted-setup material, a Fabric deployment, or a hardware TEE image.
