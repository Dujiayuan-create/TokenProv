from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from tokenprov.server_eval.config import load_config


def emit(value): print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(description="TokenProv isolated server evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "preflight", "run", "smoke", "security", "protocol"):
        item = sub.add_parser(command)
        item.add_argument("--config", required=True)
        if command == "preflight": item.add_argument("--no-network", action="store_true")
        if command == "smoke": item.add_argument("--requests", type=int, default=4)
        if command == "security":
            item.add_argument("--trials", type=int, default=100)
            item.add_argument("--queries", type=int, default=16)
            item.add_argument("--calls", type=int, default=8)
            item.add_argument("--seeds", default="42,43,44")
        if command == "protocol":
            item.add_argument("--calls", default="1,8,32,128,512")
            item.add_argument("--event-factors", default="4,8")
            item.add_argument("--repetitions", type=int, default=3)
    report = sub.add_parser("report"); report.add_argument("--run", required=True)
    hashes = sub.add_parser("hash-model"); hashes.add_argument("--path", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--base-url", required=True)
    discover.add_argument("--api-key-env", default="")
    args = parser.parse_args(argv)
    progress = lambda message: print(message, file=sys.stderr, flush=True)
    try:
        if args.command == "hash-model":
            from tokenprov.server_eval.model_artifacts import model_manifest
            emit(model_manifest(args.path)); return 0
        if args.command == "discover":
            from tokenprov.server_eval.transport import JsonHttpClient
            if args.api_key_env and not os.environ.get(args.api_key_env): raise ValueError("API key environment variable is unset")
            emit(JsonHttpClient(args.base_url, os.environ.get(args.api_key_env)).request("/v1/models")); return 0
        if args.command == "report":
            from tokenprov.server_eval.metrics import summarize
            result = summarize(args.run); emit(result)
            return 0 if result["validation"]["passed"] else 2
        config = load_config(args.config)
        if args.command == "protocol":
            from tokenprov.server_eval.protocol import run_protocol
            root, count = run_protocol(config, tuple(int(x) for x in args.calls.split(",")),
                tuple(int(x) for x in args.event_factors.split(",")), args.repetitions, progress)
            emit({"run": str(root), "observations": count}); return 0
        if args.command == "plan":
            from tokenprov.server_eval.runner import plan
            emit(plan(config)); return 0
        if args.command == "preflight":
            from tokenprov.server_eval.preflight import preflight
            result = preflight(config, check_network=not args.no_network); emit(result)
            return 0 if result["ok"] else 2
        if args.command == "security":
            from tokenprov.server_eval.security import run_security
            root, summary = run_security(config, trials=args.trials, queries=args.queries,
                calls=args.calls, seeds=tuple(int(x) for x in args.seeds.split(",")), progress=progress)
            emit({"run": str(root), "summary": summary}); return 0
        from tokenprov.server_eval.runner import run
        diagnostics = None
        if args.command == "smoke":
            if args.requests < 1: raise ValueError("--requests must be positive")
            config = replace(config, endpoints=config.endpoints[:1],
                systems=("Vanilla", "SignedLogPlus", "TokenProv-Full"),
                concurrency=(min(2, args.requests),), repetitions=1, requests_per_run=args.requests, warmups=1,
                workload=replace(config.workload, calls=(2,), input_tokens=(64,), max_output_tokens=(8,), trace_file="", routing="per_endpoint"),
                audit=replace(config.audit, probability=1, workers=2, queue_capacity=2))
        else:
            from tokenprov.server_eval.preflight import preflight
            diagnostics = preflight(config)
            if not diagnostics["ok"]: emit(diagnostics); return 2
        root, summary = run(config, fixture=args.command == "smoke", progress=progress, diagnostics=diagnostics)
        emit({"run": str(root), "counts": summary["counts"], "validation": summary["validation"]})
        return 0 if summary["validation"]["passed"] and not summary["counts"]["failed_measured_requests"] and not summary["counts"]["failed_proof_jobs"] else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
