"""Configurable multi-call harness used by the comprehensive evaluation."""
from __future__ import annotations

import secrets
import time
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tokenprov.audit import audit
from tokenprov.broker import Broker
from tokenprov.common.models import (
    Authorization, CallIntent, DeploymentDescriptor, DeploymentRegistration,
    LifecycleEvent, PricePolicy, ProviderCallEvidence, ProviderReceipt,
    RequestClosure, Settlement, UsageVector,
)
from tokenprov.common.serialization import canonical_bytes, commitment, digest
from tokenprov.crypto import generate_keypair, sign_object
from tokenprov.evidence import LocalEvidenceRepository
from tokenprov.handoff import create_handoff, countersign_handoff
from tokenprov.ledger import LocalAuthenticatedLedger
from tokenprov.lifecycle import LifecycleDAG
from tokenprov.provider import issue_receipt, register_deployment
from tokenprov.settlement import create_closure, create_settlement, evidence_root
from tokenprov.user import create_authorization


Variant = Literal["Vanilla", "SignedLog", "SignedLogPlus", "FullTrace", "TokenProv-Full", "TokenProv-TEE"]


@dataclass(frozen=True)
class EvaluationResult:
    texts: tuple[str, ...]
    usages: tuple[UsageVector, ...]
    timings_ns: dict[str, int]
    accepted: bool
    failed_relation: str | None
    protocol_bytes: int
    application_bytes: int
    repository_bytes: int
    ledger_bytes: int
    witness_bytes: int
    receipt_bytes: int
    audit_relation_timings_ns: dict[str, int]
    actual_calls: int
    lifecycle_events: int
    call_records: tuple[dict[str, Any], ...] = ()
    proof_inputs: tuple[dict[str, Any], ...] = ()
    witness_bundle: dict[str, str] | None = None


class EvaluationHarness:
    """Run all evaluation variants behind one flag and one broker/provider path."""

    def __init__(self, broker: Broker, root: str | Path, *, model_reference: str,
                 model_revision: str, model_hash: str, tokenizer_hash: str,
                 price_policy: PricePolicy | None = None,
                 provider_descriptors: dict[str, DeploymentDescriptor] | None = None,
                 tee_client=None, compact: bool = False):
        self.broker = broker
        self.compact = compact
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        provider_ids = tuple(config.provider_id for config in getattr(broker, "configurations", ())) or ("P1",)
        if set(provider_ids) & {"U", "H", "B"}:
            raise ValueError("provider ids must not collide with protocol roles")
        self.keys = {name: generate_keypair() for name in ("U", "H", "B", *provider_ids)}
        self.tee_client = tee_client
        self.price = price_policy or PricePolicy("local-research-v1", n_pf_rate=1,
                                                 n_out_rate=2)
        descriptor = DeploymentDescriptor(
            model_reference, model_revision, model_hash, tokenizer_hash,
            digest("canonicalization-rule/v1", "vllm-openai-chat-template"),
            digest("serving-semantics/v1", {
                "cache": "stock-vllm-api-cache-state-unavailable",
                "metering": "vllm-openai-usage",
                "pricing": self.price,
                "serving": "bf16,max_model_len=4096,prefix_caching=true",
            }))
        self.registrations = {}
        self.registration_by_provider = {}
        for provider_id in provider_ids:
            selected = (provider_descriptors or {}).get(provider_id, descriptor)
            registration = register_deployment(DeploymentRegistration(
                "dep-" + provider_id + "-" + digest("deployment-descriptor/v1", selected)[:16],
                provider_id, selected, commitment("deployment/v1", selected, secrets.token_hex(16))),
                self.keys[provider_id].private_key)
            self.registrations[registration.deployment_id] = registration
            self.registration_by_provider[provider_id] = registration
        self.registration = self.registration_by_provider[provider_ids[0]]
        self.repository = LocalEvidenceRepository(self.root / "evidence")
        self.ledger = LocalAuthenticatedLedger(
            self.root / "ledger.jsonl",
            {name: self.keys[name].public_key for name in ("H", "B", *provider_ids)})
        for provider_id in provider_ids:
            self.ledger.append("reg-" + provider_id, "deployment", self.registration_by_provider[provider_id],
                               provider_id, self.keys[provider_id].private_key)

    def for_broker(self, broker: Broker):
        """Share stable identities/storage; all per-request protocol state is local."""
        if broker.configurations != self.broker.configurations:
            raise ValueError("broker configurations differ from initialized deployments")
        instance = copy(self)
        instance.broker = broker
        return instance

    @staticmethod
    def _parse(raw: Any) -> tuple[str, UsageVector, dict[str, Any]]:
        if not isinstance(raw, dict) and hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        usage = raw["usage"]
        return (raw["choices"][0]["message"]["content"],
                UsageVector(n_pf=int(usage["prompt_tokens"]),
                            n_out=int(usage["completion_tokens"])), raw)

    @staticmethod
    def _size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) \
            if path.exists() else 0

    def execute(self, request_id: str, messages: tuple[dict[str, Any], ...], *,
                variant: Variant = "TokenProv-Full", calls_per_request: int = 1,
                lifecycle_events: int | None = None,
                call_messages: tuple[tuple[dict[str, Any], ...], ...] | None = None,
                max_bill_microunits: int = 10_000_000, on_call=None) -> EvaluationResult:
        if variant not in {"Vanilla", "SignedLog", "SignedLogPlus", "FullTrace", "TokenProv-Full", "TokenProv-TEE"}:
            raise ValueError("unknown evaluation variant")
        if variant == "TokenProv-TEE" and self.tee_client is None:
            raise ValueError("TokenProv-TEE requires a real attested execution client")
        if calls_per_request < 1:
            raise ValueError("calls_per_request must be positive")
        if call_messages is not None and len(call_messages) != calls_per_request:
            raise ValueError("call_messages length must equal calls_per_request")
        request_input = call_messages if call_messages is not None else messages
        minimum_events = 1 + 2 * calls_per_request
        lifecycle_events = minimum_events if lifecycle_events is None else lifecycle_events
        if variant in {"SignedLogPlus", "FullTrace", "TokenProv-Full", "TokenProv-TEE"} and lifecycle_events < minimum_events:
            raise ValueError(f"lifecycle_events must be at least {minimum_events}")
        timings = {name: 0 for name in (
            "T_auth", "T_lifecycle", "T_HB_handoff", "T_BP_handoff", "T_llm",
            "T_provider_extra", "T_receipt", "T_close", "T_merkle",
            "T_settle_local", "T_evidence_write", "T_ledger", "T_audit")}
        repository_before = self._size(self.root / "evidence") if not self.compact else 0
        ledger_before = self._size(self.root / "ledger.jsonl") if not self.compact else 0
        online_start = time.perf_counter_ns()
        application_bytes = protocol_bytes = 0
        texts: list[str] = []
        usages: list[UsageVector] = []
        call_records = []
        proof_inputs = []

        def observe(call, raw, text, usage, elapsed_ns):
            row = {"request_id": request_id, "call_id": call.call_id, "success": True,
                "provider_id": call.configuration.provider_id, "requested_model": call.configuration.model_id,
                "response_model": raw.get("model"), "actual_input_tokens": usage.n_pf,
                "actual_output_tokens": usage.n_out,
                "max_output_tokens": call.configuration.decoding_config.get("max_tokens"),
                "provider_wall_ns": elapsed_ns, "output_hash": digest("provider-output/v1", text),
                "token_count_source": "provider_usage"}
            call_records.append(row)
            if on_call: on_call(row)

        def forward(call, context=None):
            started = time.perf_counter_ns()
            try:
                if context is not None: return self.tee_client.execute(call, context)
                return self.broker.forward(call)
            except Exception as exc:
                if on_call:
                    on_call({"request_id": request_id, "call_id": call.call_id,
                        "provider_id": call.configuration.provider_id,
                        "requested_model": call.configuration.model_id, "success": False,
                        "error_type": type(exc).__name__, "provider_wall_ns": time.perf_counter_ns() - started})
                raise

        if variant == "Vanilla":
            for index in range(calls_per_request):
                current_messages = call_messages[index] if call_messages is not None else messages
                intent = CallIntent(request_id, f"c-{index}", current_messages, {})
                call = self.broker.make_call(intent, "baseline-no-handoff")
                application_bytes += len(canonical_bytes({"model": call.configuration.model_id,
                    "messages": call.transformed_messages, **call.configuration.decoding_config}))
                start = time.perf_counter_ns()
                raw = forward(call)
                elapsed_ns = time.perf_counter_ns() - start
                timings["T_llm"] += elapsed_ns
                text, usage, raw_dict = self._parse(raw)
                observe(call, raw_dict, text, usage, elapsed_ns)
                application_bytes += len(canonical_bytes(raw_dict))
                texts.append(text); usages.append(usage)
            timings["T_online"] = time.perf_counter_ns() - online_start
            timings["T_request_total"] = timings["T_online"]
            return EvaluationResult(tuple(texts), tuple(usages), timings, True, None,
                                    0, application_bytes, 0, 0, 0, 0, {},
                                    calls_per_request, 0, tuple(call_records))

        if variant == "SignedLog":
            signed_logs = []
            start = time.perf_counter_ns()
            auth_log = {"stage": "authorization", "request_id": request_id,
                        "input_hash": digest("signed-log-input/v1", request_input)}
            signed_logs.append({**auth_log, "signature": sign_object(
                self.keys["U"].private_key, "signed-log/v1", auth_log)})
            timings["T_auth"] += time.perf_counter_ns() - start
            for index in range(calls_per_request):
                call_id = f"c-{index}"
                current_messages = call_messages[index] if call_messages is not None else messages
                start = time.perf_counter_ns()
                dispatch = {"stage": "dispatch", "request_id": request_id,
                            "call_id": call_id, "messages_hash": digest("messages/v1", current_messages)}
                signed_logs.append({**dispatch, "signature": sign_object(
                    self.keys["B"].private_key, "signed-log/v1", dispatch)})
                timings["T_lifecycle"] += time.perf_counter_ns() - start
                intent = CallIntent(request_id, call_id, current_messages, {})
                call = self.broker.make_call(intent, "signed-log-no-bilateral-handoff")
                application_bytes += len(canonical_bytes({"model": call.configuration.model_id,
                    "messages": call.transformed_messages, **call.configuration.decoding_config}))
                start = time.perf_counter_ns(); raw = forward(call)
                elapsed_ns = time.perf_counter_ns() - start
                timings["T_llm"] += elapsed_ns
                text, usage, raw_dict = self._parse(raw)
                observe(call, raw_dict, text, usage, elapsed_ns)
                application_bytes += len(canonical_bytes(raw_dict))
                start = time.perf_counter_ns()
                result_log = {"stage": "provider_result", "request_id": request_id,
                              "call_id": call_id, "usage": usage,
                              "output_hash": digest("provider-output/v1", text)}
                signed_logs.append({**result_log, "signature": sign_object(
                    self.keys[call.configuration.provider_id].private_key, "signed-log/v1", result_log)})
                timings["T_receipt"] += time.perf_counter_ns() - start
                texts.append(text); usages.append(usage)
            protocol_bytes = len(canonical_bytes(signed_logs))
            timings["T_online"] = time.perf_counter_ns() - online_start
            start = time.perf_counter_ns()
            self.repository.put(request_id, "signed_logs", tuple(signed_logs))
            timings["T_evidence_write"] = time.perf_counter_ns() - start
            timings["T_request_total"] = time.perf_counter_ns() - online_start
            return EvaluationResult(tuple(texts), tuple(usages), timings, True, None,
                protocol_bytes, application_bytes,
                self.repository.request_bytes(request_id) if self.compact else self._size(self.root / "evidence") - repository_before, 0,
                0, 0, {},
                calls_per_request, len(signed_logs), tuple(call_records))

        # FullTrace and TokenProv-Full share the exact TokenProv protocol. FullTrace
        # additionally retains every accessible request and response object.
        start = time.perf_counter_ns()
        input_opening = secrets.token_hex(16)
        auth = create_authorization(Authorization(
            request_id, {"purpose": "comprehensive-evaluation"},
            commitment("input/v1", request_input, input_opening),
            {"max_bill_microunits": max_bill_microunits, "max_calls": calls_per_request}, secrets.token_hex(16), "U"),
            self.keys["U"].private_key)
        auth_digest = digest("authorization/v1", auth)
        timings["T_auth"] = time.perf_counter_ns() - start

        start = time.perf_counter_ns()
        dag = LifecycleDAG(request_id, auth_digest)
        previous = dag.add(LifecycleEvent(request_id, auth_digest, "e-root", None,
            "authorization_root", (), digest("payload/v1", {}), "active", "H"),
            self.keys["H"].private_key)
        for index in range(lifecycle_events - minimum_events):
            previous = dag.add(LifecycleEvent(request_id, auth_digest, f"e-work-{index}", None,
                "workflow_step", (digest("lifecycle-event/v1", previous),),
                digest("payload/v1", {"index": index}), "active", "H"),
                self.keys["H"].private_key)
        timings["T_lifecycle"] += time.perf_counter_ns() - start

        handoffs = []
        provider_calls = []
        receipts = []
        raw_calls = []
        witness_openings = {}
        for index in range(calls_per_request):
            call_id = f"c-{index}"
            current_messages = call_messages[index] if call_messages is not None else messages
            start = time.perf_counter_ns()
            created = dag.add(LifecycleEvent(request_id, auth_digest, f"e-create-{index}", call_id,
                "call_created", (digest("lifecycle-event/v1", previous),),
                digest("payload/v1", {}), "active", "H"), self.keys["H"].private_key)
            timings["T_lifecycle"] += time.perf_counter_ns() - start
            intent = CallIntent(request_id, call_id, current_messages, {})
            hb_boundary = {"request_id": request_id, "call_id": call_id, "intent": intent}
            hb_opening = secrets.token_hex(16)
            start = time.perf_counter_ns()
            hb = create_handoff(request_id, call_id, "H->B", hb_boundary, hb_opening, (),
                                "H", "B", self.keys["H"].private_key)
            hb = countersign_handoff(hb, hb_boundary, hb_opening, request_id, call_id,
                "H->B", self.keys["H"].public_key, self.keys["B"].private_key)
            timings["T_HB_handoff"] += time.perf_counter_ns() - start
            hb_hash = digest("handoff/v1", hb)
            call = self.broker.make_call(intent, hb_hash)
            provider_id = call.configuration.provider_id
            registration = self.registration_by_provider[provider_id]
            bp_boundary = {"request_id": request_id, "call_id": call_id,
                "provider_call": call,
                "registration_hash": digest("deployment-registration/v1", registration)}
            bp_opening = secrets.token_hex(16)
            start = time.perf_counter_ns()
            bp = create_handoff(request_id, call_id, "B->P", bp_boundary, bp_opening,
                (hb_hash,), "B", provider_id, self.keys["B"].private_key)
            bp = countersign_handoff(bp, bp_boundary, bp_opening, request_id, call_id,
                "B->P", self.keys["B"].public_key, self.keys[provider_id].private_key)
            timings["T_BP_handoff"] += time.perf_counter_ns() - start
            bp_hash = digest("handoff/v1", bp)
            application_bytes += len(canonical_bytes({"model": call.configuration.model_id,
                "messages": call.transformed_messages, **call.configuration.decoding_config}))
            attested = None
            start = time.perf_counter_ns()
            if variant == "TokenProv-TEE":
                attested = forward(call, {
                    "authorization_digest": auth_digest, "deployment_id": registration.deployment_id,
                    "hb_handoff_hash": hb_hash, "bp_handoff_hash": bp_hash,
                    "descriptor": registration.descriptor, "price_policy": self.price})
                raw = attested["response"]
            else:
                raw = forward(call)
            elapsed_ns = time.perf_counter_ns() - start
            timings["T_llm"] += elapsed_ns
            text, usage, raw_dict = self._parse(raw)
            observe(call, raw_dict, text, usage, elapsed_ns)
            application_bytes += len(canonical_bytes(raw_dict))
            start = time.perf_counter_ns()
            cache_status = {"status": "UNAVAILABLE", "source": "stock-vllm-openai-api"}
            shared = dict(request_id=request_id, call_id=call_id,
                authorization_digest=auth_digest, deployment_id=registration.deployment_id,
                service_config_hash=digest("service-configuration/v1", call.configuration),
                hb_handoff_hash=hb_hash, bp_handoff_hash=bp_hash,
                transformation_hash=digest("transformation-rule/v1", call.transformation_rule),
                input_hash=digest("provider-input/v1", call.transformed_messages),
                cache_pre_hash=digest("cache-pre/v1", cache_status),
                cache_post_hash=digest("cache-post/v1", cache_status),
                output_hash=digest("provider-output/v1", text), usage=usage,
                price_version=self.price.version, charge_microunits=self.price.charge(usage))
            witness = {"shared": shared, "provider_model": raw_dict.get("model"),
                       "cache_evidence": cache_status}
            witness_opening = secrets.token_hex(16)
            witness_commitment = commitment("provider-witness/v1", witness, witness_opening)
            mode, anchor, artifact = "signature", witness_commitment, {"mode": "signature"}
            nullifier = digest("call-nullifier/v2", {"authorization_digest": auth_digest, "call_id": call_id})
            if attested is not None:
                shared = attested["shared"]
                # The client verifies that the trusted runtime bound this actual result.
                usage = UsageVector(**shared["usage"]) if isinstance(shared["usage"], dict) else shared["usage"]
                shared = {**shared, "usage": usage}
                witness = attested["witness"]
                witness_opening = attested["opening"]
                witness_commitment = commitment("provider-witness/v1", witness, witness_opening)
                mode, artifact = "tee", attested["artifact"]
                anchor = digest("tee-artifact/v1", artifact)
                nullifier = attested["call_nullifier"]
            call_evidence = ProviderCallEvidence(**shared)
            timings["T_provider_extra"] += time.perf_counter_ns() - start
            start = time.perf_counter_ns()
            receipt = issue_receipt(ProviderReceipt(**shared, billable=True,
                witness_commitment=witness_commitment, assurance_mode=mode,
                assurance_anchor=anchor, call_nullifier=nullifier,
                provider_id=provider_id), self.keys[provider_id].private_key)
            timings["T_receipt"] += time.perf_counter_ns() - start
            start = time.perf_counter_ns()
            previous = dag.add(LifecycleEvent(request_id, auth_digest, f"e-terminal-{index}",
                call_id, "call_terminal", (digest("lifecycle-event/v1", created),),
                digest("payload/v1", shared["output_hash"]), "succeeded", "H"),
                self.keys["H"].private_key)
            timings["T_lifecycle"] += time.perf_counter_ns() - start
            handoffs.extend(((hb, hb_boundary, hb_opening), (bp, bp_boundary, bp_opening)))
            provider_calls.append((call_evidence, receipt, artifact))
            receipts.append(receipt); texts.append(text); usages.append(usage)
            witness_openings[call_id] = witness_opening
            if variant == "FullTrace":
                raw_calls.append({"intent": intent, "provider_call": call, "raw_response": raw_dict,
                                  "witness": witness})
            proof_inputs.append({"request_id": request_id, "call_id": call_id,
                "witness": witness, "opening": witness_opening,
                "commitment": witness_commitment, "receipt": receipt.signed_payload(),
                "price_policy": self.price})
        timings["T_online"] = time.perf_counter_ns() - online_start

        start = time.perf_counter_ns()
        answer_opening = secrets.token_hex(16)
        closure = create_closure(RequestClosure(request_id, auth_digest, dag.root(),
            commitment("response/v1", texts, answer_opening), tuple(dag.call_ids),
            tuple(dag.call_ids), "H"), self.keys["H"].private_key)
        timings["T_close"] = time.perf_counter_ns() - start
        handoff_records = [item[0] for item in handoffs]
        start = time.perf_counter_ns()
        root = evidence_root(handoff_records, receipts, ())
        timings["T_merkle"] = time.perf_counter_ns() - start
        start = time.perf_counter_ns()
        settlement = create_settlement(Settlement(request_id,
            digest("request-closure/v1", closure),
            tuple(digest("handoff/v1", item) for item in handoff_records),
            tuple(digest("provider-receipt/v1", item) for item in receipts), (), root,
            sum(self.price.charge(usage) for usage in usages), secrets.token_hex(16), "B"),
            self.keys["B"].private_key, self.keys["H"].private_key)
        timings["T_settle_local"] = time.perf_counter_ns() - start
        timings["T_settlement"] = timings["T_close"] + timings["T_merkle"] + timings["T_settle_local"]

        start = time.perf_counter_ns()
        witness_bundle = None
        if self.compact:
            stored = self.repository.put(request_id, "bundle", {
                "auth": auth, "lifecycle": tuple(dag.events.values()), "handoffs": handoffs,
                "provider_calls": provider_calls, "closure": closure, "settlement": settlement,
                "input_opening": input_opening, "answer_opening": answer_opening,
                "provider_witnesses": proof_inputs,
                **({"full_trace": raw_calls} if variant == "FullTrace" else {})})
            witness_bundle = {"path": str(self.repository.root / request_id / "bundle" / f"{stored.content_hash}.json"),
                              "content_hash": stored.content_hash}
        else:
            for kind, payload in (("auth", auth), ("lifecycle", tuple(dag.events.values())),
                                  ("handoffs", tuple(handoff_records)), ("receipts", tuple(receipts)),
                                  ("settlement", settlement)):
                self.repository.put(request_id, kind, payload,
                                    deployment_id=self.registration.deployment_id)
            self.repository.put(request_id, "commitments", {
                "input_opening": input_opening, "witness_openings": witness_openings,
                "answer_opening": answer_opening}, deployment_id=self.registration.deployment_id)
            self.repository.put(request_id, "provider_witnesses", tuple(proof_inputs))
            if variant == "FullTrace":
                self.repository.put(request_id, "full_trace", tuple(raw_calls),
                                    deployment_id=self.registration.deployment_id)
        timings["T_evidence_write"] = time.perf_counter_ns() - start
        start = time.perf_counter_ns()
        ledger_records = tuple((f"{request_id}-{suffix}", record_type, payload, member, self.keys[member].private_key)
            for suffix, record_type, payload, member in (
            ("auth", "authorization", digest("authorization/v1", auth), "H"),
            ("close", "closure", digest("request-closure/v1", closure), "H"),
            ("settle", "settlement", digest("settlement/v1", settlement), "B")))
        if self.compact:
            ledger_entries = self.ledger.append_batch(ledger_records)
        else:
            ledger_entries = tuple(self.ledger.append(*record) for record in ledger_records)
        for entry in ledger_entries:
            if not self.ledger.wait_finality(entry.record_id):
                raise RuntimeError("local ledger failed finality")
        timings["T_ledger"] = time.perf_counter_ns() - start
        public = {"keys": {name: pair.public_key for name, pair in self.keys.items()},
            "registrations": self.registrations,
            "price_policies": {self.price.version: self.price},
            "closure": closure, "settlement": settlement}
        private = {"lifecycle": dag, "handoffs": handoffs, "provider_calls": provider_calls}
        start = time.perf_counter_ns()
        if variant == "SignedLogPlus":
            from tokenprov.audit.signedlog import audit_signed_log_plus
            decision = audit_signed_log_plus(auth, public, private)
        else:
            decision = audit(auth, public, private,
                tee_verifier=self.tee_client.verifier if variant == "TokenProv-TEE" else None,
                required_assurance="tee" if variant == "TokenProv-TEE" else None)
        timings["T_audit"] = time.perf_counter_ns() - start
        protocol_bytes = len(canonical_bytes({"auth": auth, "lifecycle": tuple(dag.events.values()),
            "handoffs": tuple(handoff_records), "receipts": tuple(receipts),
            "closure": closure, "settlement": settlement}))
        timings["T_request_total"] = time.perf_counter_ns() - online_start
        return EvaluationResult(tuple(texts), tuple(usages), timings, decision.accepted,
            decision.failed_relation, protocol_bytes, application_bytes,
            self.repository.request_bytes(request_id) if self.compact else self._size(self.root / "evidence") - repository_before,
            sum(len(canonical_bytes(entry)) + 1 for entry in ledger_entries) if self.compact else self._size(self.root / "ledger.jsonl") - ledger_before,
            sum(len(canonical_bytes(item["witness"])) for item in proof_inputs),
            sum(len(canonical_bytes(item)) for item in receipts), decision.timings,
            calls_per_request, len(dag.events), tuple(call_records), tuple(proof_inputs), witness_bundle)
