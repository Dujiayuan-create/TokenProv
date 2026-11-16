"""Request sampling and bounded per-call deferred proof jobs.

All witnesses are retained, including unsampled requests, for later challenges.
There is no implicit proof backend or simulated substitute in real mode.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass

from tokenprov.assurance.hardware import command_json
from tokenprov.common.serialization import digest, primitive, verify_opening


class CommandProofBackend:
    def __init__(self, settings):
        self.settings = settings

    def run(self, payload):
        if not verify_opening("provider-witness/v1", payload["witness"], payload["opening"], payload["commitment"]):
            raise ValueError("fixed witness commitment does not open")
        binding = digest("deferred-proof-job/v1", payload)
        request = {"schema_version": 1, "binding": binding, "payload": payload}
        started = time.perf_counter_ns()
        proof = command_json(self.settings.prover_command, request, self.settings.timeout_s)
        prove_ns = time.perf_counter_ns() - started
        if proof.get("binding") != binding or "proof" not in proof:
            raise ValueError("prover output is not bound to this call")
        started = time.perf_counter_ns()
        verified = command_json(self.settings.verifier_command,
            {**request, "artifact": proof}, self.settings.timeout_s)
        verify_ns = time.perf_counter_ns() - started
        if verified.get("verified") is not True or verified.get("binding") != binding:
            raise ValueError("proof verification rejected")
        return {"artifact": proof, "prove_wall_ns": prove_ns, "verify_wall_ns": verify_ns,
                "proof_bytes": len(json.dumps(proof["proof"], separators=(",", ":")).encode())}


class FixtureProofBackend:
    """Only for explicitly labeled smoke tests; supplies no cryptographic proof."""
    def run(self, payload):
        if not verify_opening("provider-witness/v1", payload["witness"], payload["opening"], payload["commitment"]):
            raise ValueError("fixture witness opening rejected")
        return {"artifact": {"scope": "FIXTURE_COMMITMENT_CHECK_ONLY"},
                "prove_wall_ns": 0, "verify_wall_ns": 0, "proof_bytes": 0}


@dataclass(frozen=True)
class Task:
    metadata: dict
    witness_path: object
    queued_ns: int
    settled_ns: int
    bundle_hash: str | None = None
    witness_index: int | None = None


class AuditQueue:
    def __init__(self, artifacts, settings, *, fixture=False, backend=None):
        self.artifacts, self.settings = artifacts, settings
        self.fixture = fixture
        self.enabled = fixture or backend is not None or bool(settings.prover_command)
        self.backend = backend or (FixtureProofBackend() if fixture else CommandProofBackend(settings))
        self.queue = queue.Queue(maxsize=settings.queue_capacity)
        self.failures, self.completed, self.submitted = 0, 0, 0
        self.storage_errors = 0
        self.first_queued_ns, self.last_finished_ns = None, None
        self.active, self.peak_active, self.peak_queued = 0, 0, 0
        self.lock = threading.Lock()
        self.closed = False
        self.threads = []
        self.witness_root = artifacts.root / "witnesses"
        self.witness_root.mkdir(mode=0o700)
        if self.enabled:
            for index in range(settings.workers):
                thread = threading.Thread(target=self._worker, name=f"proof-worker-{index}", daemon=False)
                thread.start(); self.threads.append(thread)

    def submit_request(self, metadata, inputs, *, selected, settled_ns, bundle=None):
        if self.closed: raise RuntimeError("proof queue is closed")
        expected = 0
        observations = []
        for index, item in enumerate(inputs):
            if bundle:
                from pathlib import Path
                path = Path(bundle["path"])
                size = 0  # Referenced bytes already counted in repository_bytes.
            else:
                payload = primitive(item)
                name = digest("witness-file/v1", {"request_id": item["request_id"], "call_id": item["call_id"]}) + ".json"
                path = self.witness_root / name
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                size = path.stat().st_size
            observations.append({**metadata, "call_id": item["call_id"],
                "file": str(path.relative_to(self.artifacts.root)), "bytes": size,
                "storage": "evidence_bundle_reference" if bundle else "standalone_witness",
                "bundle_content_hash": bundle["content_hash"] if bundle else None,
                "witness_index": index if bundle else None,
                "commitment": item["commitment"], "retained_until_unix_ns": time.time_ns() + self.settings.retention_s * 10**9,
                "selected": selected, "deleted": False})
            if selected and self.enabled:
                expected += 1
                task = Task({**metadata, "call_id": item["call_id"]}, path, time.perf_counter_ns(), settled_ns,
                    bundle["content_hash"] if bundle else None, index if bundle else None)
                # Backpressure happens after the request's online clock stopped.
                self.queue.put(task)
                with self.lock:
                    self.submitted += 1
                    if self.first_queued_ns is None: self.first_queued_ns = task.queued_ns
                    self.peak_queued = max(self.peak_queued, self.queue.qsize())
        self.artifacts.append_many("witnesses.jsonl", observations)
        return expected

    @staticmethod
    def _payload(task):
        value = json.loads(task.witness_path.read_text(encoding="utf-8"))
        if task.witness_index is None: return value
        actual = value.pop("content_hash")
        if actual != task.bundle_hash or digest("evidence-object/v1", value) != actual:
            raise ValueError("immutable evidence bundle hash mismatch")
        payload = value["payload"]["provider_witnesses"][task.witness_index]
        if payload["call_id"] != task.metadata["call_id"] or payload["request_id"] != task.metadata["request_id"]:
            raise ValueError("bundle witness does not match scheduled call")
        return payload

    def _worker(self):
        while True:
            task = self.queue.get()
            if task is None:
                self.queue.task_done(); return
            started = time.perf_counter_ns()
            with self.lock:
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
            row = {**task.metadata, "queued_ns": task.queued_ns, "started_ns": started,
                "queue_wait_ns": started - task.queued_ns,
                "proof_source": "FIXTURE" if self.fixture else "EXTERNAL_VERIFIED_PROOF"}
            try:
                payload = self._payload(task)
                value = self.backend.run(payload)
                artifact = value.pop("artifact")
                artifact_name = "proof-" + digest("proof-artifact-file/v1", {
                    "request_id": task.metadata["request_id"], "call_id": task.metadata["call_id"]}) + ".json"
                self.artifacts.write_json(artifact_name, artifact)
                row.update(value)
                row.update({"success": True, "artifact": artifact_name,
                    "scope": artifact.get("scope", "external-backend-declared"),
                    "finished_ns": time.perf_counter_ns()})
                row["completion_after_settlement_ns"] = row["finished_ns"] - task.settled_ns
                with self.lock: self.completed += 1
            except Exception as exc:
                row.update({"success": False, "error_type": type(exc).__name__, "finished_ns": time.perf_counter_ns()})
                with self.lock: self.failures += 1
            finally:
                row["worker_service_wall_ns"] = time.perf_counter_ns() - started
                row["completion_after_settlement_ns"] = row["finished_ns"] - task.settled_ns
                try:
                    self.artifacts.append("proofs.jsonl", row)
                except Exception:
                    with self.lock: self.storage_errors += 1
                finally:
                    with self.lock:
                        self.active -= 1
                        self.last_finished_ns = max(self.last_finished_ns or 0, row["finished_ns"])
                    self.queue.task_done()

    def close(self):
        if self.closed: return
        self.queue.join()
        for _ in self.threads: self.queue.put(None)
        for thread in self.threads: thread.join()
        self.closed = True
        if self.storage_errors: raise RuntimeError("proof observations could not be persisted; run is invalid")

    def stats(self):
        return {"enabled": self.enabled, "submitted": self.submitted, "completed": self.completed,
                "failed": self.failures, "peak_active_workers": self.peak_active,
                "storage_errors": self.storage_errors,
                "audit_makespan_ns": self.last_finished_ns - self.first_queued_ns if self.first_queued_ns is not None and self.last_finished_ns else None,
                "peak_queued_jobs": self.peak_queued, "queue_capacity": self.settings.queue_capacity,
                "witness_policy": "retain_all_until_explicit_operator_cleanup"}
