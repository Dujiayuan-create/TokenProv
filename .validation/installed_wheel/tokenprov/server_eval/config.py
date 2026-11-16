"""Strict server experiment configuration without credentials in YAML."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

SYSTEMS = ("Vanilla", "SignedLog", "SignedLogPlus", "TokenProv-Full", "TokenProv-TEE")


def _keys(value, allowed, where):
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(f"unknown {where} fields: {sorted(extra)}")


def positive_int(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def safe_url(value, name):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query parameters or fragments")
    return value.rstrip("/")


@dataclass(frozen=True)
class Endpoint:
    id: str
    model: str
    base_url: str = ""
    base_url_env: str = ""
    api_key_env: str = ""
    tokenizer_path: str = ""
    revision: str = "unregistered"
    model_hash: str = "unregistered"
    tokenizer_hash: str = "unregistered"
    timeout_s: int = 180
    tee_url: str = ""
    tee_policy: str = ""
    tee_api_key_env: str = ""
    node_id: str = "unspecified"

    def url(self):
        value = os.environ.get(self.base_url_env, "") if self.base_url_env else self.base_url
        if not value:
            raise ValueError(f"missing URL for endpoint {self.id}; set {self.base_url_env}")
        return safe_url(value, self.id)

    def api_key(self, *, tee=False):
        name = self.tee_api_key_env if tee and self.tee_api_key_env else self.api_key_env
        if name and not os.environ.get(name):
            raise ValueError(f"missing environment variable {name}")
        return os.environ.get(name) if name else None


@dataclass(frozen=True)
class Workload:
    calls: tuple[int, ...] = (8,)
    input_tokens: tuple[int, ...] = (2048,)
    max_output_tokens: tuple[int, ...] = (256,)
    shared_prompt_percent: int = 0
    trace_file: str = ""
    routing: str = "per_endpoint"


@dataclass(frozen=True)
class AuditSettings:
    probability: float = 0.1
    workers: int = 2
    queue_capacity: int = 128
    prover_command: tuple[str, ...] = ()
    verifier_command: tuple[str, ...] = ()
    timeout_s: int = 300
    retention_s: int = 3600


@dataclass(frozen=True)
class Config:
    source: Path
    endpoints: tuple[Endpoint, ...]
    output_root: Path
    systems: tuple[str, ...] = ("Vanilla", "SignedLogPlus", "TokenProv-Full")
    concurrency: tuple[int, ...] = (8,)
    repetitions: int = 3
    requests_per_run: int = 128
    warmups: int = 4
    seed: int = 42
    workload: Workload = field(default_factory=Workload)
    audit: AuditSettings = field(default_factory=AuditSettings)

    def cells(self):
        endpoints = self.endpoints[:1] if self.workload.routing == "round_robin" else self.endpoints
        for endpoint in endpoints:
            for calls in ((0,) if self.workload.trace_file else self.workload.calls):
                for tokens in ((0,) if self.workload.trace_file else self.workload.input_tokens):
                    for output in self.workload.max_output_tokens:
                        for concurrency in self.concurrency:
                            yield endpoint, calls, tokens, output, concurrency


def load_config(path: str | Path) -> Config:
    source = Path(path).resolve()
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    _keys(raw, ("schema_version", "endpoints", "output_root", "systems", "execution", "workload", "audit"), "config")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    base = source.parent

    def local_path(value):
        if not isinstance(value, str) or not value:
            return ""
        p = Path(value).expanduser()
        return str((p if p.is_absolute() else base / p).resolve())

    endpoints = []
    for index, item in enumerate(raw.get("endpoints", [])):
        _keys(item, Endpoint.__dataclass_fields__, f"endpoints[{index}]")
        if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("model"), str) or not item["model"]:
            raise ValueError("each endpoint requires nonempty id and model")
        if not item["id"].replace("-", "").replace("_", "").isalnum():
            raise ValueError("endpoint id must contain only letters, numbers, '-' or '_'")
        if bool(item.get("base_url")) == bool(item.get("base_url_env")):
            raise ValueError("endpoint requires exactly one of base_url or base_url_env")
        for name in ("base_url", "tee_url"):
            if item.get(name):
                safe_url(item[name], name)
        positive_int(item.get("timeout_s", 180), "timeout_s")
        for name in ("tokenizer_path", "tee_policy"):
            if item.get(name):
                item[name] = local_path(item[name])
        endpoints.append(Endpoint(**item))
    if not endpoints or len({x.id for x in endpoints}) != len(endpoints):
        raise ValueError("endpoints must be nonempty with unique ids")
    execution = raw.get("execution", {})
    _keys(execution, ("concurrency", "repetitions", "requests_per_run", "warmups", "seed"), "execution")
    work = raw.get("workload", {})
    _keys(work, Workload.__dataclass_fields__, "workload")
    for name, default in (("calls", [8]), ("input_tokens", [2048]), ("max_output_tokens", [256])):
        values = work.get(name, default)
        if not isinstance(values, list) or not values or len(set(values)) != len(values):
            raise ValueError(f"workload.{name} must be a nonempty list without duplicates")
        work[name] = tuple(positive_int(x, name) for x in values)
    shared = work.get("shared_prompt_percent", 0)
    positive_int(shared, "shared_prompt_percent", 0)
    if shared > 100:
        raise ValueError("shared_prompt_percent must be <= 100")
    if work.get("trace_file"):
        work["trace_file"] = local_path(work["trace_file"])
    if work.get("routing", "per_endpoint") not in {"per_endpoint", "round_robin"}:
        raise ValueError("workload.routing must be per_endpoint or round_robin")
    settings = raw.get("audit", {})
    _keys(settings, AuditSettings.__dataclass_fields__, "audit")
    probability = settings.get("probability", .1)
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("audit.probability must be between 0 and 1")
    for name in ("workers", "queue_capacity", "timeout_s", "retention_s"):
        positive_int(settings.get(name, getattr(AuditSettings(), name)), "audit." + name)
    for name in ("prover_command", "verifier_command"):
        value = settings.get(name, [])
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise ValueError(f"audit.{name} must be an argv list")
        project = str(Path(__file__).resolve().parents[2])
        settings[name] = tuple(x.replace("{project_root}", project).replace("{config_dir}", str(base)) for x in value)
    if bool(settings["prover_command"]) != bool(settings["verifier_command"]):
        raise ValueError("configure both prover_command and verifier_command")
    systems = tuple(raw.get("systems", ["Vanilla", "SignedLogPlus", "TokenProv-Full"]))
    if not systems or len(set(systems)) != len(systems) or set(systems) - set(SYSTEMS):
        raise ValueError(f"systems must be unique members of {SYSTEMS}")
    concurrency = execution.get("concurrency", [8])
    if not isinstance(concurrency, list) or not concurrency or len(set(concurrency)) != len(concurrency):
        raise ValueError("concurrency must be a nonempty unique list")
    concurrency = tuple(positive_int(x, "concurrency") for x in concurrency)
    output = Path(local_path(raw.get("output_root", "../../results/server_runs")))
    # New runs must not overwrite or mingle with the curated paper datasets.
    if output.name != "server_runs":
        raise ValueError("output_root must end in server_runs (isolated versioned results)")
    if "final" in output.parts:
        raise ValueError("results/final is read-only to this runner")
    seed = execution.get("seed", 42)
    positive_int(seed, "seed", 0)
    return Config(source, tuple(endpoints), output, systems, concurrency,
        positive_int(execution.get("repetitions", 3), "repetitions"),
        positive_int(execution.get("requests_per_run", 128), "requests_per_run"),
        positive_int(execution.get("warmups", 4), "warmups", 0), seed,
        Workload(**work), AuditSettings(**settings))
