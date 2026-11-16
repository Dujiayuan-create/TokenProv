#!/usr/bin/env python3
"""CPU protocol scalability and storage experiments (C1/C2/C3/E1/E3)."""
from __future__ import annotations

import csv
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from tokenprov.audit import audit
from tokenprov.broker import NativeTokenProvBroker
from tokenprov.common.merkle import MerkleLeaf, MerkleTree, verify_inclusion
from tokenprov.common.models import ServiceConfiguration
from tokenprov.common.serialization import canonical_bytes
from tokenprov.harness import EvaluationHarness
from tokenprov.workloads import build_honest_protocol


ROOT = None  # Set to a fresh output directory by experiments/paper_protocol.py
SEEDS = (42, 43, 44, 45, 46)
CALLS = (1, 2, 4, 8, 16, 32, 64)


def broker():
    config = ServiceConfiguration("P1", "deterministic-protocol-provider", None,
        {}, {}, {}, "local-research-v1")
    def forward(call):
        tokens = len(canonical_bytes(call.transformed_messages)) // 4
        return {"model": "deterministic-protocol-provider",
            "choices": [{"message": {"content": f"deterministic-{call.call_id}"}}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 4}}
    return NativeTokenProvBroker([config], {"P1": forward})


def write_csv(name, rows):
    with (ROOT / "results/processed" / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def observation(experiment, system, seed, request_id, parameters, metrics):
    return {"experiment_id": experiment, "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": system, "variant": system, "run": SEEDS.index(seed)+1, "seed": seed,
        "request_id": request_id, "call_id": None,
        "measurement_type": "PROTOCOL_MICROBENCHMARK", "parameters": parameters,
        "metrics": metrics, "hardware": {"execution": "CPU"},
        "software": {"cryptography": "Ed25519/SHA-256", "storage": "local filesystem fsync"},
        "model": {"name": None, "note": "deterministic provider; not LLM performance"},
        "backend": {"provider": "deterministic", "ledger": "LocalAuthenticatedLedger",
                    "repository": "LocalEvidenceRepository"},
        "success": True, "error": None}


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw, c1, e1, e3 = [], [], [], []
    messages = ({"role": "user", "content": "protocol scalability"},)
    for calls in CALLS:
        for system in ("Vanilla", "SignedLog", "TokenProv-Full"):
            for seed in SEEDS:
                request_id = f"c1-{calls}-{system}-{seed}"
                harness = EvaluationHarness(broker(), ROOT / "results/runtime" /
                    f"protocol_scalability_{stamp}" / request_id,
                    model_reference="deterministic", model_revision="v1",
                    model_hash="deterministic-model-hash", tokenizer_hash="deterministic-tokenizer-hash")
                result = harness.execute(request_id, messages, variant=system,
                    calls_per_request=calls, lifecycle_events=max(1+2*calls, 4*calls))
                t = result.timings_ns
                protocol_ns = max(0, t["T_online"] - t["T_llm"])
                metrics = {"T_online_ns": t["T_online"], "T_protocol_ns": protocol_ns,
                    "T_settlement_ns": t.get("T_settlement", 0), "T_audit_ns": t["T_audit"],
                    "T_lifecycle_ns": t["T_lifecycle"],
                    "T_handoff_ns": t["T_HB_handoff"] + t["T_BP_handoff"],
                    "T_receipt_ns": t["T_receipt"], "T_merkle_ns": t["T_merkle"],
                    "ledger_bytes": result.ledger_bytes, "repository_bytes": result.repository_bytes,
                    "total_evidence_bytes": result.ledger_bytes + result.repository_bytes,
                    "actual_calls": result.actual_calls, "lifecycle_events": result.lifecycle_events}
                raw.append(observation("C1", system, seed, request_id,
                    {"calls_per_request": calls, "reduction":
                     "one storage-backed observation per each of five independent seeds; reduced from 1000 because 64-call fsync evidence runs are expensive"}, metrics))
                c1.append({"system": system, "calls_per_request": calls,
                    "actual_calls": result.actual_calls, "lifecycle_events": result.lifecycle_events,
                    "mean_online_ms": t["T_online"]/1e6, "mean_protocol_ms": protocol_ns/1e6,
                    "mean_settlement_ms": t.get("T_settlement", 0)/1e6,
                    "mean_audit_ms": t["T_audit"]/1e6,
                    "mean_lifecycle_ms": t["T_lifecycle"]/1e6,
                    "mean_handoff_ms": (t["T_HB_handoff"]+t["T_BP_handoff"])/1e6,
                    "mean_receipt_ms": t["T_receipt"]/1e6, "mean_merkle_ms": t["T_merkle"]/1e6,
                    "ledger_bytes_per_request": result.ledger_bytes,
                    "repository_bytes_per_request": result.repository_bytes,
                    "total_evidence_bytes_per_request": result.ledger_bytes+result.repository_bytes,
                    "run": SEEDS.index(seed)+1, "seed": seed,
                    "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                if system in {"SignedLog", "TokenProv-Full"}:
                    fulltrace_bytes = result.repository_bytes
                    e1.append({"system": system, "calls_per_request": calls, "run": SEEDS.index(seed)+1,
                        "seed": seed, "bytes_per_call": (result.repository_bytes+result.ledger_bytes)/calls,
                        "bytes_per_request": result.repository_bytes+result.ledger_bytes,
                        "ledger_bytes": result.ledger_bytes, "repository_bytes": result.repository_bytes,
                        "total_bytes": result.repository_bytes+result.ledger_bytes,
                        "write_latency_ms": t["T_evidence_write"]/1e6,
                        "serialization_latency_ms": None,
                        "storage_reduction_vs_fulltrace_pct": None,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"})
            if system == "TokenProv-Full":
                # FullTrace uses the same authenticated execution plus accessible raw messages/responses.
                for seed in SEEDS:
                    request_id = f"e1-{calls}-FullTrace-{seed}"
                    harness = EvaluationHarness(broker(), ROOT / "results/runtime" /
                        f"protocol_scalability_{stamp}" / request_id,
                        model_reference="deterministic", model_revision="v1",
                        model_hash="deterministic-model-hash", tokenizer_hash="deterministic-tokenizer-hash")
                    result = harness.execute(request_id, messages, variant="FullTrace",
                        calls_per_request=calls, lifecycle_events=max(1+2*calls, 4*calls))
                    t = result.timings_ns
                    e1.append({"system": "FullTrace", "calls_per_request": calls,
                        "run": SEEDS.index(seed)+1, "seed": seed,
                        "bytes_per_call": (result.repository_bytes+result.ledger_bytes)/calls,
                        "bytes_per_request": result.repository_bytes+result.ledger_bytes,
                        "ledger_bytes": result.ledger_bytes, "repository_bytes": result.repository_bytes,
                        "total_bytes": result.repository_bytes+result.ledger_bytes,
                        "write_latency_ms": t["T_evidence_write"]/1e6,
                        "serialization_latency_ms": None, "storage_reduction_vs_fulltrace_pct": 0,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                    raw.append(observation("E1", "FullTrace", seed, request_id,
                        {"calls_per_request": calls}, {"repository_bytes": result.repository_bytes,
                        "ledger_bytes": result.ledger_bytes, "T_evidence_write_ns": t["T_evidence_write"]}))
    full = {(row["calls_per_request"], row["run"]): row["total_bytes"] for row in e1
            if row["system"] == "FullTrace"}
    for row in e1:
        baseline = full[(row["calls_per_request"], row["run"])]
        row["storage_reduction_vs_fulltrace_pct"] = (baseline-row["total_bytes"])/baseline*100
    write_csv("C1_calls_scalability.csv", c1)
    write_csv("E1_storage_vs_calls.csv", e1)

    raw_path = ROOT / 'results/raw/storage_scaling.jsonl'
    raw_path.write_text(''.join(json.dumps(row)+'\n' for row in raw),encoding='utf-8')
    print(json.dumps({'storage_rows':len(e1),'protocol_rows':len(c1)}))
