"""Cumulative validation microbenchmark for Section F (historical G2).

These measurements execute real TokenProv cryptographic/protocol operations but
do not execute an LLM, so every observation is explicitly classified as a
PROTOCOL_MICROBENCHMARK.
"""

from __future__ import annotations

import csv

import json

import math

import os

import statistics

import time

from datetime import datetime, timezone

from pathlib import Path

import psutil

from tokenprov.audit import audit

from tokenprov.common.models import RequestClosure

from tokenprov.common.serialization import canonical_bytes, commitment, digest

from tokenprov.crypto import verify_object

from tokenprov.handoff import verify_handoff

from tokenprov.provider import issue_receipt, verify_provider, verify_receipt

from tokenprov.settlement import create_closure, verify_settlement

from tokenprov.workloads import build_honest_protocol

ROOT = None  # Set to a fresh output directory by experiments/paper_protocol.py

SEEDS = (42, 43, 44, 45, 46)

WARMUPS = int(os.environ.get("TOKENPROV_PROTOCOL_WARMUPS", "100"))

SAMPLES = int(os.environ.get("TOKENPROV_PROTOCOL_SAMPLES", "1000"))

def write_csv(name: str, rows: list[dict]) -> None:
    path = ROOT / "results/processed" / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def raw_row(experiment: str, variant: str, seed: int, sample: int,
            parameters: dict, metrics: dict) -> dict:
    return {
        "experiment_id": experiment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": variant,
        "variant": variant,
        "run": SEEDS.index(seed) + 1,
        "seed": seed,
        "request_id": f"{experiment.lower()}-{variant}-{seed}-{sample}",
        "call_id": None,
        "measurement_type": "PROTOCOL_MICROBENCHMARK",
        "parameters": parameters,
        "metrics": metrics,
        "hardware": {"execution": "CPU", "pid": os.getpid()},
        "software": {"cryptography": "Ed25519/SHA-256", "runtime": "CPython"},
        "model": {"name": None, "note": "no LLM executed"},
        "backend": {"protocol": "TokenProv", "provider": "deterministic fixture"},
        "success": True,
        "error": None,
    }

def run_g2(raw: list[dict]) -> list[dict]:
    variants = (
        "Vanilla", "Vanilla + Lifecycle", "+ Bilateral Handoff",
        "+ Provider Binding", "TokenProv-Full")
    rows = []
    process = psutil.Process()
    for seed in SEEDS:
        fixture = build_honest_protocol(calls=8, request_id=f"g2-{seed}",
                                        lifecycle_events=32)
        dag = fixture.private_witness["lifecycle"]
        public = fixture.public_transcript
        handoffs = fixture.private_witness["handoffs"]
        provider_calls = fixture.private_witness["provider_calls"]
        closure = public["closure"]
        settlement = public["settlement"]

        def run_variant(variant: str) -> tuple[int, int, int]:
            cpu_start = time.process_time_ns(); wall_start = time.perf_counter_ns()
            payload: dict = {"request_id": fixture.authorization.request_id}
            if variant != "Vanilla":
                if not dag.validate(public["keys"]): raise RuntimeError("G2 lifecycle rejected")
                payload["lifecycle"] = tuple(dag.events.values())
            if variant in {"+ Bilateral Handoff", "+ Provider Binding", "TokenProv-Full"}:
                for record, boundary, opening in handoffs:
                    if not verify_handoff(record, boundary, opening,
                        public["keys"][record.sender], public["keys"][record.receiver]):
                        raise RuntimeError("G2 handoff rejected")
                payload["handoffs"] = tuple(x[0] for x in handoffs)
            if variant in {"+ Provider Binding", "TokenProv-Full"}:
                for call, receipt, artifact in provider_calls:
                    if not verify_provider(call, receipt, artifact,
                        public["keys"][receipt.provider_id], public["registrations"],
                        public["price_policies"]): raise RuntimeError("G2 provider rejected")
                payload["receipts"] = tuple(x[1] for x in provider_calls)
            if variant == "TokenProv-Full":
                if not verify_settlement(settlement, closure,
                    [x[0] for x in handoffs], [x[1] for x in provider_calls],
                    public["keys"][settlement.broker_id], public["keys"][closure.harness_id]):
                    raise RuntimeError("G2 settlement rejected")
                payload["closure"] = closure; payload["settlement"] = settlement
            wall_ns = time.perf_counter_ns()-wall_start
            cpu_ns = time.process_time_ns()-cpu_start
            return wall_ns, cpu_ns, len(canonical_bytes(payload))

        for variant in variants:
            for _ in range(WARMUPS): run_variant(variant)
            values = []
            for sample in range(SAMPLES):
                wall_ns, cpu_ns, size = run_variant(variant)
                metrics = {"online_latency_ns": wall_ns, "protocol_overhead_ns": wall_ns,
                           "cpu_time_ns": cpu_ns, "bytes_per_request": size,
                           "memory_mb": process.memory_info().rss/2**20}
                raw.append(raw_row("G2", variant, seed, sample,
                    {"calls_per_request": 8, "lifecycle_events": 32,
                     "sampling": f"{WARMUPS} warmups + {SAMPLES} measurements"}, metrics))
                values.append(metrics)
            rows.append({
                "variant": variant,
                "mean_online_latency_ms": statistics.fmean(x["online_latency_ns"] for x in values)/1e6,
                "mean_protocol_overhead_ms": statistics.fmean(x["protocol_overhead_ns"] for x in values)/1e6,
                "bytes_per_request": statistics.fmean(x["bytes_per_request"] for x in values),
                "mean_cpu_time_ms": statistics.fmean(x["cpu_time_ns"] for x in values)/1e6,
                "memory_mb": max(x["memory_mb"] for x in values),
                "sample_count": SAMPLES, "run": SEEDS.index(seed)+1, "seed": seed,
                "measurement_type": "PROTOCOL_MICROBENCHMARK",
            })
    return rows

