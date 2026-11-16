"""Uniform model/system paths, per-request isolation, and separated clocks."""
from __future__ import annotations

import concurrent.futures
import json
import random
import secrets
import time
from dataclasses import replace
from pathlib import Path

from tokenprov.broker import NativeTokenProvBroker
from tokenprov.common.models import ServiceConfiguration
from tokenprov.common.serialization import digest, primitive
from tokenprov.harness import EvaluationHarness
from tokenprov.server_eval.artifacts import RunArtifacts
from tokenprov.server_eval.audit_jobs import AuditQueue
from tokenprov.server_eval.metrics import summarize
from tokenprov.server_eval.model_artifacts import make_descriptor
from tokenprov.server_eval.transport import JsonHttpClient


class PromptFactory:
    def __init__(self, endpoint):
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(endpoint.tokenizer_path, local_files_only=True,
            trust_remote_code=False)

    def messages(self, target, marker):
        padding = max(0, target - 64)
        for _ in range(16):
            messages = ({"role": "user", "content": f"Marker {marker}. Answer briefly. Padding:" + " x" * padding},)
            encoded = self.tokenizer.apply_chat_template(list(messages), tokenize=True,
                add_generation_prompt=True, enable_thinking=False)
            count = len(encoded["input_ids"] if hasattr(encoded, "keys") else encoded)
            if count == target: return messages
            padding += target - count
            if padding < 0: break
        raise ValueError(f"cannot calibrate prompt to {target} tokens; use a larger input size")


def load_traces(path):
    result, ids = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        item = json.loads(line)
        if not isinstance(item.get("task_id"), str) or item["task_id"] in ids:
            raise ValueError("trace rows require unique string task_id")
        ids.add(item["task_id"])
        if not isinstance(item.get("calls"), list) or not item["calls"]:
            raise ValueError("each trace requires a nonempty calls list")
        for call in item["calls"]:
            if not isinstance(call.get("messages"), list) or not call["messages"]:
                raise ValueError("each trace call requires messages")
            if any(not isinstance(x, dict) or not isinstance(x.get("role"), str) for x in call["messages"]):
                raise ValueError("invalid trace message")
        result.append(item)
    if not result: raise ValueError("empty trace dataset")
    return result


class TeePool:
    def __init__(self, clients): self.clients, self.verifier = clients, self
    def execute(self, call, context): return self.clients[call.configuration.provider_id].execute(call, context)
    def verify(self, call, receipt, artifact, **kwargs):
        client = self.clients.get(receipt.provider_id)
        return bool(client and client.verifier.verify(call, receipt, artifact, **kwargs))


def plan(config):
    cells = []
    traces = load_traces(config.workload.trace_file) if config.workload.trace_file else None
    for endpoint, calls, tokens, output, concurrency in config.cells():
        total_requests = config.requests_per_run * config.repetitions * len(config.systems)
        mean_calls = sum(len(t["calls"]) for t in traces) / len(traces) if traces else calls
        cells.append({"endpoint": "mixed" if config.workload.routing == "round_robin" else endpoint.id,
            "calls_per_request": "trace-dependent" if traces else calls,
            "input_tokens_target": None if traces else tokens, "max_output_tokens": output,
            "concurrency": concurrency, "requests_per_system_per_repetition": config.requests_per_run,
            "repetitions": config.repetitions, "systems": list(config.systems),
            "measured_requests": total_requests, "estimated_model_calls": total_requests * mean_calls,
            "expected_proof_jobs_if_backend_enabled": config.requests_per_run * config.repetitions
                * int("TokenProv-Full" in config.systems) * mean_calls * config.audit.probability})
    return {"schema_version": 1, "cells": cells, "output_root": str(config.output_root),
            "notes": ["plan performs no inference; expected counts are not measured counts",
                      "TEE and proof backend requirements must pass preflight before real execution"]}


def run(config, *, fixture=False, progress=print, diagnostics=None):
    if fixture and "TokenProv-TEE" in config.systems:
        raise ValueError("fixture mode never simulates TokenProv-TEE")
    artifacts = RunArtifacts(config, "fixture" if fixture else "real")
    if diagnostics is not None: artifacts.write_json("preflight.json", diagnostics)
    jobs = AuditQueue(artifacts, config.audit, fixture=fixture)
    sample_rng = random.Random(config.seed) if fixture else secrets.SystemRandom()
    order_rng = random.Random(config.seed)
    clients = {}
    try:
        traces = load_traces(config.workload.trace_file) if config.workload.trace_file else None
        factories = {ep.id: PromptFactory(ep) for ep in config.endpoints} if not fixture and not traces else {}
        if "TokenProv-TEE" in config.systems:
            from tokenprov.assurance.tee import TeeClient, TeePolicy
            for endpoint in config.endpoints:
                descriptor = make_descriptor(endpoint.model, endpoint.revision, endpoint.model_hash, endpoint.tokenizer_hash, tee=True)
                started = time.perf_counter_ns()
                clients[endpoint.id] = TeeClient(endpoint.tee_url, TeePolicy.load(endpoint.tee_policy), descriptor,
                    api_key=endpoint.api_key(tee=True), timeout_s=endpoint.timeout_s,
                    session_observer=lambda session, ep=endpoint: artifacts.append("tee_sessions.jsonl", {"endpoint_id": ep.id, "session": session}))
                artifacts.append("setup.jsonl", {"endpoint_id": endpoint.id,
                    "tee_enrollment_ns": time.perf_counter_ns() - started})
        for endpoint, calls, tokens, output, concurrency in config.cells():
            endpoints = config.endpoints if config.workload.routing == "round_robin" else (endpoint,)
            cell_id = f"{'mixed' if len(endpoints)>1 else endpoint.id}-k{calls}-in{tokens}-out{output}-c{concurrency}"
            for repetition in range(config.repetitions):
                systems = list(config.systems); order_rng.shuffle(systems)
                for system in systems:
                    progress(f"{cell_id} repetition={repetition+1}/{config.repetitions} system={system}")
                    tee = system == "TokenProv-TEE"
                    descriptors = {ep.id: make_descriptor(ep.model, ep.revision, ep.model_hash, ep.tokenizer_hash, tee=tee) for ep in endpoints}
                    configs = [ServiceConfiguration(ep.id, ep.model, "fixture://local" if fixture else ep.tee_url if tee else ep.url(),
                        {"temperature": 0, "max_tokens": output, "chat_template_kwargs": {"enable_thinking": False}},
                        {"cross_call_kv": "disabled" if tee else "unavailable"},
                        {"source": "local-generated-token-ids" if tee else "provider-usage"}, "local-research-v1") for ep in endpoints]
                    forwarders = {}
                    for ep in endpoints:
                        if fixture:
                            def synthetic(call, input_count=tokens or 16, output_count=min(output, 4)):
                                return {"model": call.configuration.model_id, "choices": [{"message": {"content": "fixture output"}}],
                                    "usage": {"prompt_tokens": input_count, "completion_tokens": output_count}}
                            forwarders[ep.id] = synthetic
                        else:
                            forwarders[ep.id] = JsonHttpClient(ep.url(), ep.api_key(), ep.timeout_s).completion
                    metadata = {"cell_id": cell_id, "system": system, "repetition": repetition + 1,
                        "execution_layout": "shared_deployment_compact_evidence_v1",
                        "concurrency": concurrency, "endpoint_ids": [ep.id for ep in endpoints],
                        "declared_node_ids": sorted({ep.node_id for ep in endpoints}),
                        "input_tokens_target": None if traces else tokens, "max_output_tokens": output,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK" if fixture else "REAL_MODEL_SERVING",
                        "workload_kind": "trace_call_replay" if traces else "synthetic_sequential",
                        "assurance_level": "tee-local-inference" if tee else "signature-protocol-only",
                        "proof_backend": "fixture" if fixture else "external" if jobs.enabled else "disabled"}

                    # Identities, deployment registrations and stores belong to
                    # a service deployment, rather than to every request.
                    setup_started = time.perf_counter_ns()
                    base_harness = EvaluationHarness(NativeTokenProvBroker(configs, forwarders),
                        artifacts.root / "runtime" / f"{cell_id}-{system}-r{repetition+1}",
                        model_reference=endpoint.model, model_revision=endpoint.revision,
                        model_hash=endpoint.model_hash, tokenizer_hash=endpoint.tokenizer_hash,
                        provider_descriptors=descriptors, tee_client=TeePool(clients) if tee else None,
                        compact=True)
                    artifacts.append("setup.jsonl", {**metadata, "deployment_setup_ns": time.perf_counter_ns() - setup_started,
                        "registration_count": len(base_harness.registrations),
                        "public_keys": {name: pair.public_key for name, pair in base_harness.keys.items()},
                        "registrations": primitive(base_harness.registrations)})

                    def prepare(index, warmup):
                        request_id = f"r-{secrets.token_hex(12)}"
                        if traces:
                            trace = traces[index % len(traces)]
                            messages = tuple(tuple(dict(m) for m in c["messages"]) for c in trace["calls"])
                            task_id = trace["task_id"]
                        else:
                            messages = []
                            for call_index in range(calls):
                                ep = endpoints[call_index % len(endpoints)]
                                shared = call_index * 100 < calls * config.workload.shared_prompt_percent
                                marker = f"{artifacts.run_id}-{cell_id}-{repetition}-{'warm' if warmup else 'measured'}-" + ("shared" if shared else f"{index}-{call_index}")
                                messages.append(({"role": "user", "content": "fixture request"},) if fixture else factories[ep.id].messages(tokens, marker))
                            messages = tuple(messages)
                            task_id = f"synthetic-{index}"
                        return request_id, messages, task_id, warmup

                    def execute(prepared):
                        request_id, messages, task_id, warmup = prepared
                        local_meta = {**metadata, "request_id": request_id, "task_id": task_id, "warmup": warmup}
                        seen_calls = []
                        def on_call(row):
                            seen_calls.append(row)
                        started = time.perf_counter_ns()
                        row = {**local_meta, "expected_requests": config.requests_per_run, "actual_calls": 0,
                            "workload_content_hash": digest("evaluation-call-inputs/v1", messages),
                            "expected_proof_jobs": 0, "audit_selected": False,
                            "audit_sampling_unit": "request", "audit_selection_source": "seeded_fixture" if fixture else "system_random_after_settlement"}
                        try:
                            harness = base_harness.for_broker(NativeTokenProvBroker(configs, forwarders))
                            setup_ns = time.perf_counter_ns() - started
                            result = harness.execute(request_id, messages[0], variant=system,
                                calls_per_request=len(messages), lifecycle_events=4*len(messages), call_messages=messages,
                                max_bill_microunits=2**62, on_call=on_call)
                            if not result.accepted: raise ValueError(f"protocol audit rejected ({result.failed_relation})")
                            if not fixture and not traces and any(r["actual_input_tokens"] != tokens for r in result.call_records):
                                raise ValueError("provider input token count differs from calibrated target")
                            if any(r["actual_output_tokens"] > output for r in result.call_records):
                                raise ValueError("provider output exceeds configured maximum")
                            selected = not warmup and sample_rng.random() < config.audit.probability
                            settled = time.perf_counter_ns()
                            count = jobs.submit_request(local_meta, result.proof_inputs,
                                selected=selected and system == "TokenProv-Full", settled_ns=settled,
                                bundle=result.witness_bundle)
                            row.update({"success": True, "setup_ns": setup_ns, "timings_ns": result.timings_ns,
                                "actual_calls": result.actual_calls, "expected_proof_jobs": count, "audit_selected": selected,
                                "proof_eligible_calls": len(result.proof_inputs) if system == "TokenProv-Full" else 0,
                                "protocol_audit_policy": "all_requests_off_online_path",
                                "protocol_bytes": result.protocol_bytes,
                                "repository_bytes": result.repository_bytes, "ledger_bytes": result.ledger_bytes,
                                "retained_witness_payload_bytes": result.witness_bytes,
                                "proof_queue_submit_ns": time.perf_counter_ns() - settled})
                        except Exception as exc:
                            row.update({"success": False, "error_type": type(exc).__name__,
                                "actual_calls": sum(r["success"] for r in seen_calls),
                                "attempted_calls": len(seen_calls), "timings_ns": {},
                                "failed_request_wall_ns": time.perf_counter_ns() - started})
                        artifacts.append_many("calls.jsonl", ({**local_meta, **call,
                            "token_count_source": "fixture" if fixture else "local-generated-token-ids" if tee else "provider_usage"}
                            for call in seen_calls))
                        row["request_pipeline_ns"] = time.perf_counter_ns() - started
                        return row

                    # Preparation and tokenizer calibration stay outside measured requests.
                    for index in range(config.warmups):
                        row = execute(prepare(index, True)); row["batch_wall_ns"] = 0
                        artifacts.append("requests.jsonl", row)
                        if not row["success"]: raise RuntimeError(f"warmup failed ({row['error_type']})")
                    prepared = [prepare(index, False) for index in range(config.requests_per_run)]
                    batch_started = time.perf_counter_ns()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                        rows = list(executor.map(execute, prepared))
                    batch_wall = time.perf_counter_ns() - batch_started
                    for row in rows:
                        row["batch_wall_ns"] = batch_wall
                        artifacts.append("requests.jsonl", row)
        artifacts.status("draining")
        drain_started = time.perf_counter_ns()
        jobs.close()
        drain_wall_ns = time.perf_counter_ns() - drain_started
        summary = summarize(artifacts.root)
        summary["proof_queue"] = jobs.stats()
        summary["proof_queue"]["final_drain_wall_ns"] = drain_wall_ns
        artifacts.write_json("summary.json", summary)
        artifacts.write_json("validation.json", summary["validation"])
        ok = summary["validation"]["passed"] and not summary["counts"]["failed_measured_requests"] and not jobs.failures
        artifacts.status("completed" if ok else "failed", counts=summary["counts"])
        return artifacts.root, summary
    except BaseException as exc:
        artifacts.status("draining", error_type=type(exc).__name__)
        jobs.close()
        artifacts.status("failed", error_type=type(exc).__name__, proof_queue=jobs.stats())
        raise
