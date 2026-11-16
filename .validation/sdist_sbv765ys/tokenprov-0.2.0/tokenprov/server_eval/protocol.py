"""Independent protocol scaling, including hundreds of calls/thousands of events."""
import time

from tokenprov.audit import audit
from tokenprov.server_eval.artifacts import RunArtifacts
from tokenprov.workloads import build_honest_protocol


def run_protocol(config, calls, event_factors, repetitions, progress=print):
    if repetitions < 1 or not calls or not event_factors or any(type(x) is not int or x < 1 for x in calls + event_factors):
        raise ValueError("positive protocol sizes and repetitions are required")
    if any(x < 3 for x in event_factors): raise ValueError("event factors must be >=3 for closed call lifecycles")
    artifacts = RunArtifacts(config, "protocol")
    count = 0
    try:
        for size in calls:
            for factor in event_factors:
                events = size * factor
                progress(f"protocol calls={size} lifecycle_events={events}")
                for repetition in range(repetitions):
                    started = time.perf_counter_ns()
                    fixture = build_honest_protocol(calls=size, lifecycle_events=events)
                    build_ns = time.perf_counter_ns() - started
                    started = time.perf_counter_ns()
                    decision = audit(fixture.authorization, fixture.public_transcript, fixture.private_witness)
                    artifacts.append("protocol.jsonl", {"calls": size, "lifecycle_events": events,
                        "repetition": repetition + 1, "build_ns": build_ns,
                        "audit_ns": time.perf_counter_ns() - started, "relation_timings_ns": decision.timings,
                        "success": decision.accepted, "failed_relation": decision.failed_relation,
                        "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                    if not decision.accepted: raise RuntimeError("honest protocol scaling audit failed")
                    count += 1
        artifacts.write_json("summary.json", {"observations": count,
            "scope": "CPU protocol generation/audit; no LLM, TEE, ZK or Fabric performance"})
        artifacts.status("completed")
        return artifacts.root, count
    except BaseException as exc:
        artifacts.status("failed", error_type=type(exc).__name__)
        raise
