"""Recompute all quantities from raw rows, retaining exact denominators."""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def _rows(root, name):
    path = root / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _quantile(values, p, minimum):
    if len(values) < minimum: return None
    values = sorted(values)
    return values[max(0, math.ceil(p * len(values)) - 1)]


def _interval(values):
    if len(values) < 2: return None
    # Student-t interval across independent repetition means, not pooled calls.
    critical = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
                7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}.get(len(values) - 1, 2.0)
    margin = critical * statistics.stdev(values) / math.sqrt(len(values))
    mean = statistics.fmean(values)
    return [mean - margin, mean + margin]


def summarize(directory):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    requests, calls, proofs = (_rows(root, name) for name in ("requests.jsonl", "calls.jsonl", "proofs.jsonl"))
    issues = []
    all_rows = requests + calls + proofs
    if any(x.get("schema_version") != 1 or x.get("run_id") != manifest["run_id"]
        or x.get("execution_mode") != manifest["mode"] for x in all_rows):
        issues.append("mixed schema, run id, or fixture/real observations")
    if not requests: issues.append("no measured request observations")
    layouts = {x.get("execution_layout", "legacy_per_request") for x in requests}
    if len(layouts) > 1: issues.append("mixed execution layouts")
    ids = [x["request_id"] for x in requests]
    if len(set(ids)) != len(ids): issues.append("duplicate request id")
    calls_by_request, proofs_by_request = defaultdict(list), defaultdict(list)
    for row in calls: calls_by_request[row["request_id"]].append(row)
    for row in proofs: proofs_by_request[row["request_id"]].append(row)
    if set(calls_by_request) - set(ids): issues.append("orphan call observations")
    if set(proofs_by_request) - set(ids): issues.append("orphan proof observations")
    for row in requests:
        observed = calls_by_request[row["request_id"]]
        if len({x["call_id"] for x in observed}) != len(observed): issues.append("duplicate call id")
        if row["success"] and (len(observed) != row["actual_calls"] or not all(x["success"] for x in observed)):
            issues.append("successful request has missing/failed call rows")
        scheduled = proofs_by_request[row["request_id"]]
        if len({x["call_id"] for x in scheduled}) != len(scheduled): issues.append("duplicate proof job for call")
        if len(scheduled) != row["expected_proof_jobs"]: issues.append("sampled request proof-count mismatch")
        if row["expected_proof_jobs"] and row["expected_proof_jobs"] != row["actual_calls"]:
            issues.append("request audit did not schedule every accepted call")
        if any(x["call_id"] not in {c["call_id"] for c in observed} for x in scheduled): issues.append("proof for unknown call")
    measured = [x for x in requests if not x["warmup"]]
    groups = defaultdict(list)
    for row in measured:
        key = (row["cell_id"], row["system"], row["repetition"])
        groups[key].append(row)
    run_stats = []
    for (cell, system, repetition), rows in groups.items():
        expected = {x["expected_requests"] for x in rows}
        if len(expected) != 1 or len(rows) != next(iter(expected)): issues.append("requests-per-repetition count mismatch")
        good = [x for x in rows if x["success"]]
        online = [x["timings_ns"]["T_online"] / 1e6 for x in good]
        jobs = [job for r in rows for job in proofs_by_request[r["request_id"]]]
        failed_jobs = sum(not x["success"] for x in jobs)
        finalized = len(good)
        total_prove = sum(x.get("prove_wall_ns", 0) for x in jobs if x["success"]) / 1e6
        duration = max(x["batch_wall_ns"] for x in rows) / 1e9
        run_stats.append({"cell_id": cell, "system": system, "repetition": repetition,
            "attempted_requests": len(rows), "successful_requests": finalized, "failed_requests": len(rows) - finalized,
            "actual_calls": sum(x["actual_calls"] for x in rows),
            "mean_online_ms": statistics.fmean(online) if online else None,
            "mean_request_pipeline_ms": statistics.fmean(x["request_pipeline_ns"] / 1e6 for x in rows),
            "p50_online_ms": _quantile(online, .5, 1), "p95_online_ms": _quantile(online, .95, 100),
            "p99_online_ms": _quantile(online, .99, 1000), "quantile_sample_count": len(online),
            "throughput_finalized_requests_s": finalized / duration if duration else 0,
            "sampled_requests": sum(x["audit_selected"] for x in good),
            "proof_jobs": len(jobs), "failed_proof_jobs": failed_jobs,
            "aggregate_prove_ms": total_prove,
            "prove_ms_per_finalized_request": total_prove / finalized if finalized and jobs and not failed_jobs else None,
            "sum_worker_service_ms_including_failures": sum(x.get("worker_service_wall_ns", 0) for x in jobs) / 1e6,
            "sum_job_verify_ms": sum(x.get("verify_wall_ns", 0) for x in jobs if x["success"]) / 1e6,
            "proof_backend_complete": bool(jobs) and failed_jobs == 0,
            "measurement_type": rows[0]["measurement_type"]})
    grouped = defaultdict(list)
    for row in run_stats: grouped[(row["cell_id"], row["system"])].append(row)
    aggregate = []
    for (cell, system), rows in grouped.items():
        means = [r["mean_online_ms"] for r in rows if r["mean_online_ms"] is not None]
        deltas = []
        for r in rows:
            baseline = next((x for x in run_stats if x["cell_id"] == cell and x["system"] == "Vanilla" and x["repetition"] == r["repetition"]), None)
            if baseline and baseline["mean_online_ms"] and r["mean_online_ms"] is not None:
                deltas.append(100 * (r["mean_online_ms"] / baseline["mean_online_ms"] - 1))
        aggregate.append({"cell_id": cell, "system": system, "repetitions": len(rows),
            "measured_requests": sum(x["attempted_requests"] for x in rows),
            "successful_requests": sum(x["successful_requests"] for x in rows),
            "mean_online_ms": statistics.fmean(means) if means else None,
            "mean_online_95ci_ms": _interval(means),
            "paired_overhead_percent": statistics.fmean(deltas) if deltas else None,
            "paired_overhead_95ci_percent": _interval(deltas)})
    return {"schema_version": 1, "run_id": manifest["run_id"], "execution_mode": manifest["mode"],
        "execution_layout": next(iter(layouts)) if len(layouts) == 1 else None,
        "validation": {"passed": not issues, "issues": sorted(set(issues))},
        "counts": {"measured_requests": len(measured), "warmup_requests": len(requests) - len(measured),
            "failed_measured_requests": sum(not x["success"] for x in measured),
            "proof_jobs": len(proofs), "failed_proof_jobs": sum(not x["success"] for x in proofs)},
        "per_repetition": run_stats, "aggregate": aggregate,
        "notes": ["p95 requires n>=100; p99 requires n>=1000 within each repetition",
            "prove work is a sum of job wall durations; it is not CPU time or elapsed audit makespan",
            "all deferred witnesses are retained, including unsampled requests"]}
