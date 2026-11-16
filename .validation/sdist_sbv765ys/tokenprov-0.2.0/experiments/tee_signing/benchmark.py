"""Real CNG VBS-isolated signing experiment using retained TokenProv receipts.

The private-key operations run through Windows' VBS key-protection provider.
Receipt preparation/accounting stay in Python. No attested inference is claimed.
Ephemeral unnamed keys only; no certificate/boot/security configuration changes.
"""
from __future__ import annotations
import argparse
import copy
import ctypes as C
from ctypes import wintypes as W
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import time
import traceback

DOMAIN = b'TOKENPROV\x00vbs-receipt-authentication-component/v1\x00'
REQUIRE_VBS = 0x20000
H, U, S = C.c_size_t, C.c_uint32, C.c_long

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')

def content_hash(domain, value):
    return hashlib.sha256(b'TOKENPROV\x00' + domain.encode() + b'\x00' + canonical(value)).hexdigest()

def longpath(path):
    return Path('\\\\?\\' + str(path.resolve())) if os.name == 'nt' else path

def status_hex(value):
    return f'0x{value & 0xffffffff:08x}'

def checked(status, action):
    if status != 0:
        raise RuntimeError(action + ': ' + status_hex(status))

def buffer(data):
    return C.create_string_buffer(data, len(data))

def describe(values):
    ordered = sorted(values)
    def percentile(p):
        pos = (len(ordered) - 1) * p
        i = int(pos)
        return ordered[i] + (ordered[min(i + 1, len(ordered) - 1)] - ordered[i]) * (pos - i)
    return {'n': len(values), 'mean': statistics.fmean(values), 'sample_sd': statistics.stdev(values) if len(values) > 1 else None,
            'median': statistics.median(values), 'p95': percentile(.95), 'min': ordered[0], 'max': ordered[-1]}

class Api:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('VBS-isolated signing requires Windows with VBS key protection enabled')
        self.n = C.WinDLL('ncrypt')
        self.b = C.WinDLL('bcrypt')
        signatures = {
            'NCryptOpenStorageProvider': [C.POINTER(H), W.LPCWSTR, U],
            'NCryptCreatePersistedKey': [H, C.POINTER(H), W.LPCWSTR, W.LPCWSTR, U, U],
            'NCryptFinalizeKey': [H, U],
            'NCryptSetProperty': [H, W.LPCWSTR, C.c_void_p, U, U],
            'NCryptGetProperty': [H, W.LPCWSTR, C.c_void_p, U, C.POINTER(U), U],
            'NCryptExportKey': [H, H, W.LPCWSTR, C.c_void_p, C.c_void_p, U, C.POINTER(U), U],
            'NCryptSignHash': [H, C.c_void_p, C.c_void_p, U, C.c_void_p, U, C.POINTER(U), U],
            'NCryptFreeObject': [H],
        }
        for name, args in signatures.items():
            f = getattr(self.n, name); f.argtypes = args; f.restype = S
        signatures = {
            'BCryptOpenAlgorithmProvider': [C.POINTER(H), W.LPCWSTR, W.LPCWSTR, U],
            'BCryptImportKeyPair': [H, H, W.LPCWSTR, C.POINTER(H), C.c_void_p, U, U],
            'BCryptVerifySignature': [H, C.c_void_p, C.c_void_p, U, C.c_void_p, U, U],
            'BCryptDestroyKey': [H],
            'BCryptCloseAlgorithmProvider': [H, U],
        }
        for name, args in signatures.items():
            f = getattr(self.b, name); f.argtypes = args; f.restype = S

class SigningSession:
    def __init__(self, api, mode):
        self.api, self.mode = api, mode
        self.provider, self.key, self.verify_algorithm, self.verify_key = H(), H(), H(), H()
        try:
            started = time.perf_counter_ns()
            checked(api.n.NCryptOpenStorageProvider(C.byref(self.provider), 'Microsoft Software Key Storage Provider', 0), 'open provider')
            self.provider_open_ns = time.perf_counter_ns() - started
            started = time.perf_counter_ns()
            checked(api.n.NCryptCreatePersistedKey(self.provider, C.byref(self.key), 'ECDSA_P256', None, 0, REQUIRE_VBS if mode == 'require_vbs' else 0), 'create ephemeral key')
            export_policy = U(0)
            checked(api.n.NCryptSetProperty(self.key, 'Export Policy', C.byref(export_policy), C.sizeof(export_policy), 0), 'set nonexportable policy')
            signing_policy = U(2)
            checked(api.n.NCryptSetProperty(self.key, 'Key Usage', C.byref(signing_policy), C.sizeof(signing_policy), 0), 'set signing usage')
            checked(api.n.NCryptFinalizeKey(self.key, 0), 'finalize key')
            self.key_setup_ns = time.perf_counter_ns() - started
            self.properties = {name: self.get_property(name) for name in ('Virtual Iso', 'Export Policy', 'Key Usage')}
            expected = 1 if mode == 'require_vbs' else 0
            if self.properties['Virtual Iso']['dword'] != expected:
                raise RuntimeError('Isolation property does not match mode; software fallback forbidden')
            size = U(); out = (C.c_ubyte * 256)()
            checked(api.n.NCryptExportKey(self.key, H(), 'ECCPUBLICBLOB', None, out, len(out), C.byref(size), 0), 'export public key')
            self.public_key = bytes(out[:size.value])
            if len(self.public_key) != 72 or self.public_key[:4] != b'ECS1':
                raise RuntimeError('Unexpected P-256 public key format')
            checked(api.b.BCryptOpenAlgorithmProvider(C.byref(self.verify_algorithm), 'ECDSA_P256', None, 0), 'open verification algorithm')
            public = buffer(self.public_key)
            checked(api.b.BCryptImportKeyPair(self.verify_algorithm, H(), 'ECCPUBLICBLOB', C.byref(self.verify_key), public, len(self.public_key), 0), 'import public verification key')
        except Exception:
            self.close()
            raise

    def get_property(self, name):
        value, size = U(), U()
        checked(self.api.n.NCryptGetProperty(self.key, name, C.byref(value), C.sizeof(value), C.byref(size), 0), 'get key property ' + name)
        if size.value != 4:
            raise RuntimeError('Unexpected property size')
        return {'status': '0x00000000', 'bytes': size.value, 'dword': value.value}

    def sign(self, digest):
        inp, out, size = buffer(digest), (C.c_ubyte * 64)(), U()
        started = time.perf_counter_ns()
        status = self.api.n.NCryptSignHash(self.key, None, inp, len(digest), out, len(out), C.byref(size), 0)
        elapsed = time.perf_counter_ns() - started
        checked(status, 'sign hash')
        if size.value != 64:
            raise RuntimeError('Unexpected ECDSA signature size')
        return bytes(out), elapsed

    def verify(self, digest, signature):
        inp, sig = buffer(digest), buffer(signature)
        started = time.perf_counter_ns()
        status = self.api.b.BCryptVerifySignature(self.verify_key, None, inp, len(digest), sig, len(signature), 0)
        return status, time.perf_counter_ns() - started

    def private_export_check(self):
        # Never log or persist the output buffer, including unexpected success.
        out, size = (C.c_ubyte * 1024)(), U()
        try:
            status = self.api.n.NCryptExportKey(self.key, H(), 'ECCPRIVATEBLOB', None, out, len(out), C.byref(size), 0)
            return {'status': status_hex(status), 'denied': status != 0}
        finally:
            C.memset(C.addressof(out), 0, C.sizeof(out))

    def close(self):
        for handle, function in ((self.verify_key, self.api.b.BCryptDestroyKey), (self.verify_algorithm, lambda h: self.api.b.BCryptCloseAlgorithmProvider(h, 0)), (self.key, self.api.n.NCryptFreeObject), (self.provider, self.api.n.NCryptFreeObject)):
            if handle.value:
                function(handle); handle.value = 0

def load_receipts(source, limit=1024):
    source = Path(source).resolve()
    requests = [json.loads(line) for line in (source / 'requests.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    eligible = {r['request_id'] for r in requests if r['system'] == 'TokenProv-Full' and not r['warmup'] and r['success'] and r.get('measurement_type') == 'REAL_MODEL_SERVING'}
    references = [json.loads(line) for line in (source / 'witnesses.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    selected = [r for r in references if r['system'] == 'TokenProv-Full' and not r['warmup'] and r['request_id'] in eligible]
    cache, result, seen = {}, [], set()
    for ref in selected:
        path = (source / ref['file']).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Evidence path escaped source run')
        if path not in cache:
            bundle = json.loads(longpath(path).read_text(encoding='utf-8'))
            expected = bundle.pop('content_hash')
            if expected != ref['bundle_content_hash'] or content_hash('evidence-object/v1', bundle) != expected:
                raise RuntimeError('Source bundle hash mismatch')
            cache[path] = (bundle, expected)
        bundle, expected = cache[path]
        if ref['bundle_content_hash'] != expected:
            raise RuntimeError('Inconsistent cached bundle hash')
        witness = bundle['payload']['provider_witnesses'][ref['witness_index']]
        receipt, price = witness['receipt'], witness['price_policy']
        if receipt['request_id'] != ref['request_id'] or receipt['call_id'] != ref['call_id']:
            raise RuntimeError('Source witness identity mismatch')
        identity = (receipt['request_id'], receipt['call_id'])
        if identity in seen:
            raise RuntimeError('Duplicate source receipt')
        seen.add(identity)
        commitment = content_hash('commitment/provider-witness/v1', {'value': witness['witness'], 'opening': witness['opening']})
        if commitment != witness['commitment'] or commitment != ref['commitment'] or commitment != receipt['witness_commitment']:
            raise RuntimeError('Receipt-bound witness commitment mismatch')
        if any(value != receipt.get(field) for field, value in witness['witness']['shared'].items()):
            raise RuntimeError('Receipt shared-field mismatch')
        usage = receipt['usage']
        if usage['n_cr'] or usage['n_cw'] or usage['n_rea']:
            raise RuntimeError('Only prompt/output accounting is included')
        if receipt['charge_microunits'] != usage['n_pf'] * price['n_pf_rate'] + usage['n_out'] * price['n_out_rate']:
            raise RuntimeError('Source charge does not match independently recomputed arithmetic')
        result.append({'receipt': receipt, 'price_policy': price, 'source_bundle_hash': ref['bundle_content_hash']})
    result.sort(key=lambda x: (x['receipt']['request_id'], x['receipt']['call_id']))
    if limit < 1 or len(result) < limit:
        raise ValueError(f'Requested {limit} real-serving receipts, found {len(result)}')
    return result[:limit]

def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--source-run', required=True, type=Path, help='completed real serving run with evidence bundles')
    parser.add_argument('--output-root', type=Path, default=Path('results/tee_signing'), help='parent for a fresh timestamped run directory')
    parser.add_argument('--receipts', type=int, default=1024, help='number of actual source receipts per session')
    parser.add_argument('--seed', type=int, default=42)
    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()
    if args.rounds < 1 or args.warmups < 0 or args.receipts < 1:
        parser.error('Invalid repetition or warmup count')
    if os.name != 'nt':
        parser.error('This benchmark requires Windows VBS key protection; reports can be verified on any platform')
    source = args.source_run.resolve()
    output_root = args.output_root.resolve()
    if output_root.is_relative_to(source):
        parser.error('--output-root must be outside the immutable source run')
    fixtures = load_receipts(source, args.receipts)
    out = output_root / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    rows, tests, sessions, all_measurements = [], [], [], []
    def emit(row):
        rows.append(row)
        with (out / 'measurements.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    def test(mode, round_number, name, passed, **details):
        row = {'type': 'correctness', 'mode': mode, 'round': round_number, 'case': name, 'passed': bool(passed), **details}
        tests.append(row); emit(row)
        if not passed:
            raise RuntimeError('Correctness case failed: ' + name)
    status, error = 'incomplete', None
    try:
        cohort_sha256 = hashlib.sha256(canonical(fixtures)).hexdigest()
        (out / 'replayed_receipts.json').write_bytes(canonical(fixtures))
        emit({'type': 'configuration', 'scope': 'VBS-isolated private-key receipt signing; accounting/serialization in host; no attested inference or remote attestation',
              'rounds': args.rounds, 'warmups_per_session': args.warmups, 'receipts_per_session': len(fixtures),
              'source_run': str(source), 'source_requests': len({v['receipt']['request_id'] for v in fixtures}),
              'seed': args.seed, 'cohort_sha256': cohort_sha256,
              'hardware': {'platform': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count()},
              'algorithm': 'ECDSA-P256/SHA256', 'key_lifetime': 'ephemeral unnamed; private key not persisted',
              'require_vbs_flag': REQUIRE_VBS, 'fallback': False, 'pid': os.getpid()})
        api = Api()
        for round_number in range(1, args.rounds + 1):
            order = ['software_ksp', 'require_vbs'] if round_number % 2 else ['require_vbs', 'software_ksp']
            indices = list(range(len(fixtures))); random.Random(args.seed + round_number - 1).shuffle(indices)
            for mode in order:
                session = SigningSession(api, mode)
                try:
                    session_record = {'type': 'session', 'round': round_number, 'mode': mode,
                        'provider_open_ns': session.provider_open_ns, 'key_setup_ns': session.key_setup_ns,
                        'properties': session.properties, 'public_key_hex': session.public_key.hex()}
                    sessions.append(session_record); emit(session_record)
                    private_export = session.private_export_check()
                    test(mode, round_number, 'private_export_denied', private_export['denied'], **private_export)
                    nonce = hashlib.sha256(f'TokenProv component round {round_number}'.encode()).hexdigest()
                    example = None
                    phases = [('first_operation', [indices[0]]), ('warmup', [indices[i % len(indices)] for i in range(args.warmups)]), ('measured', indices)]
                    for phase, members in phases:
                        for sequence, index in enumerate(members):
                            envelope = {'version': 1, 'session_challenge': nonce, 'sequence': sequence + 1,
                                        'source_bundle_hash': fixtures[index]['source_bundle_hash'], 'receipt': fixtures[index]['receipt']}
                            started = time.perf_counter_ns()
                            serialized = canonical(envelope)
                            digest = hashlib.sha256(DOMAIN + serialized).digest()
                            prepare_ns = time.perf_counter_ns() - started
                            signature, sign_ns = session.sign(digest)
                            auth_ns = time.perf_counter_ns() - started
                            verify_status, verify_ns = session.verify(digest, signature)
                            row = {'type': 'operation', 'round': round_number, 'mode': mode, 'phase': phase,
                                'index': index, 'request_id': envelope['receipt']['request_id'], 'call_id': envelope['receipt']['call_id'],
                                'host_serialize_hash_ns': prepare_ns, 'sign_api_ns': sign_ns,
                                'receipt_authentication_ns': auth_ns, 'external_verify_ns': verify_ns,
                                'signature_bytes': len(signature), 'serialized_receipt_bytes': len(serialized),
                                'verify_status': status_hex(verify_status), 'passed': verify_status == 0}
                            emit(row)
                            if phase == 'measured': all_measurements.append(row)
                            if verify_status != 0: raise RuntimeError('External signature verification failed')
                            if phase == 'measured' and example is None:
                                example = {'envelope': envelope, 'digest_hex': digest.hex(), 'signature_hex': signature.hex(),
                                           'public_key_hex': session.public_key.hex(), 'vbs_isolated': mode == 'require_vbs'}
                    (out / f'example-{mode}-round{round_number}.json').write_text(json.dumps(example, indent=2), encoding='utf-8')
                    original = example['envelope']; original_signature = bytes.fromhex(example['signature_hex'])
                    mutations = [('changed_charge', ('receipt', 'charge_microunits')), ('changed_request', ('receipt', 'request_id')),
                                 ('changed_call', ('receipt', 'call_id')), ('changed_output_commitment', ('receipt', 'output_hash')),
                                 ('changed_token_count', ('receipt', 'usage', 'n_out')), ('changed_challenge', ('session_challenge',)), ('changed_sequence', ('sequence',))]
                    for name, fields in mutations:
                        altered = copy.deepcopy(original); leaf = altered
                        for field in fields[:-1]: leaf = leaf[field]
                        old = leaf[fields[-1]]; leaf[fields[-1]] = old + 1 if type(old) is int else old + '-tampered'
                        changed_digest = hashlib.sha256(DOMAIN + canonical(altered)).digest()
                        native, _ = session.verify(changed_digest, original_signature)
                        test(mode, round_number, name + '_old_signature_rejected', native != 0, verify_status=status_hex(native))
                    changed_sig = bytearray(original_signature); changed_sig[0] ^= 1
                    native, _ = session.verify(bytes.fromhex(example['digest_hex']), bytes(changed_sig))
                    test(mode, round_number, 'tampered_signature_rejected', native != 0, verify_status=status_hex(native))
                    # Explicit boundary control: isolated signing does not validate host accounting.
                    changed = copy.deepcopy(original); changed['receipt']['charge_microunits'] += 1
                    changed_digest = hashlib.sha256(DOMAIN + canonical(changed)).digest()
                    fresh_signature, _ = session.sign(changed_digest); native, _ = session.verify(changed_digest, fresh_signature)
                    test(mode, round_number, 'scope_control_host_modified_charge_can_be_freshly_signed', native == 0,
                         interpretation='Key isolation protects key operations; caller-supplied accounting is outside the protected boundary.')
                finally:
                    session.close()
        status = 'completed'
    except Exception as exc:
        error = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
        emit({'type': 'fatal_error', **error})
    finally:
        round_stats = []
        for session in sessions:
            measured = [v for v in all_measurements if v['mode'] == session['mode'] and v['round'] == session['round']]
            if measured:
                round_stats.append({'mode': session['mode'], 'round': session['round'], 'operations': len(measured),
                    **{field + '_ms': describe([v[field] / 1e6 for v in measured]) for field in ('sign_api_ns', 'receipt_authentication_ns', 'external_verify_ns')}})
        across_rounds = []
        if status == 'completed':
            for mode in ('software_ksp', 'require_vbs'):
                subset = [v for v in round_stats if v['mode'] == mode]
                across_rounds.append({'mode': mode,
                    **{field: describe([v[field]['mean'] for v in subset]) for field in ('sign_api_ns_ms', 'receipt_authentication_ns_ms', 'external_verify_ns_ms')},
                    'key_setup_ms': describe([v['key_setup_ns'] / 1e6 for v in sessions if v['mode'] == mode])})
        summary = {'status': status, 'completed_real_vbs_key_signing': status == 'completed', 'error': error,
                   'round_statistics': round_stats, 'across_round_means': across_rounds,
                   'measured_operations': len(all_measurements), 'correctness_cases': len(tests),
                   'correctness_failures': sum(not t['passed'] for t in tests),
                   'isolation_evidence': [{'mode': v['mode'], 'round': v['round'], 'properties': v['properties']} for v in sessions],
                   'scope': 'VBS-isolated ECDSA private-key operations on replayed real TokenProv receipts; host-side accounting; no custom enclave execution, inference attestation, or remote attestation',
                   'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'system_configuration_changes': [], 'output': str(out)}
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        emit({'type': 'completion', 'success': status == 'completed'})
        print(json.dumps({'status': status, 'operations': len(all_measurements), 'correctness_cases': len(tests), 'output': str(out), 'error': error['message'] if error else None}, ensure_ascii=True))
    return 0 if status == 'completed' else 1

if __name__ == '__main__':
    raise SystemExit(main())
