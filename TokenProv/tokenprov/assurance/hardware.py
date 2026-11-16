"""Real Intel TDX DCAP and NVIDIA NVAT adapters, imported lazily.

No hardware availability is inferred from a GPU model name. Quote verification
executes on the relying party; provider-supplied success flags are not trusted.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import json
import struct
import subprocess
import tempfile
import time
from pathlib import Path


class AttestationError(RuntimeError):
    pass


def command_json(argv, payload=None, timeout_s=120):
    if not argv or any(not isinstance(x, str) or not x for x in argv):
        raise AttestationError("attestation command must be a nonempty argv list")
    try:
        result = subprocess.run(list(argv), input=None if payload is None else json.dumps(payload),
            capture_output=True, text=True, timeout=timeout_s, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AttestationError(f"attestation command failed ({type(exc).__name__})") from None
    if result.returncode != 0:
        raise AttestationError(f"attestation command exited {result.returncode}")
    if len(result.stdout) > 8 * 1024 * 1024:
        raise AttestationError("attestation output too large")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise AttestationError("attestation command did not return JSON") from None
    if not isinstance(value, dict):
        raise AttestationError("attestation command must return an object")
    return value


def _library(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise AttestationError(f"required native library not installed: {name}")
    try:
        return ctypes.CDLL(path)
    except OSError:
        raise AttestationError(f"cannot load native library: {name}") from None


def collect_tdx_quote(report_data_hex):
    data = bytes.fromhex(report_data_hex)
    if len(data) != 64:
        raise AttestationError("TDX REPORT_DATA must be exactly 64 bytes")
    lib = _library("tdx_attest")
    byte_ptr = ctypes.POINTER(ctypes.c_uint8)
    fn = lib.tdx_att_get_quote
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                   ctypes.POINTER(byte_ptr), ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    fn.restype = ctypes.c_uint32
    lib.tdx_att_free_quote.argtypes = [byte_ptr]
    lib.tdx_att_free_quote.restype = ctypes.c_uint32
    quote, size = byte_ptr(), ctypes.c_uint32()
    report = (ctypes.c_uint8 * 64).from_buffer_copy(data)
    status = fn(report, None, 0, None, ctypes.byref(quote), ctypes.byref(size), 0)
    if status != 0:
        raise AttestationError(f"TDX quote generation failed: {status:#x}")
    try:
        if not quote or not 632 <= size.value <= 1024 * 1024:
            raise AttestationError("invalid TDX quote size")
        return {"platform": "tdx", "quote": base64.b64encode(ctypes.string_at(quote, size.value)).decode()}
    finally:
        if quote:
            lib.tdx_att_free_quote(quote)


def parse_tdx_v4(quote: bytes):
    """Parse the authenticated v4 header and 584-byte TD report body only.

    Unknown quote formats are rejected, never parsed with guessed offsets.
    """
    if len(quote) < 636 or struct.unpack_from("<H", quote)[0] != 4:
        raise AttestationError("only TDX quote v4 is supported by the builtin parser; configure an external verifier for other versions")
    if struct.unpack_from("<I", quote, 4)[0] != 0x81:
        raise AttestationError("quote is not an Intel TDX quote")
    body = quote[48:632]
    return {"platform": "tdx", "measurement": hashlib.sha256(body[:520]).hexdigest(),
            "report_data": body[520:584].hex(),
            "debug": bool(struct.unpack_from("<Q", body, 120)[0] & 1),
            "mr_td": body[136:184].hex(), "rtmrs": [body[328 + i*48:376 + i*48].hex() for i in range(4)]}


def verify_tdx_quote(evidence, report_data_hex):
    try:
        quote = base64.b64decode(evidence["quote"], validate=True)
    except (KeyError, ValueError, TypeError):
        raise AttestationError("invalid base64 TDX quote") from None
    claims = parse_tdx_v4(quote)
    lib = _library("sgx_dcap_quoteverify")
    fn = lib.tdx_qv_verify_quote
    fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    fn.restype = ctypes.c_uint32
    raw = (ctypes.c_uint8 * len(quote)).from_buffer_copy(quote)
    collateral_expired, verification = ctypes.c_uint32(1), ctypes.c_uint32(0xffff)
    status = fn(raw, len(quote), None, int(time.time()), ctypes.byref(collateral_expired),
                ctypes.byref(verification), None, 0, None)
    # Accept SGX_QL_QV_RESULT_OK only; do not silently accept out-of-date TCB.
    if status or collateral_expired.value or verification.value != 0:
        raise AttestationError(f"TDX verification rejected: api={status:#x}, qv={verification.value:#x}, expired={collateral_expired.value}")
    if claims["report_data"] != report_data_hex or claims["debug"]:
        raise AttestationError("TDX report data mismatch or debug enabled")
    return {**claims, "verified": True, "tcb_status": "UpToDate"}


class NvidiaAttestor:
    """C++ nvattest CLI, using live NVML evidence and authenticated NRAS tokens."""
    def __init__(self, command=("nvattest",), nras_url="https://nras.attestation.nvidia.com",
                 policy_file=None, timeout_s=120):
        self.command, self.nras_url = tuple(command), nras_url
        self.policy_file, self.timeout_s = policy_file, timeout_s
        if not self.nras_url.startswith("https://"):
            raise AttestationError("NRAS verification URL must use HTTPS")

    def collect(self, nonce):
        result = command_json((*self.command, "attest", "--device", "gpu", "--verifier", "remote",
            "--gpu-evidence-source", "nvml", "--nonce", nonce, "--nras-url", self.nras_url,
            "--format", "json"), timeout_s=self.timeout_s)
        if result.get("result_code") != 0 or not result.get("detached_eat"):
            raise AttestationError("NVIDIA attestation did not produce an authenticated detached EAT")
        return result["detached_eat"]

    def verify(self, token, nonce):
        if not self.policy_file or not Path(self.policy_file).is_file():
            raise AttestationError("an explicit NVIDIA relying-party policy is required")
        with tempfile.TemporaryDirectory(prefix="tokenprov-nvat-") as directory:
            path = Path(directory) / "eat.json"
            path.write_text(json.dumps(token), encoding="utf-8")
            result = command_json((*self.command, "verify-token", "--token-file", str(path),
                "--nonce", nonce, "--nras-url", self.nras_url,
                "--relying-party-policy", str(self.policy_file), "--format", "json"),
                timeout_s=self.timeout_s)
        claims = result.get("claims")
        if result.get("result_code") != 0 or not isinstance(claims, list) or not claims:
            raise AttestationError("NVIDIA token verification failed")
        for claim in claims:
            if (claim.get("x-nvidia-device-type") != "gpu" or claim.get("measres") != "success"
                or claim.get("secboot") is not True or claim.get("dbgstat") != "disabled"
                or claim.get("eat_nonce") != nonce):
                raise AttestationError("NVIDIA token does not satisfy nonce/security policy")
        return claims
