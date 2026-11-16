# TokenProv

TokenProv is a research implementation of verifiable token-spending provenance for LLM services. It links authorization, call lifecycles, signed handoffs, provider receipts, evidence, and exact settlement, with an auditor that checks the resulting records.

The source release includes an offline protocol smoke test, a configurable serving evaluation, controlled security and scalability experiments, and optional assurance components. Ordinary serving uses locally signed receipts over an OpenAI-compatible service's responses and usage; these signatures do not attest the model's execution.

## Quick start

Use Python 3.11 or newer in a virtual environment. From this directory:

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m tokenprov.server_eval smoke --config configs/smoke.yaml
```

The smoke command runs without a model, GPU, network service, or tokenizer. It uses explicitly labeled fixtures, including a commitment check in place of a cryptographic proof, and creates a fresh directory under `results/server_runs/`. It verifies the workflow and produces no model, TEE, or ZK performance result.

## Real serving

Install the tokenizer client and configure an existing OpenAI-compatible endpoint:

```bash
python -m pip install -e '.[server-client]'
```

Edit [configs/server-eval.yaml](configs/server-eval.yaml) with the served model name, URL environment variable, and local tokenizer directory. Supply API keys through environment variables when required. Relative configuration paths resolve from the YAML file's directory.

```bash
python -m tokenprov.server_eval plan --config configs/server-eval.yaml
python -m tokenprov.server_eval preflight --config configs/server-eval.yaml
python -m tokenprov.server_eval run --config configs/server-eval.yaml
python -m tokenprov.server_eval report --run results/server_runs/<run-directory>
```

The provided configuration compares `Vanilla`, `SignedLog`, `SignedLogPlus`, and `TokenProv-Full`: three repetitions, 128 measured requests and four warmups per system per repetition, concurrency eight, eight calls per request, 2,048 input tokens, and an output cap of 256 tokens. Proof generation is disabled until an actual backend is configured. Check `plan` before making real service calls.

An optional local Transformers service is provided in [scripts/serve_transformers.py](scripts/serve_transformers.py). It loads an existing local model; see [the reproduction guide](docs/REPRODUCIBILITY.md) for setup, workload controls, output definitions, and additional experiments.

## Reproduce the paper figures and tables

The [paper artifact guide](paper/README.md) maps Sections B--F and the provenance
appendix to retained data, final figures/tables, and generators. All three
real-serving rounds are included, including slower observations. These commands
use bundled measurements and start no model, GPU, TEE or prover:

```bash
python paper/reproduce.py --verify-only
python -m pip install -e '.[plots]'
python paper/reproduce.py --section all
```

Final snapshots are in `paper/published/`; regenerated files go to `paper/generated/`.
Use `--section b2` (or `b1`, `b3_b4`, `c`, `d`, `e`, `f`, `cases`) for one section,
`--output` for another output location, and `--font-dir` for locally installed
fonts. Figures use Times New Roman; the examples also use Microsoft YaHei for
original Chinese text. Fonts are not bundled. Regenerated CSVs are compared with
retained values; hashes verify bundled files. This is distinct from rerunning live
experiments or independently verifying every archived cryptographic proof.

Fresh CPU storage, security and ablation experiments are available through
`python experiments/paper_protocol.py --help`. See the component and experiment
[coverage matrix](docs/EXPERIMENT_COVERAGE.md) for their settings and historical IDs.

## Assurance scope

Protocol signatures and audits authenticate records and enforce their relationships. The default local ledger supplies authenticated ordering and local durable storage. The optional Groth16 circuit covers scoped token accounting and commitment constraints. The confidential runtime requires actual platform evidence, approved policies, and workload/device identity adapters before enrollment can succeed. The separate Windows signing experiment protects private-key operations.

Read [ASSURANCE.md](docs/ASSURANCE.md) before interpreting any of these components as evidence about provider behavior. The source release includes curated historical measurements and final paper artifacts, while excluding complete server run archives, downloaded models, proving keys, virtual environments, and third-party toolchains. Fresh runs reproduce the methods; numerical GPU results depend on the deployment and workload.

## Repository

| Path | Contents |
| --- | --- |
| [tokenprov/](tokenprov/) | Protocol, audit, settlement, evidence, serving evaluation, and assurance adapters |
| [configs/](configs/) | Offline fixture, serving, workload, and confidential-runtime configuration |
| [tests/](tests/) | Protocol, persistence, isolation, and rejection checks |
| [experiments/](experiments/README.md) | CPU storage/security/ablation, accounting-proof, and isolated-signing experiments |
| [paper/](paper/README.md) | Retained paper data, final snapshots, and portable figure/table generators |
| [tools/zk/](tools/zk/README.md) | Optional Groth16 circuit, proof worker, and dependency lockfile |
| [docs/](docs/ARCHITECTURE.md) | Architecture, reproduction method, and assurance boundaries |

Released under the [MIT License](LICENSE).
Third-party dependencies and model weights retain their own licenses.
The complete source ZIP and source tarball include the paper materials. The Python
wheel installs the runtime package; use the source tree for experiment scripts,
configuration examples and paper reproduction.
