# Reproduction guide

Run commands from the release root in a Python 3.11+ virtual environment. The protocol and offline tests require no model or GPU. Real serving requires a separately deployed service and matching local tokenizer files; the client does not download models.

## Install and validate

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m tokenprov.server_eval smoke --config configs/smoke.yaml
```

Smoke execution replaces the workload with a small deterministic fixture, skips model preflight, and labels observations as fixtures. Its proof worker checks an opening only. Use its counts and validation status to check installation; its timings do not measure serving or real proof performance.

## Connect a model service

An existing OpenAI-compatible service can be reused. The tokenizer extra pins Transformers 4.54.0 for input calibration:

```bash
python -m pip install -e '.[server-client]'
```

Set the endpoint URL; this example uses Bash:

```bash
export TOKENPROV_MODEL_A_URL=http://127.0.0.1:8000
# Set TOKENPROV_MODEL_A_API_KEY if the endpoint requires authentication.
python -m tokenprov.server_eval discover --base-url "$TOKENPROV_MODEL_A_URL"
```

In PowerShell, set `$env:TOKENPROV_MODEL_A_URL = 'http://127.0.0.1:8000'` and pass `$env:TOKENPROV_MODEL_A_URL` to `--base-url`.

Edit [configs/server-eval.yaml](../configs/server-eval.yaml): set `model` to the identifier returned by `/v1/models`, `tokenizer_path` to the matching local tokenizer directory, and the revision/hash fields to the identity being registered. Use `api_key_env` for the name of an API-key environment variable. Paths such as `tokenizer_path`, `trace_file`, `tee_policy`, and `output_root` resolve relative to the configuration file, unless absolute.

To record local file hashes:

```bash
python -m tokenprov.server_eval hash-model --path /srv/models/model-a > model-a-manifest.json
```

Hashing may read all model weights. These hashes describe files, and do not attest which files a remote service actually loaded.

## Optional local model service

[scripts/serve_transformers.py](../scripts/serve_transformers.py) implements the controlled local Transformers service. Install the PyTorch build compatible with your GPU and CUDA environment, then install the model-server extra:

```bash
python -m pip install -e '.[model-server]'
python scripts/serve_transformers.py --model-dir /srv/models/model-a --model-name model-a --port 8000 --max-batch 8 --memory-fraction 0.1
```

The directory must already contain the model and tokenizer. The service uses local files, BF16, SDPA, a 4,096-token context limit, a two-millisecond batching window, and at most eight requests per microbatch with the command above. Select the GPU with `CUDA_VISIBLE_DEVICES`, and set the model directory, served name, batch size, and memory fraction for the actual deployment; model size and other GPU users determine whether it fits. Keep server startup arguments and software versions with each measurement.

This microbatch service and a vLLM service use different scheduling implementations. Compare systems against the same service profile, and report deployment differences when comparing services.

## Run the serving study

```bash
python -m tokenprov.server_eval plan --config configs/server-eval.yaml
python -m tokenprov.server_eval preflight --config configs/server-eval.yaml
python -m tokenprov.server_eval run --config configs/server-eval.yaml
python -m tokenprov.server_eval report --run results/server_runs/<run-directory>
```

`plan` performs no inference. It reports the cells, total measured requests, estimated model calls, and expected proof jobs if a backend is enabled. `preflight` checks configured endpoints, listed model names, local tokenizer availability, and any enabled assurance tools. Real execution stops if preflight fails.

The supplied serving configuration specifies:

| Setting | Value |
| --- | --- |
| Systems | `Vanilla`, `SignedLog`, `SignedLogPlus`, `TokenProv-Full` |
| Repetitions | 3 |
| Measured requests | 128 per system, cell, and repetition |
| Warmups | 4 per system, cell, and repetition |
| Concurrent requests | 8 |
| Sequential calls per request | 8 |
| Input tokens per call | 2,048 |
| Output cap per call | 256; actual generated tokens are recorded |
| Request audit probability | 0.10 |
| Proof backend | Disabled until both real commands are supplied |

Each extra endpoint, concurrency level, token size, or call-count setting adds cells. Start with a small real run before increasing these values. Failures remain in the observations; failed calls are not automatically retried.

System order is seeded and shuffled for each repetition. Real audit sampling uses the operating system's randomness, and saved rows record the actual choices. Identical task inputs are used for each system within a repetition. For protocol-overhead measurements, disable service prefix caching or apply a consistent, recorded cache policy. `shared_prompt_percent` controls repeated whole prompts at selected call positions; it is not a measured cache-hit rate.

The configuration runs three repetitions in one fresh invocation. Historical measurements collected as one initial launch plus two later launches are a different execution schedule. Complete historical run archives are excluded. Curated paper measurements and final generators are included in [paper/](../paper/README.md); fresh runs support method reproduction, and do not promise identical numerical GPU observations.

## Outputs and measurement definitions

Each serving or smoke run creates a new timestamp-and-random-identifier directory under `results/server_runs/`:

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Configuration, execution mode, and run identity |
| `environment.json` | Client Python/platform and package versions |
| `preflight.json` | Real serving preflight diagnostics |
| `setup.jsonl` | Deployment identities, registrations, and setup costs |
| `requests.jsonl`, `calls.jsonl` | Warmup and measured observations, timing, usage, and failures |
| `witnesses.jsonl` | Retained witness references and sampling decisions |
| `proofs.jsonl`, proof JSON files | Jobs and proof artifacts when proof work runs |
| `runtime/` | Per-system ledger and evidence storage |
| `summary.json`, `validation.json`, `status.json` | Saved summary, consistency checks, and completion state |

The client environment manifest does not describe the remote service's environment. Archive server versions, model/tokenizer hashes, launch arguments, hardware, driver, cache policy, and relevant firmware settings separately when reporting results.

`T_online` covers authorization through completion of the final call's receipt/lifecycle processing. It excludes closure, settlement, evidence persistence, ledger updates, and the subsequent synchronous protocol audit. `T_request_total` includes those request-finalization operations. `request_pipeline_ns` also includes lightweight request setup, witness indexing, and proof-queue backpressure; it excludes input preparation and waiting for a request executor. Throughput uses the full concurrent batch's wall time.

The report validates request/call/proof correspondence and retains attempted, successful, and failed counts. Means and paired overhead confidence intervals use repetition means. Quantiles are computed within each repetition: p95 requires at least 100 successful requests and p99 requires at least 1,000. The default 128-request repetition cannot support p99.

`aggregate_prove_ms` sums successful job wall durations. It is neither CPU time nor elapsed parallel makespan. Worker service time including failures and queue makespan are recorded separately. A failed proof job prevents reporting a complete per-successful-request proof cost.

## Security, scalability, and trace workloads

These commands run controlled protocol experiments without model inference:

```bash
python -m tokenprov.server_eval security --config configs/smoke.yaml --trials 100 --queries 16 --calls 8 --seeds 42,43,44
python -m tokenprov.server_eval protocol --config configs/smoke.yaml --calls 1,8,32,128,512 --event-factors 4,8 --repetitions 3
```

Security results exercise the implemented mutation set and independent baselines. They are not comprehensive coverage of adaptive attackers or dishonest model execution. Protocol scalability writes `protocol.jsonl`; security writes `security.jsonl`. Their dedicated summaries are separate from the serving `report` command.

Set `workload.trace_file` to replay JSONL tasks in this form:

```json
{"task_id":"task-001","calls":[{"messages":[{"role":"user","content":"Example task"}]}]}
```

See [configs/workloads/example_traces.jsonl](../configs/workloads/example_traces.jsonl). This replays fixed call inputs; it does not execute an agent's tool selection, branching, environment interaction, or task scoring.

Additional scoped proof and signing experiments are documented in [experiments/README.md](../experiments/README.md). Their prerequisites and assurance boundaries differ from ordinary serving; see [ASSURANCE.md](ASSURANCE.md).
