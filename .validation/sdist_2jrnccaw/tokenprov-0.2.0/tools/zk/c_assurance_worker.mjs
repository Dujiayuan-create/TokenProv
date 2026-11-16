// Persistent benchmark adapter for the scoped Groth16 accounting circuit.
// The SHA-256 witness/shared-field checks remain host checks, as in proof_worker.mjs.
import os from 'node:os';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import readline from 'node:readline';
import { syncBuiltinESMExports } from 'node:module';
if (process.argv.length !== 3) throw Error('usage: node c_assurance_worker.mjs BUILD_DIRECTORY');
const cpus = os.cpus();
// ffjavascript 0.3.1 sizes its curve-worker pool from os.cpus().length.
// Apply the cap before loading circomlibjs/snarkjs, including named ESM exports.
// This is a process-local benchmark bound, not a machine CPU-affinity setting.
os.cpus = () => cpus.slice(0, 2);
syncBuiltinESMExports();
const { buildPoseidon } = await import('circomlibjs');
const snarkjs = await import('snarkjs');
const build = resolve(process.argv[2]);
const poseidon = await buildPoseidon();
const keyBytes = readFileSync(join(build, 'verification_key.json'));
const verificationKey = JSON.parse(keyBytes);
const keyHash = createHash('sha256').update(keyBytes).digest('hex');
const wasm = join(build, 'tokenprov_accounting_js/tokenprov_accounting.wasm');
const zkey = join(build, 'tokenprov_accounting_final.zkey');
const stable = value => value === null || typeof value !== 'object' ? JSON.stringify(value)
  : Array.isArray(value) ? `[${value.map(stable).join(',')}]`
  : `{${Object.keys(value).sort().map(k => JSON.stringify(k)+':'+stable(value[k])).join(',')}}`;
const hash = (domain, value) => createHash('sha256').update(Buffer.from(`TOKENPROV\0${domain}\0`)).update(stable(value)).digest('hex');
const prepared = new Map();
const elapsed = start => Number(process.hrtime.bigint() - start) / 1e6;

function prepare(payload, binding) {
  const start = process.hrtime.bigint();
  if (hash('deferred-proof-job/v1', payload) !== binding) throw Error('job binding mismatch');
  const receipt = payload.receipt, rates = payload.price_policy, usage = receipt.usage;
  if (hash('commitment/provider-witness/v1', {value: payload.witness, opening: payload.opening}) !== payload.commitment
      || payload.commitment !== receipt.witness_commitment) throw Error('receipt-bound witness mismatch');
  for (const [field, value] of Object.entries(payload.witness.shared)) {
    if (stable(value) !== stable(receipt[field])) throw Error('shared value mismatch: '+field);
  }
  if (rates.version !== receipt.price_version || !receipt.billable) throw Error('unsupported billing profile');
  if (usage.n_cr || usage.n_cw || usage.n_rea) throw Error('unsupported usage profile');
  for (const number of [usage.n_pf, usage.n_out, rates.n_pf_rate, rates.n_out_rate, receipt.charge_microunits]) {
    if (!Number.isSafeInteger(number) || number < 0) throw Error('unsafe integer');
  }
  const host_check_ms = elapsed(start);
  const t = process.hrtime.bigint();
  const field = poseidon.F.p;
  const nonce = BigInt('0x'+createHash('sha256').update(payload.opening).digest('hex')) % field;
  const values = [BigInt(usage.n_pf),BigInt(usage.n_out),BigInt(rates.n_pf_rate),BigInt(rates.n_out_rate),nonce];
  const callHash = BigInt('0x'+hash('scoped-call-binding/v1',receipt)) % field;
  const input = {promptTokens:values[0].toString(),completionTokens:values[1].toString(),
    promptRate:values[2].toString(),completionRate:values[3].toString(),nonce:nonce.toString(),
    expectedCharge:String(receipt.charge_microunits),commitment:poseidon.F.toObject(poseidon(values)).toString(),
    requestHash:callHash.toString(),receiptRequestHash:callHash.toString()};
  const value = {input, expected:[input.expectedCharge,input.commitment,input.requestHash,input.receiptRequestHash]};
  prepared.set(binding,value);
  return {binding,host_check_ms,circuit_input_ms:elapsed(t),prepare_ms:elapsed(start)};
}

async function prove(binding) {
  const value = prepared.get(binding);
  if (!value) throw Error('missing prepared witness');
  const start = process.hrtime.bigint(), wtns = {type:'mem'};
  await snarkjs.wtns.calculate(value.input,wasm,wtns);
  const circuit_witness_ms = elapsed(start), t = process.hrtime.bigint();
  const {proof,publicSignals} = await snarkjs.groth16.prove(zkey,wtns);
  const groth16_prove_ms = elapsed(t);
  if (stable(publicSignals) !== stable(value.expected)) throw Error('unexpected public signals');
  const artifact = {binding,proof,publicSignals,verification_key_sha256:keyHash,
    scope:'scoped-prompt-output-accounting; call-field checks in host; no inference proof'};
  return {artifact,circuit_witness_ms,groth16_prove_ms,full_prove_ms:elapsed(start),
    proof_bytes:Buffer.byteLength(JSON.stringify(proof)),
    artifact_bytes:Buffer.byteLength(JSON.stringify(artifact))};
}

async function verify(binding,artifact) {
  const start = process.hrtime.bigint(), value = prepared.get(binding);
  if (!value) throw Error('missing prepared witness');
  const verified = artifact.binding === binding && artifact.verification_key_sha256 === keyHash
    && stable(artifact.publicSignals) === stable(value.expected)
    && await snarkjs.groth16.verify(verificationKey,artifact.publicSignals,artifact.proof);
  return {verified,verify_ms:elapsed(start)};
}

process.stdout.write(JSON.stringify({ready:true,node:process.version,curve_workers:2,verification_key_sha256:keyHash})+'\n');
for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  const start = process.hrtime.bigint();
  try {
    const request = JSON.parse(line);
    let result;
    if (request.action === 'prepare') result = prepare(request.payload,request.binding);
    else if (request.action === 'prove') result = await prove(request.binding);
    else if (request.action === 'verify') result = await verify(request.binding,request.artifact);
    else if (request.action === 'full') {
      const p = prepare(request.payload,request.binding);
      const proof = await prove(request.binding);
      result = {...p,...proof,...await verify(request.binding,proof.artifact)};
      if (!result.verified) throw Error('honest proof rejected');
    } else if (request.action === 'clear') { prepared.clear(); result = {cleared:true}; }
    else if (request.action === 'shutdown') {
      process.stdout.write(JSON.stringify({ok:true,shutdown:true})+'\n');
      process.exit(0);
    } else throw Error('unknown action');
    process.stdout.write(JSON.stringify({ok:true,...result,worker_command_ms:elapsed(start)})+'\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ok:false,error:String(error),worker_command_ms:elapsed(start)})+'\n');
  }
}
process.exit(0);
