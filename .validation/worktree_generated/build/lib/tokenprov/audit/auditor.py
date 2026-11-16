from __future__ import annotations
import time
from tokenprov.common.models import AuditResult
from tokenprov.common.serialization import digest
from tokenprov.handoff.bilateral import verify_handoff
from tokenprov.provider.receipts import verify_provider
from tokenprov.settlement.core import verify_settlement
from tokenprov.user.authorization import verify_authorization


def audit(auth, public_transcript: dict, private_witness: dict, *, tee_verifier=None,
          required_assurance: str | None = None) -> AuditResult:
    """Verify R_auth, R_life, R_hand, R_prov, R_set in protocol order."""
    timings: dict[str, int] = {}
    def check(name, fn, interval):
        start = time.perf_counter_ns()
        try: ok = bool(fn())
        except (KeyError, TypeError, ValueError, AttributeError, IndexError): ok = False
        timings[name] = time.perf_counter_ns() - start
        return None if ok else AuditResult(False, name, interval, timings.copy())

    result = check("R_auth", lambda: verify_authorization(auth, public_transcript["keys"][auth.user_id]), ("U", "H"))
    if result: return result
    dag = private_witness["lifecycle"]
    auth_digest = digest("authorization/v1", auth)
    closure = public_transcript["closure"]
    result = check("R_life", lambda: (
        dag.request_id == auth.request_id
        and dag.authorization_digest == auth_digest
        and dag.validate(public_transcript["keys"])
        and closure.request_id == auth.request_id
        and closure.authorization_digest == auth_digest
        and closure.lifecycle_root == dag.root()
        and set(closure.terminal_call_ids) == dag.call_ids
    ), ("H", "H"))
    if result: return result
    def handoffs_ok():
        by_call = {}
        for h, boundary, opening in private_witness["handoffs"]:
            if not verify_handoff(h, boundary, opening, public_transcript["keys"][h.sender], public_transcript["keys"][h.receiver]): return False
            if boundary.get("request_id") != h.request_id or boundary.get("call_id") != h.call_id: return False
            if h.request_id != auth.request_id or h.direction in by_call.setdefault(h.call_id, {}): return False
            if h.direction == "H->B" and (h.sender, h.receiver) != ("H", "B"): return False
            if h.direction == "B->P" and h.sender != "B": return False
            by_call.setdefault(h.call_id, {})[h.direction] = h
        observed_acceptances = {cid for cid, pair in by_call.items() if "B->P" in pair}
        if observed_acceptances != set(closure.provider_accepted_call_ids): return False
        if not set(by_call).issubset(dag.call_ids): return False
        for call_id in closure.provider_accepted_call_ids:
            pair = by_call.get(call_id, {})
            if set(pair) != {"H->B", "B->P"}: return False
            if digest("handoff/v1", pair["H->B"]) not in pair["B->P"].causal_references: return False
        return True
    result = check("R_hand", handoffs_ok, ("H", "P"))
    if result: return result
    def providers_ok():
        seen = set()
        indexed = {(h.call_id, h.direction): (h, boundary)
                   for h, boundary, _ in private_witness["handoffs"]}
        for call, receipt, artifact in private_witness["provider_calls"]:
            if required_assurance and receipt.assurance_mode != required_assurance: return False
            if receipt.request_id != auth.request_id or call.request_id != auth.request_id: return False
            if receipt.call_nullifier in seen: return False
            seen.add(receipt.call_nullifier)
            matching = {direction: indexed[(call.call_id, direction)][0] for direction in ("H->B", "B->P")}
            if call.authorization_digest != auth_digest: return False
            if call.hb_handoff_hash != digest("handoff/v1", matching["H->B"]): return False
            if call.bp_handoff_hash != digest("handoff/v1", matching["B->P"]): return False
            if matching["B->P"].receiver != receipt.provider_id: return False
            reg = public_transcript["registrations"].get(call.deployment_id)
            if reg is None or reg.provider_id != receipt.provider_id: return False
            providers = auth.policy.get("allowed_provider_ids")
            if providers is not None and receipt.provider_id not in providers: return False
            models = auth.policy.get("allowed_model_hashes")
            if models is not None and reg.descriptor.model_hash not in models: return False
            boundary = indexed[(call.call_id, "B->P")][1]
            if "deployment_id" in boundary and boundary["deployment_id"] != call.deployment_id: return False
            if "provider_call" in boundary:
                request = boundary["provider_call"]
                if request.configuration.provider_id != receipt.provider_id: return False
                if request.configuration.model_id != reg.descriptor.model_id: return False
                if digest("service-configuration/v1", request.configuration) != call.service_config_hash: return False
                if digest("provider-input/v1", request.transformed_messages) != call.input_hash: return False
                if digest("transformation-rule/v1", request.transformation_rule) != call.transformation_hash: return False
                if boundary.get("registration_hash") != digest("deployment-registration/v1", reg): return False
            if not verify_provider(call, receipt, artifact,
                                   public_transcript["keys"][receipt.provider_id],
                                   public_transcript["registrations"],
                                   public_transcript["price_policies"], tee_verifier=tee_verifier): return False
        return True
    result = check("R_prov", providers_ok, ("B", "P"))
    if result: return result
    def settlement_ok():
        settlement = public_transcript["settlement"]
        maximum = auth.policy.get("max_bill_microunits")
        if type(maximum) is not int or maximum < 0 or settlement.bill_microunits > maximum:
            return False
        if "max_calls" in auth.policy and (type(auth.policy["max_calls"]) is not int
            or len(dag.call_ids) > auth.policy["max_calls"]): return False
        return verify_settlement(settlement, closure,
                   [x[0] for x in private_witness["handoffs"]],
                   [x[1] for x in private_witness["provider_calls"]],
                   public_transcript["keys"][settlement.broker_id],
                   public_transcript["keys"][closure.harness_id])
    result = check("R_set", settlement_ok, ("H", "B"))
    if result: return result
    return AuditResult(True, None, None, timings)
