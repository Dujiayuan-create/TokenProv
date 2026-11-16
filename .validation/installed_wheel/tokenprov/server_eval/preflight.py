"""Read-only hardware, endpoint and attestation-tool diagnostics."""
from __future__ import annotations

import ctypes.util
import importlib.util
import os
import platform
import shutil
import subprocess
from pathlib import Path

from tokenprov.server_eval.transport import JsonHttpClient


def hardware_report():
    result = {"platform": platform.platform(), "cpu": platform.processor(), "cpu_count": os.cpu_count(),
              "tdx_guest_device": any(Path(x).exists() for x in ("/dev/tdx_guest", "/dev/tdx-guest")),
              "snp_guest_device": Path("/dev/sev-guest").exists(),
              "tdx_quote_library": bool(ctypes.util.find_library("tdx_attest")),
              "tdx_verification_library": bool(ctypes.util.find_library("sgx_dcap_quoteverify")),
              "nvattest": shutil.which("nvattest"), "gpu_confidential_mode": "NOT_ATTESTED"}
    if shutil.which("nvidia-smi"):
        try:
            process = subprocess.run(["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total",
                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15)
            result["gpus"] = process.stdout.strip().splitlines() if process.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired): result["gpus"] = []
    return result


def preflight(config, *, check_network=True):
    errors, warnings, endpoints = [], [], []
    wants_tee = "TokenProv-TEE" in config.systems
    for endpoint in config.endpoints:
        entry = {"id": endpoint.id, "model": endpoint.model}
        try:
            url, key = endpoint.url(), endpoint.api_key()
            if not config.workload.trace_file:
                if not endpoint.tokenizer_path or not Path(endpoint.tokenizer_path).is_dir():
                    raise ValueError(f"{endpoint.id}: supply a local tokenizer_path for calibrated synthetic input")
                if importlib.util.find_spec("transformers") is None:
                    raise ValueError("install tokenprov[server-client] for local token calibration")
            if check_network:
                models = JsonHttpClient(url, key, min(endpoint.timeout_s, 10)).request("/v1/models")
                entry["listed_models"] = [x.get("id") for x in models.get("data", [])]
                if endpoint.model not in entry["listed_models"]:
                    raise ValueError(f"{endpoint.id}: configured model is absent from /v1/models")
            if endpoint.model_hash == "unregistered" or endpoint.tokenizer_hash == "unregistered":
                warnings.append(f"{endpoint.id}: remote model identity is not verified; signature-only measurements")
            if wants_tee:
                endpoint.api_key(tee=True)
                from tokenprov.assurance.tee import TeePolicy
                if not endpoint.tee_url or not endpoint.tee_policy:
                    raise ValueError(f"{endpoint.id}: TokenProv-TEE requires tee_url and tee_policy")
                if endpoint.model_hash == "unregistered" or endpoint.tokenizer_hash == "unregistered":
                    raise ValueError(f"{endpoint.id}: TEE requires approved content hashes")
                policy = TeePolicy.load(endpoint.tee_policy)
                if not Path(policy.nvidia_policy_file).is_file(): raise ValueError("NVIDIA Rego policy is missing")
                if shutil.which(policy.nvattest_command[0]) is None:
                    raise ValueError("nvattest CLI is missing on the relying party")
                if not policy.cpu_verifier_command and not ctypes.util.find_library("sgx_dcap_quoteverify"):
                    raise ValueError("install Intel DCAP QVL or configure an actual CPU verifier")
                if policy.cpu_verifier_command and shutil.which(policy.cpu_verifier_command[0]) is None:
                    raise ValueError("CPU verifier command is missing")
                if not Path(policy.workload_policy_file).is_file(): raise ValueError("approved workload launch/device policy is missing")
                if shutil.which(policy.workload_verifier_command[0]) is None:
                    raise ValueError("trusted local measured-workload verifier is missing")
                entry["tee"] = "configured; cryptographic enrollment happens at run start"
            entry["ok"] = True
        except Exception as exc:
            # Configuration errors are safe to explain; transport errors omit URLs/headers.
            entry.update({"ok": False, "error": str(exc)})
            errors.append(entry["error"])
        endpoints.append(entry)
    for command in (config.audit.prover_command, config.audit.verifier_command):
        if command and shutil.which(command[0]) is None: errors.append(f"proof executable unavailable: {command[0]}")
    if not config.audit.prover_command:
        warnings.append("No proof backend configured: request sampling is logged, no ZK proving costs are claimed")
    if config.workload.trace_file and not Path(config.workload.trace_file).is_file():
        errors.append("trace_file does not exist")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "endpoints": endpoints, "hardware": hardware_report()}
