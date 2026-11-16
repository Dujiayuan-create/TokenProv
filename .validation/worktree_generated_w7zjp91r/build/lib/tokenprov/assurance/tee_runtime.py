"""Measured local-inference runtime for a CPU CVM plus confidential NVIDIA GPU.

Run inside an approved immutable TDX/SNP guest, not an ordinary container.
The signing key is generated in this process and has no export API. A session
quote is only issued for this key and a deployment whose local files were hashed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from tokenprov.assurance.hardware import AttestationError, NvidiaAttestor, collect_tdx_quote, command_json
from tokenprov.assurance.tee import cpu_report_data, gpu_nonce, initial_state, receipt_claims
from tokenprov.common.models import PricePolicy, UsageVector
from tokenprov.common.serialization import canonical_bytes, commitment, digest, primitive
from tokenprov.crypto import generate_keypair, sign_object
from tokenprov.server_eval.model_artifacts import make_descriptor, model_manifest


def program_hash():
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        name = path.relative_to(root).as_posix().encode()
        h.update(len(name).to_bytes(4, "big") + name + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


class LocalInference:
    """One local GPU, registered tokenizer, no remote-code or network inference."""
    def __init__(self, config):
        from transformers import AutoTokenizer
        self.config = config
        self.tokenizer = AutoTokenizer.from_pretrained(config["model_path"], local_files_only=True, trust_remote_code=False)
        self.lock = threading.Lock()
        self.backend = config.get("backend", "vllm")
        if self.backend == "vllm":
            from vllm import LLM
            self.model = LLM(model=config["model_path"], tokenizer=config["model_path"],
                trust_remote_code=False, load_format="safetensors", dtype="bfloat16", tensor_parallel_size=1,
                max_model_len=config.get("max_model_len", 4096),
                enable_prefix_caching=False, gpu_memory_utilization=config.get("gpu_memory_percent", 80) / 100)
        elif self.backend == "transformers":
            import torch
            from transformers import AutoModelForCausalLM
            if not torch.cuda.is_available(): raise AttestationError("TEE inference requires a CUDA GPU")
            self.model = AutoModelForCausalLM.from_pretrained(config["model_path"], local_files_only=True,
                trust_remote_code=False, use_safetensors=True, torch_dtype=torch.bfloat16).to("cuda:0").eval()
        else:
            raise ValueError("TEE backend must be vllm or transformers")

    def infer(self, payload):
        if payload.get("model") != self.config["model"]:
            raise ValueError("unregistered model")
        maximum = payload.get("max_tokens", 256)
        if type(maximum) is not int or not 1 <= maximum <= self.config.get("max_output_tokens", 2048):
            raise ValueError("invalid max_tokens")
        if payload.get("temperature", 0) != 0:
            raise ValueError("registered TEE profile currently requires temperature=0")
        template = payload.get("chat_template_kwargs", {"enable_thinking": False})
        if template != {"enable_thinking": False}:
            raise ValueError("registered TEE profile disables hidden reasoning")
        ids = self.tokenizer.apply_chat_template(payload["messages"], tokenize=True,
            add_generation_prompt=True, enable_thinking=False)
        if len(ids) + maximum > self.config.get("max_model_len", 4096):
            raise ValueError("input plus output limit exceeds registered context length")
        with self.lock:
            if self.backend == "vllm":
                from vllm import SamplingParams
                result = self.model.generate([{"prompt_token_ids": ids}],
                    SamplingParams(temperature=0, max_tokens=maximum), use_tqdm=False)[0]
                generated = list(result.outputs[0].token_ids)
                text = result.outputs[0].text
            else:
                import torch
                inputs = torch.tensor([ids], device="cuda:0")
                with torch.inference_mode():
                    result = self.model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                        max_new_tokens=maximum, do_sample=False, use_cache=True,
                        pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
                generated = result[0, len(ids):].tolist()
                text = self.tokenizer.decode(generated, skip_special_tokens=True)
        return {"model": self.config["model"], "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": len(ids), "completion_tokens": len(generated)}}


class TeeRuntime:
    def __init__(self, config, backend=None):
        self.config = config
        self.keys = generate_keypair()
        self.lock = threading.RLock()
        self.sessions, self.streams, self.results = {}, {}, {}
        self.price = PricePolicy("local-research-v1", n_pf_rate=1, n_out_rate=2)
        self.manifest = model_manifest(config["model_path"], strict=True)
        if self.manifest["model_hash"] != config["model_hash"] or self.manifest["tokenizer_hash"] != config["tokenizer_hash"]:
            raise AttestationError("local model/tokenizer content does not match the approved manifest")
        self.descriptor = make_descriptor(config["model"], config["revision"], config["model_hash"], config["tokenizer_hash"], tee=True)
        self.deployment_digest = digest("deployment-descriptor/v1", self.descriptor)
        self.gpu = NvidiaAttestor(tuple(config.get("nvattest_command", ["nvattest"])),
            config.get("nras_url", "https://nras.attestation.nvidia.com"),
            config.get("nvidia_policy_file"), config.get("attestation_timeout_s", 120))
        # Backend construction is lazy: no model is loaded before attestation.
        self.backend = backend

    def session(self, payload):
        challenge = payload.get("challenge", "")
        if not isinstance(challenge, str) or len(bytes.fromhex(challenge)) != 32:
            raise ValueError("session challenge must be 32 bytes")
        if payload.get("deployment_digest") != self.deployment_digest:
            raise AttestationError("requested deployment is not loaded by this runtime")
        with self.lock:
            if len(self.sessions) >= self.config.get("max_sessions", 128):
                raise AttestationError("session limit reached; restart with a fresh approved runtime")
            now = int(time.time())
            session = {"version": 1, "session_id": secrets.token_hex(16), "challenge": challenge,
                "runtime_public_key": self.keys.public_key, "deployment_digest": self.deployment_digest,
                "program_hash": program_hash(), "issued_at": now,
                "expires_at": now + self.config.get("session_ttl_s", 3600)}
            session["gpu_token"] = self.gpu.collect(gpu_nonce(session))
            # Re-verify locally before any model work. The client verifies independently.
            gpu_claims = self.gpu.verify(session["gpu_token"], gpu_nonce(session))
            # NVAT collects all visible NVML GPUs. Refuse partially attested device sets.
            import subprocess
            count = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=15, check=True)
            if len([x for x in count.stdout.splitlines() if x.strip()]) != len(gpu_claims):
                raise AttestationError("visible GPUs and verified GPU evidence count differ")
            if not self.config.get("workload_evidence_command"):
                raise AttestationError("CPU_IMAGE_NOT_PROVISIONED: configure a protected measured launcher/device evidence source")
            session["workload_evidence"] = command_json(self.config["workload_evidence_command"],
                {"schema_version": 1, "session_binding": session, "manifest": self.manifest,
                 "model_path": self.config["model_path"]}, self.config.get("attestation_timeout_s", 120))
            if self.config.get("cpu_attester_command"):
                session["cpu_evidence"] = command_json(self.config["cpu_attester_command"],
                    {"schema_version": 1, "report_data": cpu_report_data(session)},
                    self.config.get("attestation_timeout_s", 120))
            else:
                session["cpu_evidence"] = collect_tdx_quote(cpu_report_data(session))
            if self.backend is None:
                backend = LocalInference(self.config)
                # Detect accidental file mutation during loading. Protection
                # after this point requires the verified immutable launch policy.
                if model_manifest(self.config["model_path"], strict=True) != self.manifest:
                    raise AttestationError("model artifacts changed during loading")
                self.backend = backend
            self.sessions[session["session_id"]] = session
            return session

    def execute(self, payload):
        with self.lock:
            session = self.sessions[payload["session_id"]]
            if int(time.time()) >= session["expires_at"]:
                raise AttestationError("session expired")
            call, context = payload["call"], payload["context"]
            cfg = call["configuration"]
            if cfg["model_id"] != self.config["model"] or cfg["provider_id"] != self.config["provider_id"]:
                raise AttestationError("unregistered provider/model")
            if context["descriptor"] != primitive(self.descriptor) or context["price_policy"] != primitive(self.price):
                raise AttestationError("unregistered deployment or pricing")
            if cfg["price_version"] != self.price.version:
                raise AttestationError("wrong pricing version")
            if cfg["cache_policy"] != {"cross_call_kv": "disabled"} or cfg["metering_policy"] != {"source": "local-generated-token-ids"}:
                raise AttestationError("unregistered cache or metering profile")
            allowed_decoding = {"temperature", "max_tokens", "chat_template_kwargs"}
            if set(cfg["decoding_config"]) - allowed_decoding:
                raise ValueError("decoding settings are outside the approved TEE profile")
            key = (session["session_id"], context["authorization_digest"], call["call_id"])
            invocation_hash = digest("tee-invocation/v1", payload)
            if key in self.results:
                original, result = self.results[key]
                if original != invocation_hash:
                    raise AttestationError("conflicting invocation for the same call")
                return result
            response = self.backend.infer({"model": cfg["model_id"], "messages": call["transformed_messages"], **cfg["decoding_config"]})
            text = response["choices"][0]["message"]["content"]
            usage = UsageVector(n_pf=response["usage"]["prompt_tokens"], n_out=response["usage"]["completion_tokens"])
            cache = {"status": "DISABLED", "cross_call_kv": False, "profile": "tee-local-v1"}
            shared = {"request_id": call["request_id"], "call_id": call["call_id"],
                "authorization_digest": context["authorization_digest"], "deployment_id": context["deployment_id"],
                "service_config_hash": digest("service-configuration/v1", cfg),
                "hb_handoff_hash": context["hb_handoff_hash"], "bp_handoff_hash": context["bp_handoff_hash"],
                "transformation_hash": digest("transformation-rule/v1", call["transformation_rule"]),
                "input_hash": digest("provider-input/v1", call["transformed_messages"]),
                "cache_pre_hash": digest("cache-pre/v1", cache), "cache_post_hash": digest("cache-post/v1", cache),
                "output_hash": digest("provider-output/v1", text), "usage": primitive(usage),
                "price_version": self.price.version, "charge_microunits": self.price.charge(usage)}
            witness, opening = {"shared": shared, "cache_evidence": cache, "provider_model": cfg["model_id"]}, secrets.token_hex(32)
            nullifier = digest("call-nullifier/v2", {"authorization_digest": context["authorization_digest"], "call_id": call["call_id"]})
            claims = {**shared, "billable": True, "witness_commitment": commitment("provider-witness/v1", witness, opening),
                "assurance_mode": "tee", "call_nullifier": nullifier, "provider_id": cfg["provider_id"]}
            claim_hash = digest("tee-receipt-claims/v1", claims)
            stream = key[:2]
            sequence, previous = self.streams.get(stream, (0, initial_state(*stream)))
            transition = {"session_id": session["session_id"], "request_id": call["request_id"], "call_id": call["call_id"],
                "sequence": sequence + 1, "previous": previous, "claim_hash": claim_hash, "created_at": int(time.time()),
                "next": digest("tee-state/v1", {"previous": previous, "claim_hash": claim_hash, "sequence": sequence + 1})}
            artifact = {"version": 1, "session_id": session["session_id"], "session_digest": digest("tee-session/v1", session),
                "transition": transition, "signature": sign_object(self.keys.private_key, "tee-transition/v1", transition)}
            result = {"response": response, "shared": shared, "witness": witness, "opening": opening,
                      "call_nullifier": nullifier, "artifact": artifact}
            self.streams[stream] = (sequence + 1, transition["next"])
            self.results[key] = (invocation_hash, result)
            return result


def serve(config, host="127.0.0.1", port=18443):
    runtime = TeeRuntime(config)
    secret = os.environ.get(config.get("api_key_env", ""), "")
    if host not in {"127.0.0.1", "localhost", "::1"} and not secret:
        raise ValueError("a remotely exposed TEE runtime requires an API key and TLS reverse proxy")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/v1/models":
                self.reply(200, {"data": [{"id": config["model"]}]})
            else: self.reply(404, {"error": "not found"})
        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            if secret and self.headers.get("Authorization") != "Bearer " + secret:
                self.reply(401, {"error": "unauthorized"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8 * 1024 * 1024: raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/session": result = runtime.session(payload)
                elif self.path == "/execute": result = runtime.execute(payload)
                elif self.path == "/v1/chat/completions":
                    if runtime.backend is None: raise AttestationError("initialize attestation before inference")
                    result = runtime.backend.infer(payload)
                else: self.reply(404, {"error": "not found"}); return
                self.reply(200, result)
            except (ValueError, KeyError, TypeError, AttestationError) as exc:
                self.reply(400, {"error": type(exc).__name__})
            except Exception:
                self.reply(503, {"error": "runtime unavailable"})
    server = ThreadingHTTPServer((host, port), Handler)
    try: server.serve_forever()
    finally: server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--program-hash", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18443)
    args = parser.parse_args()
    if args.program_hash:
        print(program_hash()); return
    if not args.config: parser.error("--config is required")
    path = Path(args.config).resolve()
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for field in ("model_path", "nvidia_policy_file"):
        p = Path(config[field])
        config[field] = str((p if p.is_absolute() else path.parent / p).resolve())
    serve(config, args.host, args.port)


if __name__ == "__main__": main()
