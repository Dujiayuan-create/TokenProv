from dataclasses import replace
from tokenprov.common.models import Handoff
from tokenprov.common.serialization import commitment, verify_opening
from tokenprov.crypto.signing import sign_object, verify_object


def create_handoff(request_id, call_id, direction, boundary, opening, causal_references,
                   sender, receiver, sender_private_key) -> Handoff:
    if direction not in {"H->B", "B->P"}: raise ValueError("invalid handoff direction")
    h = Handoff(request_id, call_id, direction, commitment("handoff-boundary/v1", boundary, opening),
                tuple(causal_references), sender, receiver)
    return replace(h, sender_signature=sign_object(sender_private_key, "handoff/v1", h.context()))


def countersign_handoff(handoff, boundary, opening, expected_request_id, expected_call_id,
                        expected_direction, sender_public_key, receiver_private_key) -> Handoff:
    if (handoff.request_id, handoff.call_id, handoff.direction) != (expected_request_id, expected_call_id, expected_direction):
        raise ValueError("handoff context mismatch")
    if not verify_opening("handoff-boundary/v1", boundary, opening, handoff.boundary_commitment):
        raise ValueError("boundary commitment mismatch")
    if not verify_object(sender_public_key, "handoff/v1", handoff.context(), handoff.sender_signature):
        raise ValueError("invalid sender signature")
    return replace(handoff, receiver_signature=sign_object(receiver_private_key, "handoff/v1", handoff.context()))


def verify_handoff(handoff, boundary, opening, sender_public_key, receiver_public_key) -> bool:
    return (handoff.direction in {"H->B", "B->P"}
            and verify_opening("handoff-boundary/v1", boundary, opening, handoff.boundary_commitment)
            and verify_object(sender_public_key, "handoff/v1", handoff.context(), handoff.sender_signature)
            and verify_object(receiver_public_key, "handoff/v1", handoff.context(), handoff.receiver_signature))
