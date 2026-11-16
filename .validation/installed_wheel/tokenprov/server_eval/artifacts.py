"""Append-only observations and atomic summaries in fresh run directories."""
from __future__ import annotations

import json
import os
import platform
import secrets
import threading
from importlib.metadata import PackageNotFoundError, version
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


class RunArtifacts:
    def __init__(self, config, mode):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(6)
        self.root = config.output_root / self.run_id
        if os.name == "nt":
            # Evidence names contain full hashes. Support deep Windows workspaces
            # without requiring a machine-wide LongPathsEnabled registry change.
            absolute = str(self.root.resolve())
            if not absolute.startswith("\\\\?\\"):
                self.root = Path("\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.mode = mode
        manifest = asdict(config)
        manifest["source"] = str(config.source)
        manifest["output_root"] = str(config.output_root)
        self.write_json("manifest.json", {"schema_version": 1, "run_id": self.run_id,
            "mode": mode, "config": manifest})
        packages = {}
        for name in ("tokenprov", "cryptography", "PyYAML", "transformers", "torch", "vllm", "litellm", "nvidia-ml-py"):
            try: packages[name] = version(name)
            except PackageNotFoundError: packages[name] = None
        self.write_json("environment.json", {"python": platform.python_version(), "packages": packages,
            "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count()})
        self.status("running")

    def append(self, name, record):
        self.append_many(name, (record,))

    def append_many(self, name, records):
        lines = "".join(json.dumps({"schema_version": 1, "run_id": self.run_id,
            "execution_mode": self.mode, **record}, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
            for record in records)
        if not lines: return
        with self.lock:
            with (self.root / name).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(lines)
                handle.flush()

    def write_json(self, name, data):
        path = self.root / name
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def status(self, state, **extra):
        self.write_json("status.json", {"state": state, "updated_at": datetime.now(timezone.utc).isoformat(), **extra})
