from dataclasses import replace
from tokenprov.common.models import DeploymentRegistration, PricePolicy, ProviderCallEvidence, ProviderReceipt
from tokenprov.crypto.signing import sign_object, verify_object


def register_deployment(reg: DeploymentRegistration, private_key: str) -> DeploymentRegistration:
    if not reg.deployment_id or not reg.provider_id or not reg.deployment_commitment:
        raise ValueError("deployment registration is incomplete")
    return replace(reg, signature=sign_object(private_key, "deployment-registration/v1", reg.signed_payload()))


def issue_receipt(receipt: ProviderReceipt, private_key: str) -> ProviderReceipt:
    if receipt.charge_microunits < 0:
        raise ValueError("receipt charge must be non-negative")
    if not receipt.billable and receipt.charge_microunits != 0:
        raise ValueError("non-billable receipt must have zero charge")
    if not receipt.call_nullifier or not receipt.witness_commitment:
        raise ValueError("receipt must bind a call nullifier and witness commitment")
    if receipt.assurance_mode == "zk-deferred" and receipt.assurance_anchor != receipt.witness_commitment:
        raise ValueError("deferred assurance must anchor the fixed witness commitment")
    return replace(receipt, signature=sign_object(private_key, "provider-receipt/v1", receipt.signed_payload()))


def verify_receipt(receipt: ProviderReceipt, provider_public_key: str, registrations: dict[str, DeploymentRegistration]) -> bool:
    reg = registrations.get(receipt.deployment_id)
    return bool(reg and reg.provider_id == receipt.provider_id
                and receipt.charge_microunits >= 0
                and verify_object(provider_public_key, "deployment-registration/v1", reg.signed_payload(), reg.signature)
                and verify_object(provider_public_key, "provider-receipt/v1", receipt.signed_payload(), receipt.signature)
                and bool(receipt.call_nullifier and receipt.witness_commitment)
                and (receipt.billable or receipt.charge_microunits == 0)
                and (receipt.assurance_mode != "zk-deferred"
                     or receipt.assurance_anchor == receipt.witness_commitment))


def verify_provider(call: ProviderCallEvidence, receipt: ProviderReceipt,
                    assurance_artifact: dict | None, provider_public_key: str,
                    registrations: dict[str, DeploymentRegistration],
                    price_policies: dict[str, PricePolicy], *, tee_verifier=None) -> bool:
    """Check accounting/linkage; TEE additionally requires a caller-pinned verifier.

    Receipt signatures alone do not establish inference execution correctness.
    """
    policy = price_policies.get(receipt.price_version)
    if not policy: return False
    expected_charge = policy.charge(receipt.usage) if receipt.billable else 0
    shared_fields = (
        "request_id", "call_id", "authorization_digest", "deployment_id",
        "service_config_hash", "hb_handoff_hash", "bp_handoff_hash",
        "transformation_hash", "input_hash", "cache_pre_hash", "cache_post_hash",
        "output_hash", "usage", "price_version", "charge_microunits",
    )
    if (not all(getattr(call, field) == getattr(receipt, field) for field in shared_fields)
        or receipt.charge_microunits != expected_charge
        or not verify_receipt(receipt, provider_public_key, registrations)):
        return False
    # Authenticate all common fields before mutating a TEE verifier's head.
    if receipt.assurance_mode == "signature":
        if assurance_artifact not in (None, {"mode": "signature"}): return False
    elif receipt.assurance_mode == "tee":
        reg = registrations.get(receipt.deployment_id)
        if reg is None or tee_verifier is None or not tee_verifier.verify(call, receipt, assurance_artifact,
                deployment_descriptor=reg.descriptor):
            return False
    else:
        # Commitment fixation is tested in Phase 1, but no hash opening is
        # mislabeled as ZK/TEE assurance.
        return False
    return True
