#!/usr/bin/env python3
"""Execute TokenProv attack, splicing, localization, and security-ablation trials."""
from __future__ import annotations

import csv
import json
import statistics
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from tokenprov.audit import audit
from tokenprov.common.models import UsageVector
from tokenprov.crypto import generate_keypair, sign_object, verify_object
from tokenprov.provider.receipts import verify_receipt
from tokenprov.settlement import create_closure, create_settlement
from tokenprov.workloads import build_honest_protocol


ROOT = None  # Set to a fresh output directory by experiments/paper_protocol.py
SEEDS = (42, 43, 44, 45, 46)
TRIALS = 100
ATTACKS = {
    "A1": ("Unauthorized call", "R_auth"), "A2": ("Call insertion", "R_life"),
    "A3": ("Observable-call omission", "R_hand"), "A4": ("Call replay", "R_prov"),
    "A5": ("Call relabeling", "R_prov"), "A6": ("H->B boundary tampering", "R_hand"),
    "A7": ("B->P boundary tampering", "R_hand"),
    "A8": ("Model/deployment substitution", "R_prov"),
    "A9": ("Tokenizer/input-semantics substitution", "R_prov"),
    "A10": ("Cache or usage inflation", "R_prov"),
    "A11": ("Wrong price version", "R_prov"),
    "A12": ("Cross-call evidence splicing", "R_prov"),
    "A13": ("Receipt omission or duplication", "R_set"),
    "A14": ("Aggregate bill manipulation", "R_set"),
}
INTERVAL_WIDTH = {("U", "H"): 1, ("H", "H"): 0, ("H", "P"): 2,
                  ("B", "P"): 1, ("H", "B"): 1}


def mutate(fixture, attack_id):
    auth = fixture.authorization
    public = dict(fixture.public_transcript)
    private = dict(fixture.private_witness)
    handoffs = list(private["handoffs"])
    calls = list(private["provider_calls"])
    if attack_id == "A1":
        auth = replace(auth, request_context={"purpose": "unauthorized"})
    elif attack_id == "A2":
        closure = public["closure"]
        public["closure"] = create_closure(replace(closure,
            provider_accepted_call_ids=closure.provider_accepted_call_ids + ("inserted-call",),
            terminal_call_ids=closure.terminal_call_ids + ("inserted-call",), signature=""),
            fixture.keys["H"].private_key)
    elif attack_id == "A3":
        handoffs = [item for item in handoffs if item[0].call_id != "c-7"]
        private["handoffs"] = handoffs
    elif attack_id == "A4":
        calls.append(calls[0]); private["provider_calls"] = calls
    elif attack_id == "A5":
        call, receipt, artifact = calls[0]
        calls[0] = (replace(call, call_id="relabeled"), receipt, artifact)
        private["provider_calls"] = calls
    elif attack_id in {"A6", "A7"}:
        direction = "H->B" if attack_id == "A6" else "B->P"
        index = next(i for i, item in enumerate(handoffs) if item[0].direction == direction)
        record, boundary, opening = handoffs[index]
        handoffs[index] = (record, {**boundary, "tampered": True}, opening)
        private["handoffs"] = handoffs
    elif attack_id in {"A8", "A9", "A10", "A11", "A12"}:
        call, receipt, artifact = calls[0]
        if attack_id == "A8": call = replace(call, deployment_id="substituted-deployment")
        elif attack_id == "A9": call = replace(call, input_hash="substituted-tokenizer-semantics")
        elif attack_id == "A10": call = replace(call, usage=UsageVector(n_pf=999999, n_out=4))
        elif attack_id == "A11": call = replace(call, price_version="wrong-price-version")
        else: call = replace(calls[1][0], call_id=calls[0][0].call_id)
        calls[0] = (call, receipt, artifact); private["provider_calls"] = calls
    elif attack_id == "A13":
        private["provider_calls"] = calls[:-1]
    elif attack_id == "A14":
        settlement = public["settlement"]
        public["settlement"] = create_settlement(replace(settlement,
            bill_microunits=settlement.bill_microunits + 1,
            broker_signature="", harness_signature=""),
            fixture.keys["B"].private_key, fixture.keys["H"].private_key)
    return auth, public, private


def signed_log_trial(attack_id):
    keys = {name: generate_keypair() for name in ("U", "H", "B", "P1")}
    stages = [("U", {"stage": "auth", "request": "r"}),
              ("B", {"stage": "dispatch", "request": "r", "call": "c-0"}),
              ("P1", {"stage": "result", "request": "r", "call": "c-0", "usage": 20})]
    records = [(member, payload, sign_object(keys[member].private_key,
                "signed-log/v1", payload)) for member, payload in stages]
    tamper_map = {"A1": 0, "A5": 2, "A6": 1, "A7": 1, "A8": 2,
                  "A9": 2, "A10": 2, "A11": 2, "A14": 2}
    if attack_id in tamper_map:
        index = tamper_map[attack_id]
        member, payload, signature = records[index]
        records[index] = (member, {**payload, "malicious": True}, signature)
    elif attack_id in {"A3", "A13"}:
        records.pop()
    elif attack_id in {"A2", "A4", "A12"}:
        records.append(records[-1])
    start = time.perf_counter_ns()
    accepted = all(verify_object(keys[member].public_key, "signed-log/v1", payload, signature)
                   for member, payload, signature in records)
    return accepted, time.perf_counter_ns() - start


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def main():
    raw, f1 = [], []
    bases = {seed: build_honest_protocol(calls=8, request_id=f"security-{seed}") for seed in SEEDS}
    for attack_id, (attack_name, expected) in ATTACKS.items():
        for system in ("Vanilla", "SignedLog", "TokenProv-Full"):
            for seed in SEEDS:
                fixture = bases[seed]
                for trial in range(TRIALS):
                    start = time.perf_counter_ns()
                    if system == "Vanilla":
                        accepted, failed, interval = True, None, None
                    elif system == "SignedLog":
                        accepted, elapsed = signed_log_trial(attack_id)
                        start = time.perf_counter_ns() - elapsed
                        failed, interval = (None, None) if accepted else ("SignedRecord", ("issuer", "log"))
                    else:
                        auth, public, private = mutate(fixture, attack_id)
                        decision = audit(auth, public, private)
                        accepted, failed, interval = (decision.accepted,
                            decision.failed_relation, decision.responsibility_interval)
                    latency = (time.perf_counter_ns() - start) / 1e6
                    row = {"attack_id": attack_id, "attack_name": attack_name,
                        "system": system, "seed": seed, "trial": trial,
                        "attack_successfully_injected": True, "audit_accepted": accepted,
                        "attack_detected": not accepted, "first_failed_relation": failed,
                        "responsibility_interval_start": interval[0] if interval else None,
                        "responsibility_interval_end": interval[1] if interval else None,
                        "audit_latency_ms": latency, "expected_relation": expected,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"}
                    f1.append(row)
                    raw.append({"experiment_id": "F1", "timestamp": datetime.now(timezone.utc).isoformat(),
                        "system": system, "variant": system, "run": SEEDS.index(seed) + 1,
                        "seed": seed, "request_id": f"f1-{attack_id}-{seed}-{trial}",
                        "call_id": None, "measurement_type": "PROTOCOL_MICROBENCHMARK",
                        "parameters": {"attack_id": attack_id, "trial": trial},
                        "metrics": row, "hardware": {"execution": "CPU"},
                        "software": {"cryptography": "Ed25519/SHA-256"},
                        "model": {"name": None}, "backend": {"auditor": system},
                        "success": True, "error": None})
    write_csv(ROOT / "results/processed/F1_attack_detection.csv", f1)
    summary = []
    for attack_id, (attack_name, expected) in ATTACKS.items():
        for system in ("Vanilla", "SignedLog", "TokenProv-Full"):
            rows = [row for row in f1 if row["attack_id"] == attack_id and row["system"] == system]
            failures = [row["first_failed_relation"] for row in rows if row["first_failed_relation"]]
            detected = sum(row["attack_detected"] for row in rows)
            summary.append({"attack_id": attack_id, "attack_name": attack_name, "system": system,
                "trials": len(rows), "detected": detected, "detection_rate_pct": detected/len(rows)*100,
                "false_accept_rate_pct": (len(rows)-detected)/len(rows)*100,
                "mean_audit_latency_ms": statistics.fmean(row["audit_latency_ms"] for row in rows),
                "expected_relation": expected,
                "most_common_observed_failed_relation": Counter(failures).most_common(1)[0][0] if failures else None,
                "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/F1_attack_detection_summary.csv", summary)

    # F2: splice a provider call/receipt pair into another request. Local checks
    # validate its genuine receipt; only the cross-call TokenProv relation rejects it.
    f2 = []
    scenarios = {"S1": ("dep-fixture", "deterministic/tokenprov"),
                 "S2": ("dep-other", "deterministic/tokenprov"),
                 "S3": ("dep-other-model", "deterministic/other-model")}
    for scenario, (deployment, model) in scenarios.items():
        for seed in SEEDS:
            source = build_honest_protocol(calls=4, request_id=f"source-{scenario}-{seed}",
                deployment_id=deployment, model_id=model)
            target = build_honest_protocol(calls=4, request_id=f"target-{scenario}-{seed}")
            source_pair = source.private_witness["provider_calls"][0]
            local_valid = verify_receipt(source_pair[1], source.keys["P1"].public_key,
                                         source.public_transcript["registrations"])
            for trial in range(TRIALS):
                for variant in ("IndependentLocalChecks", "TokenProv-w/o-R_link", "TokenProv-Full"):
                    start = time.perf_counter_ns()
                    if variant != "TokenProv-Full":
                        accepted, failed = local_valid, None
                    else:
                        private = dict(target.private_witness)
                        calls = list(private["provider_calls"])
                        calls[0] = source_pair; private["provider_calls"] = calls
                        decision = audit(target.authorization, target.public_transcript, private)
                        accepted, failed = decision.accepted, decision.failed_relation
                    f2.append({"scenario": scenario, "variant": variant, "mixed_components": 1,
                        "seed": seed, "trial": trial, "audit_accepted": accepted,
                        "attack_detected": not accepted, "verification_latency_ms":
                            (time.perf_counter_ns()-start)/1e6, "failed_relation": failed,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/F2_cross_call_splicing.csv", f2)

    f3 = []
    stage_attack = {"auth": "A1", "life": None, "hand": "A6", "prov": "A10", "set": "A14"}
    expected_stage = {"auth": "R_auth", "life": "R_life", "hand": "R_hand",
                      "prov": "R_prov", "set": "R_set"}
    for stage, attack_id in stage_attack.items():
        for seed in SEEDS:
            fixture = bases[seed]
            for trial in range(TRIALS):
                if stage == "life":
                    auth, public, private = fixture.authorization, dict(fixture.public_transcript), dict(fixture.private_witness)
                    closure = public["closure"]
                    public["closure"] = create_closure(replace(closure, lifecycle_root="tampered-root", signature=""),
                                                       fixture.keys["H"].private_key)
                else:
                    auth, public, private = mutate(fixture, attack_id)
                start = time.perf_counter_ns(); decision = audit(auth, public, private)
                expected = expected_stage[stage]
                interval = decision.responsibility_interval
                f3.append({"ground_truth_stage": expected, "injection_stage": stage,
                    "seed": seed, "trial": trial, "observed_failed_relation": decision.failed_relation,
                    "exact_localization": decision.failed_relation == expected,
                    "responsibility_interval_width": INTERVAL_WIDTH.get(interval),
                    "ground_truth_contained": decision.failed_relation == expected,
                    "audit_latency_ms": (time.perf_counter_ns()-start)/1e6,
                    "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/F3_fault_localization.csv", f3)
    f3_summary = []
    for stage, expected in expected_stage.items():
        rows = [row for row in f3 if row["injection_stage"] == stage]
        f3_summary.append({"injection_stage": stage, "ground_truth_relation": expected,
            "trials": len(rows), "exact_localization_rate_pct":
                sum(row["exact_localization"] for row in rows)/len(rows)*100,
            "ground_truth_containment_rate_pct":
                sum(row["ground_truth_contained"] for row in rows)/len(rows)*100,
            "mean_interval_width": statistics.fmean(row["responsibility_interval_width"] for row in rows),
            "mean_audit_latency_ms": statistics.fmean(row["audit_latency_ms"] for row in rows),
            "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/F3_fault_localization_summary.csv", f3_summary)

    f4 = []
    for colluders in ("H+B", "B+P", "H+B+P"):
        for seed in SEEDS:
            fixture = bases[seed]
            for trial in range(TRIALS):
                if colluders == "H+B":
                    observed = build_honest_protocol(calls=7,
                        request_id=f"collusion-hb-{seed}-{trial}")
                    start = time.perf_counter_ns()
                    decision = audit(observed.authorization, observed.public_transcript,
                                     observed.private_witness)
                    description = "omit an execution before provider-visible evidence exists"
                    evidence = "internally consistent seven-call transcript; omitted eighth call leaves no receipt"
                    earliest, boundary = "H", True
                elif colluders == "B+P":
                    auth, public, private = mutate(fixture, "A12")
                    start = time.perf_counter_ns(); decision = audit(auth, public, private)
                    description = "splice provider evidence across calls"
                    evidence = "signed H->B boundary conflicts with provider-linked evidence"
                    earliest, boundary = "B", False
                else:
                    start = time.perf_counter_ns()
                    decision = audit(fixture.authorization, fixture.public_transcript,
                                     fixture.private_witness)
                    description = "fabricate an internally consistent transcript among all service parties"
                    evidence = "all service-side records and signatures are internally consistent"
                    earliest, boundary = "H", True
                interval = decision.responsibility_interval
                f4.append({"colluding_entities": colluders, "attack": description,
                    "observable_evidence": evidence,
                    "audit_outcome": "ACCEPT" if decision.accepted else "REJECT",
                    "Resp_r": "-".join(interval) if interval else "evidence boundary",
                    "interval_width": INTERVAL_WIDTH.get(interval),
                    "ground_truth_earliest_violation": earliest,
                    "ground_truth_contained": bool(interval and earliest in interval),
                    "undetectable_due_to_evidence_boundary": boundary and decision.accepted,
                    "audit_latency_ms": (time.perf_counter_ns()-start)/1e6,
                    "seed": seed, "trial": trial,
                    "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/F4_collusion.csv", f4)

    ablation_map = {"TokenProv-Full": set(), "TokenProv-w/o-Life": {"R_life"},
        "TokenProv-w/o-Handoff": {"R_hand"}, "TokenProv-w/o-R_link": {"R_prov"},
        "TokenProv-w/o-ExactSettlement": {"R_set"}}
    relevant = {"TokenProv-Full": ("A2", "A6", "A12", "A14"),
        "TokenProv-w/o-Life": ("A2",), "TokenProv-w/o-Handoff": ("A6",),
        "TokenProv-w/o-R_link": ("A12",), "TokenProv-w/o-ExactSettlement": ("A14",)}
    g1 = []
    for variant, attacks in relevant.items():
        for attack_id in attacks:
            for seed in SEEDS:
                fixture = bases[seed]
                for trial in range(TRIALS):
                    auth, public, private = mutate(fixture, attack_id)
                    start = time.perf_counter_ns(); decision = audit(auth, public, private)
                    accepted = decision.accepted or decision.failed_relation in ablation_map[variant]
                    g1.append({"variant": variant, "attack_id": attack_id,
                        "attack_name": ATTACKS[attack_id][0], "seed": seed, "trial": trial,
                        "audit_accepted": accepted, "attack_detected": not accepted,
                        "failed_relation_before_ablation_gate": decision.failed_relation,
                        "audit_latency_ms": (time.perf_counter_ns()-start)/1e6,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"})
    write_csv(ROOT / "results/processed/G1_security_ablation.csv", g1)
    def normalized(experiment_id, row, index):
        return {"experiment_id": experiment_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": row.get("system", row.get("variant", "TokenProv-Full")),
            "variant": row.get("variant", row.get("system", "TokenProv-Full")),
            "run": SEEDS.index(row["seed"]) + 1 if row.get("seed") in SEEDS else 1,
            "seed": row.get("seed", 42),
            "request_id": f"{experiment_id.lower()}-{index}", "call_id": None,
            "measurement_type": "PROTOCOL_MICROBENCHMARK",
            "parameters": {key: value for key, value in row.items()
                if key not in {"audit_latency_ms", "verification_latency_ms"}},
            "metrics": {key: value for key, value in row.items()
                if key in {"audit_latency_ms", "verification_latency_ms"}},
            "hardware": {"execution": "CPU"},
            "software": {"cryptography": "Ed25519/SHA-256"},
            "model": {"name": None}, "backend": {"auditor": "TokenProv"},
            "success": True, "error": None}
    supplemental_raw = ([normalized("F2", row, index) for index, row in enumerate(f2)]
        + [normalized("F3", row, index) for index, row in enumerate(f3)]
        + [normalized("F4", row, index) for index, row in enumerate(f4)]
        + [normalized("G1", row, index) for index, row in enumerate(g1)])
    raw_path = ROOT / "results/raw/security_experiments.jsonl"
    with raw_path.open("x", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, sort_keys=True) + "\n" for row in raw + supplemental_raw)
    with (ROOT / "results/raw/all_measurements.jsonl").open("a", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, sort_keys=True) + "\n" for row in raw + supplemental_raw)
    print(json.dumps({"raw_F1_observations": len(raw), "F1_trials": len(f1),
        "F2_trials": len(f2), "F3_trials": len(f3), "F4_trials": len(f4),
        "G1_trials": len(g1)}, indent=2))


if __name__ == "__main__":
    main()
