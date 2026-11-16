# Protocol and assurance experiments

The main serving evaluation uses `python -m tokenprov.server_eval`; see
[the reproduction guide](../docs/REPRODUCIBILITY.md). The scripts here replay retained
evidence from an actual serving run. Curated retained results and final paper
generators are in [paper/](../paper/README.md). Complete run archives, model weights,
private deployment keys, downloaded toolchains and proving assets are not shipped.

## CPU storage, security and ablation experiments

This portable entry point supplies the independent historical drivers for D's
storage comparison, E's protocol attacks/localization/collusion results, and F's
rule-gating/cumulative validation ablations. Historical IDs (E1, F1--F4, G1/G2)
are preserved in filenames; they are not the current section numbers. All these
experiments use protocol fixtures, without an LLM.

```bash
python -m pip install -e '.[benchmarks]'
python experiments/paper_protocol.py --experiment storage --output results/paper-storage
python experiments/paper_protocol.py --experiment security --output results/paper-security
python experiments/paper_protocol.py --experiment ablation --output results/paper-ablation
```

The historical settings are five seeds (42--46), storage call counts
1/2/4/8/16/32/64, 100 security trials per fixture, and 100 warmups plus 1,000
ablation samples per variant per seed. Use `--seeds`, `--calls`, `--trials`,
`--samples` and `--warmups` for a small diagnostic run. These overrides change the
experiment and must not be labeled as the published cohort. Each command requires
a new output directory and cannot overwrite retained results.

Storage uses the current runtime's serialization and local filesystem; fresh byte
counts and timings can differ from the historical release. The historical splicing
comparison includes source-local checks and a local-check surrogate. Rule ablation
is an acceptance gate over the auditor's first failure, not a rebuilt implementation
with a module physically removed. These distinctions also appear in the paper tables.

The [coverage matrix](../docs/EXPERIMENT_COVERAGE.md) links each final result to its
data and executable method. To redraw existing results instead of launching new
experiments, use `python paper/reproduce.py`.

## C1--C3 scoped Groth16 replay

Install the [optional proof dependencies and matching build assets](../tools/zk/README.md).
The source run must contain `requests.jsonl`, `witnesses.jsonl` and their referenced
evidence bundles. It must have at least 100 successful, non-warmup
`TokenProv-Full` requests marked `REAL_MODEL_SERVING`, with eight calls each.
For example, use a small real serving configuration with 128 requests and eight
calls; an offline serving `smoke` run cannot supply this cohort.

From the repository root:

```bash
npm ci --prefix tools/zk
python experiments/run_c_assurance_server.py \
  --source-run results/server_runs/YOUR_RUN \
  --build-dir /path/to/matching/circuit-build \
  --output results/assurance/YOUR_NEW_RUN --smoke
python experiments/report_c_assurance.py results/assurance/YOUR_NEW_RUN
```

The assurance `--smoke` option performs a small number of **actual proofs** with
the supplied assets and retained inputs. It starts no LLM or GPU service and
does not substitute fixture proofs. It still validates the full source cohort.
Omit `--smoke` for three rounds by default, or specify `--rounds` and `--seed`.
`--node` defaults to the executable on `PATH`. `--legacy-worker` can override
the process-per-call comparison adapter; `--skip-legacy` omits that comparison.
All output goes to a new directory; existing paths and output inside the source
run are rejected. The installed npm directory and source evidence are read-only.

- C1 separates persistent warm proof work from process-per-call command costs.
- C2 prepares and fsyncs retained inputs in both modes. Immediate proves before
  the foreground timer ends; Deferred proves and verifies afterward.
- C3 persists 100 requests before selecting nested challenge sets. Each rate
  executes its own proofs, using all eight calls of every challenged request.

There are two persistent proof processes, each with at most two curve workers.
The default full run generates thousands of CPU proofs; start with `--smoke`.
The report derives counts and statistics from the saved plan, including smoke
runs and custom round counts. It exports JSON and LaTeX summaries into a fresh
`paper_exports/` directory, or the new path supplied with `--output`.

The circuit proves scoped Poseidon opening, token-charge arithmetic and a
request-hash equality. SHA-256 witness commitments and shared call fields are
checked on the host. This does not prove LLM inference, the full protocol
linkage relation or confidential execution. C2 replay phase times do not measure
live inference latency or GPU interference. Summed job wall durations represent
work, not measured CPU time; audit makespan is reported separately. File-hash
validation in the report does not independently rerun cryptographic verification.

## Isolated receipt signing

See [the Windows CNG component guide](tee_signing/README.md) for real VBS-isolated
key operations and a report verifier that also runs on Linux and macOS.
