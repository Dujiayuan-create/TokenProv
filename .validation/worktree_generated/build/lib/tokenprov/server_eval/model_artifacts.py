"""Content manifests for existing local model weights; never downloads models."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tokenprov.common.models import DeploymentDescriptor
from tokenprov.common.serialization import digest

TOKENIZER_FILES = {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "vocab.json", "merges.txt", "tokenizer.model", "spiece.model", "chat_template.jinja",
    "added_tokens.json"}


def hash_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def model_manifest(directory, *, strict=False):
    root = Path(directory).resolve()
    if not root.is_dir(): raise ValueError("model directory does not exist")
    weights, tokenizer = {}, {}
    for path in sorted(root.rglob("*")):
        if strict and path.is_symlink():
            raise ValueError("TEE model snapshots must contain real files, not mutable symlinks")
        if strict and (path.suffix in {".bin", ".pt", ".pth", ".py"} or path.name.startswith("adapter_")):
            raise ValueError("TEE profile rejects alternate weights, adapters and executable model code")
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts): continue
        name = path.relative_to(root).as_posix()
        if path.name in TOKENIZER_FILES or name.startswith("chat_templates/"):
            tokenizer[name] = {"sha256": hash_file(path), "bytes": path.stat().st_size}
        elif path.suffix == ".safetensors" or path.name in {"config.json", "generation_config.json", "model.safetensors.index.json"}:
            weights[name] = {"sha256": hash_file(path), "bytes": path.stat().st_size}
    if not any(name.endswith(".safetensors") for name in weights) or not tokenizer:
        raise ValueError("local safetensors weights and tokenizer files are required")
    return {"schema_version": 1, "model_hash": digest("model-artifacts/v1", weights),
        "tokenizer_hash": digest("tokenizer-artifacts/v1", tokenizer),
        "model_files": weights, "tokenizer_files": tokenizer}


def make_descriptor(model, revision, model_hash, tokenizer_hash, *, tee=False):
    return DeploymentDescriptor(model, revision, model_hash, tokenizer_hash,
        digest("canonicalization-rule/v1", "hf-registered-chat-template-thinking-disabled"),
        digest("serving-semantics/v2", {"mode": "tee-local-inference" if tee else "external-openai-api",
            "cross_call_kv": "disabled" if tee else "unavailable",
            "reasoning": "disabled", "metering": "generated-token-ids" if tee else "provider-usage",
            "price_version": "local-research-v1"}))
