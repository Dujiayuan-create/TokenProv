# Optional scoped Groth16 tooling

This circuit proves a Poseidon commitment opening, integer prompt/output charge
arithmetic, and equality of two public request hashes. The worker checks receipt
and witness bindings in host code. This is one scoped equality within `R_link`;
it does not prove the full TokenProv relation, LLM inference, or hardware identity.
The default Python runtime does not require these tools.

From a fresh checkout, install the locked JavaScript dependencies:

```sh
npm ci --prefix tools/zk
```

The lockfile pins `snarkjs` 0.7.5, `circomlibjs` 0.1.7, and `circomlib` 2.0.5.
Provide Node.js, Python 3.11+, and a separate **Circom 2.2.3** executable. Circom
is not installed by npm or the helper. Obtain the compiler from the
[Circom project](https://github.com/iden3/circom/releases/tag/v2.2.3).

You must also provide a trusted **BN128 Powers of Tau file prepared for phase 2**,
large enough for this circuit. Authenticate its source and checksum independently.
The helper does not download a transcript, generate phase-1 parameters, or claim
that a newly generated transcript is trusted. See the upstream
[snarkjs ceremony instructions](https://github.com/iden3/snarkjs/tree/v0.7.5#readme)
for transcript preparation and verification.

For a local research/development setup, run from the repository root:

```sh
python tools/zk/setup.py --circom /path/to/circom --ptau /path/to/trusted_phase2.ptau --development-setup
```

On Windows, quote executable and transcript paths containing spaces; for example,
`--circom "C:/tools/circom.exe"`. `--node` selects a separate Node.js executable
when it is not on `PATH`. The helper checks the compiler and installed package
versions, compiles R1CS/WASM/SYM with the local dependency include path, checks
the supplied Powers of Tau transcript, creates an initial Groth16 key, and adds
one local phase-2 contribution using OS randomness passed through stdin. It then
checks the final key against the circuit and transcript and exports the
verification key. This **one-party development setup is not a production
multiparty ceremony**. Transcript consistency checks do not establish the
identity or independence of contributors or prove destruction of setup secrets.

To use an externally supplied final key instead of creating a local setup:

```sh
python tools/zk/setup.py --circom /path/to/circom --ptau /path/to/trusted_phase2.ptau --zkey /path/to/approved_final.zkey
```

The external key is copied and checked against the newly compiled circuit and
the supplied transcript. Its ceremony history and suitability for your deployment
remain your responsibility. Neither setup path establishes production trust.

The default output is the ignored `tools/zk/build/` directory. It must not exist;
use `--output /path/to/new-directory` for a separate build. Existing files are
never replaced. Failed builds retain partial output for inspection, and a retry
requires another new output directory. No setup commands run during proof jobs.
The generated worker interface is:

```text
build/tokenprov_accounting_js/tokenprov_accounting.wasm
build/tokenprov_accounting_final.zkey
build/verification_key.json
build/setup.json
```

`setup.json` records input and output hashes, versions, and the setup mode. It is
a local provenance record, not ceremony certification. Treat all setup material
as deployment-specific; do not commit keys, transcripts, or generated witnesses.

`proof_worker.mjs` accepts one JSON request on stdin and emits one JSON response
on stdout. Its command interface is:

```sh
node tools/zk/proof_worker.mjs prove /absolute/path/to/build
node tools/zk/proof_worker.mjs verify /absolute/path/to/build
```

The one-job worker caps the ffjavascript curve pool at two workers per process
and exits after flushing its response. This is a local concurrency bound, not
a CPU-affinity or hardware guarantee.

Use these commands through the configured TokenProv proof adapter, which supplies
the request schema and checks the response binding. The separate
`c_assurance_worker.mjs` supports the optional component benchmark described in
`experiments/README.md`. Installing these dependencies or building keys does not
itself constitute a proof benchmark result.
