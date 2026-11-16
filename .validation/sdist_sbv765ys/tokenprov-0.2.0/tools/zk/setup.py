"""Build optional scoped Groth16 assets; never download or overwrite artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys


TOOLS_DIR = Path(__file__).resolve().parent
CIRCOM_VERSION = "2.2.3"
PACKAGES = {"snarkjs": "0.7.5", "circomlibjs": "0.1.7", "circomlib": "2.0.5"}
STEM = "tokenprov_accounting"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--circom", default="circom", help="Circom 2.2.3 executable or executable path")
    result.add_argument("--node", default="node", help="Node.js executable or executable path")
    result.add_argument("--ptau", required=True, type=Path,
                        help="trusted, phase-2-prepared BN128 Powers of Tau file; supplied by you")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--development-setup", action="store_true",
                      help="create one local phase-2 contribution for research/development only")
    mode.add_argument("--zkey", type=Path,
                      help="externally supplied final Groth16 key for this exact circuit and ptau")
    result.add_argument("--output", type=Path, default=TOOLS_DIR / "build",
                        help="new output directory; must not exist (default: tools/zk/build)")
    return result


def regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or not resolved.stat().st_size:
        raise ValueError(f"{label} must be a nonempty file: {resolved}")
    return resolved


def executable(value: str) -> str:
    found = shutil.which(value)
    if found is None:
        # Support explicit relative paths on platforms whose PATH excludes cwd.
        candidate = Path(value).expanduser()
        if not candidate.is_file() or not shutil.which(str(candidate.resolve())):
            raise ValueError(f"executable not found: {value}")
        found = str(candidate.resolve())
    return str(Path(found).resolve())


def run(command: list[str], *, entropy: str | None = None) -> None:
    # In particular, do not interpolate paths into a shell or expose contribution
    # entropy in argv, diagnostic command lines, or the setup manifest.
    subprocess.run(command, check=True, text=True,
                   input=entropy, stdin=subprocess.DEVNULL if entropy is None else None)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> Path:
    requested_output = args.output.expanduser()
    if requested_output.exists() or requested_output.is_symlink():
        raise ValueError(f"output already exists; choose a new --output directory: {requested_output}")
    output = requested_output.resolve()
    if output.exists():
        raise ValueError(f"output already exists; choose a new --output directory: {output}")
    ptau = regular_file(args.ptau, "Powers of Tau input")
    supplied_key = regular_file(args.zkey, "final zkey input") if args.zkey else None
    circuit = regular_file(TOOLS_DIR / "circuits" / f"{STEM}.circom", "circuit source")
    modules = TOOLS_DIR / "node_modules"
    for name, expected in PACKAGES.items():
        package = regular_file(modules / name / "package.json", f"installed {name} package.json")
        version = json.loads(package.read_text(encoding="utf-8"))["version"]
        if version != expected:
            raise ValueError(f"{name} must be {expected}, found {version}; run npm ci --prefix tools/zk")
    cli = regular_file(modules / "snarkjs" / "build" / "cli.cjs", "snarkjs CLI")
    circom = executable(args.circom)
    node = executable(args.node)
    version = subprocess.run([circom, "--version"], check=True, capture_output=True, text=True)
    if not re.search(r"\b2\.2\.3\b", version.stdout + version.stderr):
        raise ValueError(f"Circom {CIRCOM_VERSION} is required")

    # Creating the directory exclusively also rejects a concurrent builder.
    output.mkdir(parents=True, exist_ok=False)
    snarkjs = [node, str(cli)]
    r1cs = output / f"{STEM}.r1cs"
    final_key = output / f"{STEM}_final.zkey"
    verification_key = output / "verification_key.json"
    print("Compiling the scoped accounting circuit", flush=True)
    run([circom, str(circuit), "--r1cs", "--wasm", "--sym", "--O1",
         "-l", str(modules), "-o", str(output)])
    generated = [r1cs, output / f"{STEM}.sym",
                 output / f"{STEM}_js" / f"{STEM}.wasm"]
    for artifact in generated:
        regular_file(artifact, "compiled circuit artifact")
    print("Checking the supplied Powers of Tau transcript", flush=True)
    run([*snarkjs, "powersoftau", "verify", str(ptau)])
    if args.development_setup:
        print("Creating a one-party development phase-2 setup", flush=True)
        initial_key = output / f"{STEM}_0000.zkey"
        run([*snarkjs, "groth16", "setup", str(r1cs), str(ptau), str(initial_key)])
        run([*snarkjs, "zkey", "contribute", str(initial_key), str(final_key),
             "--name=TokenProv local development contribution"], entropy=secrets.token_hex(64) + "\n")
    else:
        # Copy, rather than edit, the externally supplied ceremony artifact.
        assert supplied_key is not None
        shutil.copyfile(supplied_key, final_key)
    regular_file(final_key, "final proving key")
    print("Verifying the final key against the compiled circuit and supplied ptau", flush=True)
    run([*snarkjs, "zkey", "verify", str(r1cs), str(ptau), str(final_key)])
    run([*snarkjs, "zkey", "export", "verificationkey", str(final_key), str(verification_key)])
    regular_file(verification_key, "verification key")
    manifest = {
        "schema_version": 1,
        "setup_mode": "one_party_development" if args.development_setup else "supplied_final_zkey",
        "scope": "prompt/output charge arithmetic, Poseidon opening, request-hash equality",
        "circom_version": CIRCOM_VERSION,
        "npm_packages": PACKAGES,
        "circuit_sha256": sha256(circuit),
        "ptau_sha256": sha256(ptau),
        "artifacts_sha256": {
            path.relative_to(output).as_posix(): sha256(path)
            for path in [*generated, final_key, verification_key]
        },
        "ceremony_note": "Transcript consistency checks do not authenticate its origin or certify a multiparty ceremony.",
    }
    (output / "setup.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Worker assets ready: {output}", flush=True)
    return output


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        build(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"setup failed: {error}", file=sys.stderr)
        print("Any partial output is retained. Inspect it and choose a new --output before retrying.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
