"""Portable interface tests; temporary examples are test data, never measurements."""
import importlib.util
import json
from pathlib import Path
import struct

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location('public_' + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assurance = load('experiments/run_c_assurance_server.py')
assurance_report = load('experiments/report_c_assurance.py')
signing = load('experiments/tee_signing/benchmark.py')
signing_report = load('experiments/tee_signing/report.py')


def test_assurance_plan_counts_follow_rounds_and_smoke():
    args = assurance.make_parser().parse_args([
        '--source-run', 'source', '--build-dir', 'build', '--output', 'new',
        '--rounds', '5', '--seed', '17'])
    plan = assurance.build_plan(args, 137)
    assert plan['seeds'] == [17, 18, 19, 20, 21]
    assert plan['source_requests'] == 137
    counts = assurance_report.expected_counts(plan)
    assert counts['C1-warm'] == 160
    assert counts['C2-call'] == 600
    assert counts['C3-job'] == 7640
    args.smoke = args.skip_legacy = True
    smoke = assurance.build_plan(args, 100)
    assert smoke['rounds'] == 1 and smoke['seeds'] == [17]
    assert smoke['formal'] is False
    assert assurance_report.expected_counts(smoke) == {
        'C1-warm': 2, 'C1-legacy-command': 0, 'C2-call': 2, 'C2-request': 2,
        'C3-job': 8, 'warmup': 6, 'negative-control': 4}


def test_assurance_statistics_use_round_means():
    rows = [{'mode': 'warm', 'round': 1, 'cost': 1.0},
            {'mode': 'warm', 'round': 2, 'cost': 3.0}]
    result = assurance_report.summarize(rows, ['mode'])[0]
    assert result['rounds'] == 2
    assert result['cost']['mean'] == 2
    assert result['cost']['sample_sd'] == pytest.approx(2 ** .5)


def source_run(path, count=100, calls=8, measurement_type='REAL_MODEL_SERVING'):
    path.mkdir()
    requests, refs = [], []
    for index in range(count):
        request_id = f'request-{index:03d}'
        requests.append({'request_id': request_id, 'system': 'TokenProv-Full',
                         'warmup': False, 'success': True, 'measurement_type': measurement_type})
        payloads = []
        for call in range(calls):
            call_id = f'call-{call}'
            witness = {'shared': {'request_id': request_id, 'call_id': call_id}}
            opening = f'opening-{index}-{call}'
            commitment = assurance.digest('commitment/provider-witness/v1', {'value': witness, 'opening': opening})
            receipt = {'request_id': request_id, 'call_id': call_id, 'witness_commitment': commitment,
                       'usage': {'n_pf': 2, 'n_out': 3, 'n_cr': 0, 'n_cw': 0, 'n_rea': 0},
                       'charge_microunits': 8}
            payloads.append({'request_id': request_id, 'call_id': call_id, 'witness': witness,
                             'opening': opening, 'commitment': commitment, 'receipt': receipt,
                             'price_policy': {'n_pf_rate': 1, 'n_out_rate': 2}})
        bundle = {'payload': {'provider_witnesses': payloads}}
        bundle_hash = assurance.digest('evidence-object/v1', bundle)
        bundle['content_hash'] = bundle_hash
        filename = f'bundle-{index}.json'
        (path / filename).write_text(json.dumps(bundle), encoding='utf-8')
        for call, payload in enumerate(payloads):
            refs.append({'request_id': request_id, 'call_id': payload['call_id'], 'system': 'TokenProv-Full',
                         'warmup': False, 'file': filename, 'bundle_content_hash': bundle_hash,
                         'witness_index': call, 'commitment': payload['commitment']})
    for filename, rows in [('requests.jsonl', requests), ('witnesses.jsonl', refs)]:
        (path / filename).write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    return refs


def test_replay_loaders_accept_current_layout_and_reject_path_escape(tmp_path):
    source = tmp_path / 'source'
    refs = source_run(source, count=101)
    cohort, manifest = assurance.source_cohort(source)
    assert len(cohort) == len(manifest) == 101
    assert len(signing.load_receipts(source, 9)) == 9
    refs[0]['file'] = '../outside.json'
    (source / 'witnesses.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in refs), encoding='utf-8')
    with pytest.raises(ValueError, match='escaped'):
        assurance.source_cohort(source)
    with pytest.raises(ValueError, match='escaped'):
        signing.load_receipts(source, 9)


@pytest.mark.parametrize('count,calls,kind', [(99, 8, 'REAL_MODEL_SERVING'),
                                           (100, 7, 'REAL_MODEL_SERVING'),
                                           (100, 8, 'PROTOCOL_MICROBENCHMARK')])
def test_assurance_rejects_incomplete_or_fixture_cohorts(tmp_path, count, calls, kind):
    source = tmp_path / 'source'
    source_run(source, count=count, calls=calls, measurement_type=kind)
    with pytest.raises(ValueError):
        assurance.source_cohort(source)


def example(private_key, envelope, isolated):
    digest = signing_report.hashlib.sha256(signing_report.DOMAIN + signing_report.canonical(envelope)).digest()
    signature = private_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    r, s = utils.decode_dss_signature(signature)
    numbers = private_key.public_key().public_numbers()
    public = struct.pack('<4sI', b'ECS1', 32) + numbers.x.to_bytes(32, 'big') + numbers.y.to_bytes(32, 'big')
    return {'envelope': envelope, 'digest_hex': digest.hex(),
            'signature_hex': (r.to_bytes(32, 'big') + s.to_bytes(32, 'big')).hex(),
            'public_key_hex': public.hex(), 'vbs_isolated': isolated}


def test_saved_cng_signature_is_verified_without_windows(tmp_path):
    private_key = ec.generate_private_key(ec.SECP256R1())
    saved = example(private_key, {'receipt': {'request_id': 'r', 'call_id': 'c'}}, True)
    path = tmp_path / 'example.json'
    path.write_text(json.dumps(saved), encoding='utf-8')
    assert signing_report.verify_example(path)['verified']
    saved['signature_hex'] = '00' * 64
    path.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises((InvalidSignature, ValueError)):
        signing_report.verify_example(path)


def test_signing_report_counts_derive_from_configuration(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    fixtures = [{'receipt': {'request_id': 'r', 'call_id': str(index)}} for index in range(3)]
    config = {'type': 'configuration', 'rounds': 2, 'receipts_per_session': 3,
              'warmups_per_session': 0, 'cohort_sha256': signing_report.hashlib.sha256(signing_report.canonical(fixtures)).hexdigest()}
    raw, statistics_rows = [config], []
    for mode in ('software_ksp', 'require_vbs'):
        for round_number in (1, 2):
            saved = example(ec.generate_private_key(ec.SECP256R1()),
                            {'receipt': {'request_id': 'r', 'call_id': '0'}}, mode == 'require_vbs')
            (run / f'example-{mode}-round{round_number}.json').write_text(json.dumps(saved), encoding='utf-8')
            raw.append({'type': 'session', 'mode': mode, 'round': round_number,
                        'properties': {'Virtual Iso': {'dword': int(mode == 'require_vbs')}, 'Export Policy': {'dword': 0}},
                        'key_setup_ns': 100, 'public_key_hex': saved['public_key_hex']})
            for index in range(3):
                raw.append({'type': 'operation', 'mode': mode, 'round': round_number, 'phase': 'measured',
                            'index': index, 'passed': True, 'verify_status': '0x00000000',
                            'sign_api_ns': 1000, 'receipt_authentication_ns': 2000, 'external_verify_ns': 3000})
            for name in ['private_export_denied', 'scope_control_host_modified_charge_can_be_freshly_signed'] + [f'case-{i}_rejected' for i in range(8)]:
                raw.append({'type': 'correctness', 'mode': mode, 'round': round_number, 'case': name, 'passed': True})
            statistics_rows.append({'mode': mode, 'round': round_number,
                                    'sign_api_ns_ms': {'mean': .001}, 'receipt_authentication_ns_ms': {'mean': .002},
                                    'external_verify_ns_ms': {'mean': .003}})
    raw.append({'type': 'completion', 'success': True})
    (run / 'replayed_receipts.json').write_text(json.dumps(fixtures), encoding='utf-8')
    (run / 'measurements.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in raw), encoding='utf-8')
    (run / 'summary.json').write_text(json.dumps({'status': 'completed', 'correctness_failures': 0,
                                                'round_statistics': statistics_rows}), encoding='utf-8')
    result = signing_report.main(run)
    assert result['rounds'] == 2 and result['measured_operations'] == 12
    assert result['tampered_receipt_or_signature_rejections'] == 32
    assert result['private_export_denials'] == result['accepted_scope_controls'] == 4
    with pytest.raises(FileExistsError):
        signing_report.main(run)


def test_signing_cli_portable_defaults():
    args = signing.make_parser().parse_args(['--source-run', 'run'])
    assert args.receipts == 1024 and args.rounds == 3 and args.seed == 42

