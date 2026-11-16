"""Offline setup tests: exercise validation and key handling without a ceremony."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "zk" / "setup.py"
spec = importlib.util.spec_from_file_location("tokenprov_zk_setup", SCRIPT)
zk_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zk_setup)


def toolchain(tmp_path, monkeypatch):
    tools = tmp_path / "tool dir"
    circuit = tools / "circuits" / "tokenprov_accounting.circom"
    circuit.parent.mkdir(parents=True)
    circuit.write_text("test circuit", encoding="utf-8")
    for name, version in zk_setup.PACKAGES.items():
        package = tools / "node_modules" / name / "package.json"
        package.parent.mkdir(parents=True)
        package.write_text(json.dumps({"version": version}), encoding="utf-8")
    cli = tools / "node_modules" / "snarkjs" / "build" / "cli.cjs"
    cli.parent.mkdir()
    cli.write_text("test cli", encoding="utf-8")
    monkeypatch.setattr(zk_setup, "TOOLS_DIR", tools)
    monkeypatch.setattr(zk_setup, "executable", lambda value: str(tmp_path / value))
    ptau = tmp_path / "input ; transcript.ptau"
    ptau.write_bytes(b"test transcript")
    return tools, ptau


def mock_commands(monkeypatch, *, fail_verification=False):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "circom compiler 2.2.3\n", "")
        if "--r1cs" in command:
            output = Path(command[command.index("-o") + 1])
            (output / "tokenprov_accounting.r1cs").write_bytes(b"r1cs")
            (output / "tokenprov_accounting.sym").write_bytes(b"sym")
            wasm = output / "tokenprov_accounting_js" / "tokenprov_accounting.wasm"
            wasm.parent.mkdir()
            wasm.write_bytes(b"wasm")
        elif command[2:4] in (["groth16", "setup"], ["zkey", "contribute"]):
            # Contribution options follow the output filename.
            Path(command[5] if command[3] == "contribute" else command[-1]).write_bytes(b"key")
        elif command[2:4] == ["zkey", "verify"] and fail_verification:
            raise subprocess.CalledProcessError(1, command)
        elif command[2:5] == ["zkey", "export", "verificationkey"]:
            Path(command[-1]).write_text('{"protocol":"groth16"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(zk_setup.subprocess, "run", fake_run)
    return calls


def test_requires_explicit_setup_mode_without_running_commands(monkeypatch):
    monkeypatch.setattr(zk_setup.subprocess, "run", lambda *a, **k: pytest.fail("executed a setup command"))
    with pytest.raises(SystemExit) as error:
        zk_setup.main(["--ptau", "input.ptau"])
    assert error.value.code == 2


def test_existing_output_is_preserved_before_commands(tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "key"
    sentinel.write_bytes(b"preserved")
    monkeypatch.setattr(zk_setup.subprocess, "run", lambda *a, **k: pytest.fail("executed a setup command"))
    assert zk_setup.main(["--ptau", "missing.ptau", "--development-setup", "--output", str(output)]) == 1
    assert sentinel.read_bytes() == b"preserved"


def test_development_build_uses_argument_arrays_and_private_entropy(tmp_path, monkeypatch):
    _, ptau = toolchain(tmp_path, monkeypatch)
    calls = mock_commands(monkeypatch)
    output = tmp_path / "new output"
    assert zk_setup.main(["--ptau", str(ptau), "--development-setup", "--output", str(output)]) == 0
    contribution, options = next((cmd, opts) for cmd, opts in calls if cmd[2:4] == ["zkey", "contribute"])
    assert len(options["input"].strip()) == 128
    assert options["input"].strip() not in " ".join(contribution)
    assert all(isinstance(command, list) and not options.get("shell") for command, options in calls)
    assert any(str(ptau.resolve()) in command for command, _ in calls)
    manifest = json.loads((output / "setup.json").read_text(encoding="utf-8"))
    assert manifest["setup_mode"] == "one_party_development"
    assert "tokenprov_accounting_js/tokenprov_accounting.wasm" in manifest["artifacts_sha256"]
    assert options["input"].strip() not in (output / "setup.json").read_text(encoding="utf-8")


def test_external_key_failure_stops_before_export(tmp_path, monkeypatch):
    _, ptau = toolchain(tmp_path, monkeypatch)
    supplied_key = tmp_path / "external.zkey"
    supplied_key.write_bytes(b"external ceremony key")
    calls = mock_commands(monkeypatch, fail_verification=True)
    output = tmp_path / "external build"
    assert zk_setup.main(["--ptau", str(ptau), "--zkey", str(supplied_key), "--output", str(output)]) == 1
    assert supplied_key.read_bytes() == b"external ceremony key"
    assert not any(command[2:4] in (["groth16", "setup"], ["zkey", "contribute"], ["zkey", "export"])
                   for command, _ in calls)
    assert not (output / "verification_key.json").exists()
    assert not (output / "setup.json").exists()


def test_external_key_is_copied_and_verified_without_local_setup(tmp_path, monkeypatch):
    _, ptau = toolchain(tmp_path, monkeypatch)
    supplied_key = tmp_path / "external.zkey"
    supplied_key.write_bytes(b"external ceremony key")
    calls = mock_commands(monkeypatch)
    output = tmp_path / "external build"
    assert zk_setup.main(["--ptau", str(ptau), "--zkey", str(supplied_key), "--output", str(output)]) == 0
    assert (output / "tokenprov_accounting_final.zkey").read_bytes() == supplied_key.read_bytes()
    assert not any(command[2:4] in (["groth16", "setup"], ["zkey", "contribute"]) for command, _ in calls)
    manifest = json.loads((output / "setup.json").read_text(encoding="utf-8"))
    assert manifest["setup_mode"] == "supplied_final_zkey"


def test_wrong_compiler_version_does_not_create_output(tmp_path, monkeypatch):
    _, ptau = toolchain(tmp_path, monkeypatch)
    monkeypatch.setattr(zk_setup.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, "circom compiler 2.2.30", ""))
    output = tmp_path / "wrong version"
    assert zk_setup.main(["--ptau", str(ptau), "--development-setup", "--output", str(output)]) == 1
    assert not output.exists()
