"""Attested invocation verification with relying-party session/key/state pinning."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from tokenprov.assurance.hardware import AttestationError, NvidiaAttestor, command_json, verify_tdx_quote
from tokenprov.common.serialization import canonical_bytes, commitment, digest, primitive
from tokenprov.crypto.signing import verify_object


def receipt_claims(receipt):
    value = receipt.signed_payload() if hasattr(receipt, "signed_payload") else dict(receipt)
    return {k: v for k, v in value.items() if k not in {"signature", "assurance_anchor"}}


def session_binding(session):
    return {k: session[k] for k in ("version", "session_id", "challenge", "runtime_public_key",
        "deployment_digest", "program_hash", "issued_at", "expires_at")}


def gpu_nonce(session):
    return digest("tee-gpu-session/v1", session_binding(session))


def cpu_report_data(session):
    bound = {**session_binding(session), "gpu_token_hash": digest("tee-gpu-token/v1", session["gpu_token"]),
        "workload_evidence_hash": digest("tee-workload-evidence/v1", session.get("workload_evidence"))}
    return hashlib.sha512(b"TOKENPROV-TEE-SESSION\x00" + canonical_bytes(bound)).hexdigest()


def initial_state(session_id, authorization_digest):
    return digest("tee-initial-state/v1", {"session_id": session_id, "authorization_digest": authorization_digest})


@dataclass(frozen=True)
class TeePolicy:
    allowed_measurements: tuple[str, ...]
    allowed_program_hashes: tuple[str, ...]
    platform: str = "tdx"
    cpu_verifier_command: tuple[str, ...] = ()
    nvattest_command: tuple[str, ...] = ("nvattest",)
    nvidia_policy_file: str = ""
    nras_url: str = "https://nras.attestation.nvidia.com"
    max_session_s: int = 3600
    timeout_s: int = 120
    workload_verifier_command: tuple[str, ...] = ()
    workload_policy_file: str = ""
    challenge_ttl_s: int = 120

    @classmethod
    def load(cls, path):
        source = Path(path).resolve()
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("unknown or invalid TEE policy fields")
        for name in ("allowed_measurements", "allowed_program_hashes"):
            if not isinstance(raw.get(name), list) or not raw[name] or any(not isinstance(x, str) or len(x) != 64 or any(c not in "0123456789abcdef" for c in x) for x in raw[name]):
                raise ValueError(f"TEE {name} must contain approved SHA256 digests")
            raw[name] = tuple(raw[name])
        for name in ("cpu_verifier_command", "nvattest_command", "workload_verifier_command"):
            if name in raw:
                if not isinstance(raw[name], list) or any(not isinstance(x, str) or not x for x in raw[name]):
                    raise ValueError(f"TEE {name} must be an argv list")
                raw[name] = tuple(raw[name])
        policy = Path(raw.get("nvidia_policy_file", ""))
        if not policy.is_absolute(): policy = source.parent / policy
        raw["nvidia_policy_file"] = str(policy.resolve())
        if not raw.get("workload_verifier_command") or not raw.get("workload_policy_file"):
            raise ValueError("full TEE requires a trusted local workload verifier and approved launch/device policy")
        policy = Path(raw["workload_policy_file"])
        raw["workload_policy_file"] = str((policy if policy.is_absolute() else source.parent / policy).resolve())
        if raw.get("platform", "tdx") != "tdx" and not raw.get("cpu_verifier_command"):
            raise ValueError("non-TDX platforms require an actual CPU quote verifier command")
        for name in ("max_session_s", "timeout_s", "challenge_ttl_s"):
            if name in raw and (type(raw[name]) is not int or raw[name] < 1):
                raise ValueError(f"{name} must be a positive integer")
        return cls(**raw)


class TeeVerifier:
    def __init__(self, policy: TeePolicy):
        self.policy = policy
        self.sessions = {}
        self.pending = {}
        self.heads = {}
        self.verified = {}
        self.lock = threading.RLock()
        self.gpu = NvidiaAttestor(policy.nvattest_command, policy.nras_url,
                                  policy.nvidia_policy_file, policy.timeout_s)

    def challenge(self):
        challenge = secrets.token_hex(32)
        with self.lock:
            now = time.monotonic()
            self.pending = {k: t for k, t in self.pending.items() if now - t <= self.policy.challenge_ttl_s}
            self.pending[challenge] = now
        return challenge

    def enroll(self, session, expected_challenge, deployment_digest):
        with self.lock:
            now = int(time.time())
            if expected_challenge not in self.pending or session.get("challenge") != expected_challenge:
                raise AttestationError("unsolicited or replayed attestation challenge")
            if time.monotonic() - self.pending[expected_challenge] > self.policy.challenge_ttl_s:
                raise AttestationError("attestation challenge expired on relying-party clock")
            if session.get("version") != 1 or session.get("deployment_digest") != deployment_digest:
                raise AttestationError("attested deployment mismatch")
            if session.get("program_hash") not in self.policy.allowed_program_hashes:
                raise AttestationError("unapproved runtime program")
            issued, expires = session.get("issued_at"), session.get("expires_at")
            if (type(issued) is not int or type(expires) is not int or issued > now + 30
                or now >= expires or not 0 < expires - issued <= self.policy.max_session_s):
                raise AttestationError("invalid attestation session lifetime")
            report_data = cpu_report_data(session)
            if self.policy.cpu_verifier_command:
                cpu = command_json(self.policy.cpu_verifier_command,
                    {"schema_version": 1, "evidence": session["cpu_evidence"], "expected_report_data": report_data},
                    self.policy.timeout_s)
            else:
                cpu = verify_tdx_quote(session["cpu_evidence"], report_data)
            if (cpu.get("verified") is not True or cpu.get("platform") != self.policy.platform
                or cpu.get("measurement") not in self.policy.allowed_measurements
                or cpu.get("report_data") != report_data or cpu.get("debug") is not False
                or cpu.get("tcb_status") != "UpToDate"):
                raise AttestationError("CPU attestation or measurement policy rejected")
            gpu_claims = self.gpu.verify(session["gpu_token"], gpu_nonce(session))
            # A raw CVM quote accepts caller-selected REPORT_DATA; it does not
            # authenticate a Python process. Only a protected measured launcher
            # or equivalent deployment service can establish this last binding.
            workload = self._verify_workload(session, cpu, gpu_claims)
            session_id = session["session_id"]
            if session_id in self.sessions:
                raise AttestationError("attestation session id already enrolled")
            self.sessions[session_id] = {"session": session, "cpu": cpu, "gpu": gpu_claims, "workload": workload,
                "deadline_monotonic": time.monotonic() + min(expires - now, self.policy.max_session_s),
                "digest": digest("tee-session/v1", session)}
            del self.pending[expected_challenge]
            return self.sessions[session_id]

    def _verify_workload(self, session, cpu, gpu_claims):
        if not self.policy.workload_verifier_command or not self.policy.workload_policy_file:
            raise AttestationError("CPU_IMAGE_NOT_PROVISIONED: a hardware quote alone does not verify runtime identity")
        policy_bytes = Path(self.policy.workload_policy_file).read_bytes()
        expected = {"session_digest": digest("tee-session/v1", session),
            "runtime_public_key": session["runtime_public_key"], "program_hash": session["program_hash"],
            "deployment_digest": session["deployment_digest"], "policy_sha256": hashlib.sha256(policy_bytes).hexdigest()}
        binding = digest("tee-workload-verification/v1", expected)
        result = command_json(self.policy.workload_verifier_command, {"schema_version": 1, "binding": binding,
            "expected": expected, "session": session, "verified_cpu": cpu, "verified_gpu": gpu_claims,
            "policy_file": self.policy.workload_policy_file}, self.policy.timeout_s)
        checks = ("workload_identity_verified", "immutable_artifacts_verified",
            "gpu_device_binding_verified", "secure_cpu_gpu_path_verified")
        if (result.get("verified") is not True or result.get("binding") != binding
            or result.get("expected") != expected or any(result.get(k) is not True for k in checks)):
            raise AttestationError("measured workload, immutable model or CPU/GPU device binding rejected")
        return result

    def verify(self, call, receipt, artifact, *, deployment_descriptor=None):
        try:
            with self.lock:
                return self._verify(call, receipt, artifact, deployment_descriptor)
        except (KeyError, TypeError, ValueError, AttributeError, AttestationError):
            return False

    def _verify(self, call, receipt, artifact, deployment_descriptor):
        if not isinstance(artifact, dict) or artifact.get("version") != 1 or receipt.assurance_mode != "tee": return False
        if receipt.assurance_anchor != digest("tee-artifact/v1", artifact): return False
        enrolled = self.sessions[artifact["session_id"]]
        if time.monotonic() >= enrolled["deadline_monotonic"]: return False
        session, transition = enrolled["session"], artifact["transition"]
        if deployment_descriptor is None or session["deployment_digest"] != digest("deployment-descriptor/v1", deployment_descriptor): return False
        if artifact["session_digest"] != enrolled["digest"]: return False
        expected_claim_hash = digest("tee-receipt-claims/v1", receipt_claims(receipt))
        if transition.get("claim_hash") != expected_claim_hash: return False
        if transition.get("session_id") != session["session_id"]: return False
        if (transition.get("request_id"), transition.get("call_id")) != (call.request_id, call.call_id): return False
        if not session["issued_at"] <= transition["created_at"] < session["expires_at"]: return False
        if not verify_object(session["runtime_public_key"], "tee-transition/v1", transition, artifact["signature"]): return False
        fingerprint = digest("tee-artifact/v1", artifact)
        key = (session["session_id"], call.authorization_digest, call.call_id)
        if key in self.verified:
            return self.verified[key] == fingerprint
        stream = (session["session_id"], call.authorization_digest)
        sequence, previous = self.heads.get(stream, (0, initial_state(*stream)))
        if transition.get("sequence") != sequence + 1 or transition.get("previous") != previous: return False
        next_state = digest("tee-state/v1", {"previous": previous, "claim_hash": expected_claim_hash, "sequence": sequence + 1})
        if transition.get("next") != next_state: return False
        self.heads[stream] = (sequence + 1, next_state)
        self.verified[key] = fingerprint
        return True


class TeeClient:
    """Client for the measured runtime; never accepts a service's 'verified' flag."""
    def __init__(self, url, policy, descriptor, *, api_key=None, timeout_s=180, session_observer=None):
        from tokenprov.server_eval.transport import JsonHttpClient
        self.http = JsonHttpClient(url, api_key, timeout_s)
        self.verifier = TeeVerifier(policy)
        self.descriptor = descriptor
        self.deployment_digest = digest("deployment-descriptor/v1", descriptor)
        challenge = self.verifier.challenge()
        session = self.http.request("/session", {"challenge": challenge, "deployment_digest": self.deployment_digest})
        self.verifier.enroll(session, challenge, self.deployment_digest)
        self.session = session
        if session_observer: session_observer(session)

    def execute(self, call, context):
        from tokenprov.common.models import ProviderCallEvidence, ProviderReceipt, UsageVector
        from tokenprov.server_eval.transport import parse_completion
        if int(time.time()) >= self.session["expires_at"]:
            raise AttestationError("TEE session expired; establish a fresh attested session")
        value = self.http.request("/execute", {"session_id": self.session["session_id"],
            "call": primitive(call), "context": primitive(context)})
        text, prompt, output = parse_completion(value["response"])
        shared = value["shared"]
        expected = {"request_id": call.request_id, "call_id": call.call_id,
            "authorization_digest": context["authorization_digest"], "deployment_id": context["deployment_id"],
            "service_config_hash": digest("service-configuration/v1", call.configuration),
            "hb_handoff_hash": context["hb_handoff_hash"], "bp_handoff_hash": context["bp_handoff_hash"],
            "transformation_hash": digest("transformation-rule/v1", call.transformation_rule),
            "input_hash": digest("provider-input/v1", call.transformed_messages),
            "output_hash": digest("provider-output/v1", text),
            "usage": {"n_pf": prompt, "n_cr": 0, "n_cw": 0, "n_rea": 0, "n_out": output},
            "price_version": context["price_policy"].version,
            "charge_microunits": context["price_policy"].charge(UsageVector(n_pf=prompt, n_out=output))}
        if any(shared.get(k) != primitive(v) for k, v in expected.items()):
            raise AttestationError("TEE result does not match the requested invocation")
        evidence = ProviderCallEvidence(**{**shared, "usage": UsageVector(**shared["usage"])})
        receipt = ProviderReceipt(**evidence.__dict__, billable=True,
            witness_commitment=commitment("provider-witness/v1", value["witness"], value["opening"]),
            assurance_mode="tee", assurance_anchor=digest("tee-artifact/v1", value["artifact"]),
            call_nullifier=value["call_nullifier"], provider_id=call.configuration.provider_id)
        if not self.verifier.verify(evidence, receipt, value["artifact"], deployment_descriptor=self.descriptor):
            raise AttestationError("TEE transition verification rejected")
        return value
