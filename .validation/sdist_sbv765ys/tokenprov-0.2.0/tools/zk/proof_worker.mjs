// One-call adapter for the EXISTING scoped accounting circuit, not inference.
// Commands: node proof_worker.mjs prove|verify /absolute/path/to/zk/build
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve, join } from "node:path";
import os from "node:os";
import { syncBuiltinESMExports } from "node:module";

const [action, directory] = process.argv.slice(2);
if (process.argv.length !== 4 || !["prove", "verify"].includes(action) || !directory) {
  throw new Error("provide prove|verify and build directory");
}
// ffjavascript 0.3.1 sizes its curve pool from os.cpus().length. Bound this
// one-job CLI before loading the libraries, including their named ESM imports.
// This process-local cap does not set machine CPU affinity.
const cpus = os.cpus();
os.cpus = () => cpus.slice(0, 2);
syncBuiltinESMExports();
const { buildPoseidon } = await import("circomlibjs");
const snarkjs = await import("snarkjs");
const root = resolve(directory);
const request = JSON.parse(readFileSync(0, "utf8"));
const { payload, binding } = request;
const receipt = payload.receipt;
const rates = payload.price_policy;
const usage = receipt.usage;
const stable = value => value === null || typeof value !== "object" ? JSON.stringify(value)
  : Array.isArray(value) ? `[${value.map(stable).join(",")}]`
  : `{${Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + stable(value[k])).join(",")}}`;
const hash = (domain, value) => createHash("sha256").update(Buffer.from(`TOKENPROV\0${domain}\0`)).update(stable(value)).digest("hex");
if (hash("deferred-proof-job/v1", payload) !== binding) throw new Error("job binding mismatch");
if (hash("commitment/provider-witness/v1", { value: payload.witness, opening: payload.opening }) !== payload.commitment
    || payload.commitment !== receipt.witness_commitment) throw new Error("receipt-bound witness mismatch");
for (const [field, value] of Object.entries(payload.witness.shared)) {
  if (stable(value) !== stable(receipt[field])) throw new Error(`shared value mismatch: ${field}`);
}
if (rates.version !== receipt.price_version || !receipt.billable) throw new Error("unsupported billing profile");
if (usage.n_cr || usage.n_cw || usage.n_rea) throw new Error("this circuit supports prompt/output accounting only");
for (const number of [usage.n_pf, usage.n_out, rates.n_pf_rate, rates.n_out_rate, receipt.charge_microunits]) {
  if (!Number.isSafeInteger(number) || number < 0) throw new Error("unsafe integer in circuit input");
}
const poseidon = await buildPoseidon();
const field = poseidon.F.p;
const nonce = BigInt("0x" + createHash("sha256").update(payload.opening).digest("hex")) % field;
const values = [BigInt(usage.n_pf), BigInt(usage.n_out), BigInt(rates.n_pf_rate), BigInt(rates.n_out_rate), nonce];
const callHash = BigInt("0x" + hash("scoped-call-binding/v1", receipt)) % field;
const input = {
  promptTokens: values[0].toString(), completionTokens: values[1].toString(),
  promptRate: values[2].toString(), completionRate: values[3].toString(), nonce: nonce.toString(),
  expectedCharge: String(receipt.charge_microunits),
  commitment: poseidon.F.toObject(poseidon(values)).toString(),
  requestHash: callHash.toString(), receiptRequestHash: callHash.toString(),
};
const expected = [input.expectedCharge, input.commitment, input.requestHash, input.receiptRequestHash];
const keyBytes = readFileSync(join(root, "verification_key.json"));
const keyHash = createHash("sha256").update(keyBytes).digest("hex");
const scope = "scoped-prompt-output-accounting; call-field checks in host; no inference proof";
let response;
if (action === "prove") {
  const started = performance.now();
  const { proof, publicSignals } = await snarkjs.groth16.fullProve(input,
    join(root, "tokenprov_accounting_js", "tokenprov_accounting.wasm"), join(root, "tokenprov_accounting_final.zkey"));
  if (stable(publicSignals) !== stable(expected)) throw new Error("unexpected circuit public-signal layout");
  response = { binding, proof, publicSignals, scope,
    verification_key_sha256: keyHash, native_prove_ms: performance.now() - started };
} else {
  const artifact = request.artifact;
  const verified = artifact.binding === binding && artifact.verification_key_sha256 === keyHash
    && stable(artifact.publicSignals) === stable(expected)
    && await snarkjs.groth16.verify(JSON.parse(keyBytes), artifact.publicSignals, artifact.proof);
  response = { verified, binding, scope };
}
// snarkjs retains ffjavascript worker threads. Flush the complete JSON before
// ending this one-job CLI so Python's subprocess does not wait until timeout.
await new Promise((resolve, reject) => process.stdout.write(JSON.stringify(response),
  error => error ? reject(error) : resolve()));
process.exit(0);
