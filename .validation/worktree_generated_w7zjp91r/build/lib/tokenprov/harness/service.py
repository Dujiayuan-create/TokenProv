"""Single-call end-to-end TokenProv harness for real or deterministic brokers."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenprov.audit import audit
from tokenprov.broker import Broker
from tokenprov.common.models import (
    Authorization, CallIntent, DeploymentDescriptor, DeploymentRegistration,
    LifecycleEvent, PricePolicy, ProviderCallEvidence, ProviderReceipt,
    RequestClosure, Settlement, UsageVector,
)
from tokenprov.common.serialization import commitment, digest
from tokenprov.crypto import generate_keypair
from tokenprov.evidence import LocalEvidenceRepository
from tokenprov.handoff import create_handoff, countersign_handoff
from tokenprov.ledger import LocalAuthenticatedLedger
from tokenprov.lifecycle import LifecycleDAG
from tokenprov.provider import issue_receipt, register_deployment
from tokenprov.settlement import create_closure, create_settlement, evidence_root
from tokenprov.user import create_authorization


@dataclass(frozen=True)
class EndToEndResult:
    text: str
    usage: UsageVector
    timings_ns: dict[str, int]
    accepted: bool
    receipt: ProviderReceipt


class SingleCallHarness:
    """Runs U→H→B→P and keeps settlement/L/V/audit in separate clocks.

    The provider signature is produced by a local provider-side sidecar over
    usage returned by the real endpoint. Stock vLLM does not expose KV state;
    the receipt commits to that explicit unavailable status.
    """

    def __init__(self, broker: Broker, root: str | Path, *, model_reference: str,
                 model_revision: str, model_hash: str | None = None,
                 tokenizer_hash: str | None = None,
                 price_policy: PricePolicy | None = None):
        self.broker = broker
        self.keys = {name: generate_keypair() for name in ("U", "H", "B", "P1")}
        self.price = price_policy or PricePolicy("local-research-v1", n_pf_rate=1,
                                                 n_out_rate=2)
        descriptor = DeploymentDescriptor(
            model_reference, model_revision,
            model_hash or digest("model-reference/v1", {"model": model_reference,
                                                         "revision": model_revision}),
            tokenizer_hash or digest("tokenizer-reference/v1", {"model": model_reference,
                                                                 "revision": model_revision}),
            digest("canonicalization-rule/v1", "vllm-openai-chat-template"),
            digest("serving-semantics/v1", {
                "cache": "stock-vllm-api-cache-state-unavailable",
                "metering": "vllm-openai-usage",
                "pricing": self.price,
                "serving": "bf16,max_model_len=4096,prefix_caching=true",
            }))
        self.registration = register_deployment(DeploymentRegistration(
            "dep-p1-qwen3-1.7b", "P1", descriptor,
            commitment("deployment/v1", descriptor, "local-deployment-opening")),
            self.keys["P1"].private_key)
        root = Path(root)
        self.repository = LocalEvidenceRepository(root / "evidence")
        self.ledger = LocalAuthenticatedLedger(
            root / "ledger.jsonl",
            {name: self.keys[name].public_key for name in ("H", "B", "P1")})
        self.ledger.append("reg-p1", "deployment", self.registration,
                           "P1", self.keys["P1"].private_key)

    @staticmethod
    def _parse_response(raw: Any) -> tuple[str, UsageVector, dict[str, Any]]:
        if not isinstance(raw, dict) and hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        try:
            text = raw["choices"][0]["message"]["content"]
            usage = raw["usage"]
            vector = UsageVector(n_pf=int(usage["prompt_tokens"]),
                                 n_out=int(usage["completion_tokens"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("provider response lacks required content/usage") from exc
        return text, vector, raw

    def execute(self, request_id: str, messages: tuple[dict[str, Any], ...],
                *, max_bill_microunits: int = 1_000_000) -> EndToEndResult:
        timings: dict[str, int] = {}
        online_start = time.perf_counter_ns()
        input_opening = secrets.token_hex(16)
        auth = create_authorization(Authorization(
            request_id, {"purpose": "real-serving-benchmark"},
            commitment("input/v1", messages, input_opening),
            {"max_bill_microunits": max_bill_microunits},
            secrets.token_hex(16), "U"), self.keys["U"].private_key)
        auth_digest = digest("authorization/v1", auth)
        dag = LifecycleDAG(request_id, auth_digest)
        root = dag.add(LifecycleEvent(request_id, auth_digest, "e-root", None,
                       "authorization_root", (), digest("payload/v1", {}), "active", "H"),
                       self.keys["H"].private_key)
        call_id = "c-0"
        created = dag.add(LifecycleEvent(
            request_id, auth_digest, "e-create", call_id, "call_created",
            (digest("lifecycle-event/v1", root),), digest("payload/v1", {}), "active", "H"),
            self.keys["H"].private_key)
        intent = CallIntent(request_id, call_id, messages, {})
        hb_boundary = {"request_id": request_id, "call_id": call_id, "intent": intent}
        hb_opening = secrets.token_hex(16)
        hb = create_handoff(request_id, call_id, "H->B", hb_boundary, hb_opening, (),
                            "H", "B", self.keys["H"].private_key)
        hb = countersign_handoff(hb, hb_boundary, hb_opening, request_id, call_id,
                                 "H->B", self.keys["H"].public_key,
                                 self.keys["B"].private_key)
        hb_hash = digest("handoff/v1", hb)
        call = self.broker.make_call(intent, hb_hash)
        if call.configuration.price_version != self.price.version:
            raise ValueError("broker service configuration references a different price version")
        bp_boundary = {"request_id": request_id, "call_id": call_id, "provider_call": call,
                       "registration_hash": digest("deployment-registration/v1", self.registration)}
        bp_opening = secrets.token_hex(16)
        bp = create_handoff(request_id, call_id, "B->P", bp_boundary, bp_opening,
                            (hb_hash,), "B", "P1", self.keys["B"].private_key)
        bp = countersign_handoff(bp, bp_boundary, bp_opening, request_id, call_id,
                                 "B->P", self.keys["B"].public_key,
                                 self.keys["P1"].private_key)
        bp_hash = digest("handoff/v1", bp)
        raw = self.broker.forward(call)
        text, usage, raw_dict = self._parse_response(raw)
        service_hash = digest("service-configuration/v1", call.configuration)
        input_hash = digest("provider-input/v1", call.transformed_messages)
        output_hash = digest("provider-output/v1", text)
        cache_status = {"status": "UNAVAILABLE", "source": "stock-vllm-openai-api"}
        cache_pre_hash = digest("cache-pre/v1", cache_status)
        cache_post_hash = digest("cache-post/v1", cache_status)
        charge = self.price.charge(usage)
        shared = dict(
            request_id=request_id, call_id=call_id, authorization_digest=auth_digest,
            deployment_id=self.registration.deployment_id,
            service_config_hash=service_hash, hb_handoff_hash=hb_hash,
            bp_handoff_hash=bp_hash,
            transformation_hash=digest("transformation-rule/v1", call.transformation_rule),
            input_hash=input_hash, cache_pre_hash=cache_pre_hash,
            cache_post_hash=cache_post_hash, output_hash=output_hash, usage=usage,
            price_version=self.price.version, charge_microunits=charge)
        witness = {"shared": shared, "provider_model": raw_dict.get("model"),
                   "cache_evidence": cache_status}
        witness_opening = secrets.token_hex(16)
        witness_commitment = commitment("provider-witness/v1", witness, witness_opening)
        call_evidence = ProviderCallEvidence(**shared)
        receipt = issue_receipt(ProviderReceipt(
            **shared, billable=True, witness_commitment=witness_commitment,
            assurance_mode="signature", assurance_anchor=witness_commitment,
            call_nullifier=secrets.token_hex(16), provider_id="P1"),
            self.keys["P1"].private_key)
        dag.add(LifecycleEvent(
            request_id, auth_digest, "e-terminal", call_id, "call_terminal",
            (digest("lifecycle-event/v1", created),), digest("payload/v1", output_hash),
            "succeeded", "H"), self.keys["H"].private_key)
        timings["T_online"] = time.perf_counter_ns() - online_start

        settlement_start = time.perf_counter_ns()
        answer_opening = secrets.token_hex(16)
        closure = create_closure(RequestClosure(
            request_id, auth_digest, dag.root(), commitment("response/v1", text, answer_opening),
            (call_id,), (call_id,), "H"), self.keys["H"].private_key)
        handoffs = [hb, bp]
        settlement = create_settlement(Settlement(
            request_id, digest("request-closure/v1", closure),
            tuple(digest("handoff/v1", item) for item in handoffs),
            (digest("provider-receipt/v1", receipt),), (),
            evidence_root(handoffs, [receipt], ()), charge,
            secrets.token_hex(16), "B"), self.keys["B"].private_key,
            self.keys["H"].private_key)
        timings["T_settlement"] = time.perf_counter_ns() - settlement_start

        evidence_start = time.perf_counter_ns()
        for evidence_type, payload, evidence_call in (
            ("auth", auth, None), ("lifecycle", tuple(dag.events.values()), call_id),
            ("handoffs", tuple(handoffs), call_id), ("receipts", receipt, call_id),
            ("commitments", {"input_opening": input_opening,
                             "witness_opening": witness_opening,
                             "answer_opening": answer_opening}, call_id),
            ("settlement", settlement, None)):
            self.repository.put(request_id, evidence_type, payload, call_id=evidence_call,
                                deployment_id=self.registration.deployment_id)
        timings["T_evidence"] = time.perf_counter_ns() - evidence_start

        ledger_start = time.perf_counter_ns()
        for suffix, record_type, payload, member in (
            ("auth", "authorization", digest("authorization/v1", auth), "H"),
            ("close", "closure", digest("request-closure/v1", closure), "H"),
            ("settle", "settlement", digest("settlement/v1", settlement), "B")):
            record_id = f"{request_id}-{suffix}"
            self.ledger.append(record_id, record_type, payload, member,
                               self.keys[member].private_key)
            if not self.ledger.wait_finality(record_id):
                raise RuntimeError("local ledger failed finality")
        timings["T_ledger"] = time.perf_counter_ns() - ledger_start

        public = {"keys": {name: pair.public_key for name, pair in self.keys.items()},
                  "registrations": {self.registration.deployment_id: self.registration},
                  "price_policies": {self.price.version: self.price},
                  "closure": closure, "settlement": settlement}
        private = {"lifecycle": dag,
                   "handoffs": [(hb, hb_boundary, hb_opening),
                                (bp, bp_boundary, bp_opening)],
                   "provider_calls": [(call_evidence, receipt, {"mode": "signature"})]}
        audit_start = time.perf_counter_ns()
        decision = audit(auth, public, private)
        timings["T_audit"] = time.perf_counter_ns() - audit_start
        return EndToEndResult(text, usage, timings, decision.accepted, receipt)
