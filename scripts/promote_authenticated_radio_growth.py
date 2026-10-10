"""Offline promotion of authenticated radio snapshots into typed growth."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from radio_source_integration import prepare_radio_only_snapshots
from genuine_root_recovery import runner_config_sha256


POLICY = 'authenticated-watch-radio-only-v1'
PRODUCER = 'opengenius-authenticated-radio-growth-promotion-v1'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _graph_roots(path):
    graph = _read(Path(path) / 'accepted-graphs.json')
    roots = graph.get('accepted_roots')
    if not isinstance(roots, list):
        raise ValueError('Promotion growth lacks accepted roots')
    return sum(bool(row.get('ordered_target_pids')) for row in roots), graph


def _verify_frozen_campaign(progress, frozen_progress, config, track_snapshot,
                            identity_map, baseline_state, baseline_graph_export,
                            rebase_report):
    """Run the same transitive progress, attempt-ledger, and growth checks as resume."""
    from expand_genuine_roots import (
        _accepted_roots, _authorized_rebound_retry_pids,
        _validate_prior_attempt_ledger, _verify_progress_receipts,
    )
    from genuine_root_recovery import replay_growth_checkpoint
    from music_identity_map import load_library

    tracks, library_hash, _ = load_library(track_snapshot=track_snapshot)
    del tracks
    identity_hash = _sha(identity_map.read_bytes())
    prior_attempts, prior_requests = set(), 0
    ledger_source = config.get('prior_attempt_ledger')
    if ledger_source is not None:
        evidence_source = config.get('external_seed_video_evidence')
        evidence = _read(evidence_source['path']) if evidence_source else None
        retry = _authorized_rebound_retry_pids(evidence, identity_hash)
        ledger = _validate_prior_attempt_ledger(
            ledger_source['path'], library_hash,
            _sha(track_snapshot.read_bytes()),
            authorized_rebound_retry_pids=retry,
            current_identity_map_sha256=identity_hash)
        retry -= ledger['same_map_attempted_pids']
        prior_attempts = ledger['attempted_pids'] - retry
        prior_requests = ledger['http_requests']
    _verify_progress_receipts(progress, library_hash, identity_hash,
                              prior_attempts, prior_requests)
    final_growth = Path(progress['batches'][-1]['growth_output']).resolve()
    replay = replay_growth_checkpoint(
        previous_growth=final_growth, track_snapshot=track_snapshot,
        identity_map_path=identity_map, baseline_state_path=baseline_state,
        baseline_graph_export_path=baseline_graph_export,
        rebase_report_path=rebase_report)
    roots = _accepted_roots(final_growth / 'accepted-graphs.json')
    if (replay['outgoing_root_count'] != progress['successful_outgoing_roots']
            or sum(bool(edges) for edges in roots.values())
            != progress['successful_outgoing_roots']):
        raise ValueError('Frozen source progress differs from its complete typed replay')
    if _sha(Path(frozen_progress).read_bytes()) != _receipt(frozen_progress)['sha256']:
        raise ValueError('Frozen source progress changed during validation')
    return library_hash, identity_hash


def promote(*, track_snapshot, identity_map, baseline_state,
            baseline_graph_export, rebase_report, frozen_progress,
            output_dir, ledger_output=None, script_dir=None):
    """Proof, merge, and receipt each frozen legacy batch without API calls."""
    track_snapshot, identity_map = Path(track_snapshot).resolve(), Path(identity_map).resolve()
    baseline_state = Path(baseline_state).resolve()
    baseline_graph_export, rebase_report = Path(baseline_graph_export).resolve(), Path(rebase_report).resolve()
    frozen_progress, output_dir = Path(frozen_progress).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Promotion output already exists; preserve it and choose a fresh path')
    progress = _read(frozen_progress)
    config = progress.get('config')
    if (not isinstance(config, dict) or progress.get('status') != 'paused_at_checkpoint'
            or not isinstance(progress.get('batches'), list) or not progress['batches']
            or type(progress.get('http_requests')) is not int
            or type(progress.get('successful_outgoing_roots')) is not int
            or not isinstance(progress.get('attempted_pids'), list)):
        raise ValueError('Source progress is not a frozen receipted checkpoint')
    expected_paths = {
        'track_snapshot': track_snapshot, 'identity_map': identity_map,
        'baseline_state': baseline_state, 'baseline_graph_export': baseline_graph_export,
        'rebase_report': rebase_report,
    }
    for key, path in expected_paths.items():
        bound = config.get(key)
        if (not isinstance(bound, dict) or Path(bound.get('path', '')).resolve() != path
                or bound.get('sha256') != _sha(path.read_bytes())):
            raise ValueError('Promotion inputs differ from frozen progress: ' + key)
    if config.get('source_policy') is not None:
        raise ValueError('Promotion source progress must retain its original legacy policy')
    library_hash, identity_hash = _verify_frozen_campaign(
        progress, frozen_progress, config, track_snapshot, identity_map,
        baseline_state, baseline_graph_export, rebase_report)
    if len(progress['attempted_pids']) != len(set(progress['attempted_pids'])):
        raise ValueError('Frozen source progress has duplicate attempted PIDs')
    parent = progress['batches'][-1].get('growth_output')
    parent_graph_receipt = progress['batches'][-1].get('growth_receipt')
    if not isinstance(parent, str) or not isinstance(parent_graph_receipt, dict):
        raise ValueError('Frozen source progress has no final typed graph receipt')
    parent_path = Path(parent).resolve()
    if (Path(parent_graph_receipt.get('path', '')).resolve()
            != (parent_path / 'accepted-graphs.json').resolve()
            or _sha((parent_path / 'accepted-graphs.json').read_bytes())
            != parent_graph_receipt.get('sha256')):
        raise ValueError('Frozen source progress graph receipt changed')
    parent_roots, _ = _graph_roots(parent_path)
    if parent_roots != progress['successful_outgoing_roots']:
        raise ValueError('Frozen progress root total differs from its exact accepted graph')

    output_dir.mkdir(parents=True)
    # The promotion is an append-only source-policy transition after the full
    # frozen legacy checkpoint. Historical runs remain intact and are not
    # replayed into this new branch; source_parent_receipt separately verifies
    # each original intent against its actual legacy parent.
    growth_parent = parent_path
    batch_reports = []
    merge_script = Path(script_dir or Path(__file__).resolve().parent) / 'merge_rebased_radio_growth.py'
    source_parent_receipt = None
    configured_parent = config.get('previous_growth')
    if configured_parent is not None:
        configured_path = Path(configured_parent['path']).resolve()
        if configured_path.is_dir():
            configured_path = configured_path / 'accepted-graphs.json'
        source_parent_receipt = {
            'path': str(configured_path),
            'accepted_graphs_sha256': configured_parent['sha256'],
        }
    for record in progress['batches']:
        if record.get('status') == 'preflight_failed' or record.get('outcome') == 'preflight_failed':
            continue
        batch_dir = Path(record['seed_plan']['path']).resolve().parent
        intent_path = batch_dir / 'intent.json'
        summary_path = Path(record['collector_summary']['path']).resolve()
        manifest_path = Path(record['seed_manifest']['path']).resolve()
        evidence_path = Path(record['seed_video_evidence']['path']).resolve()
        for receipt_key, path in (
                ('collector_summary', summary_path), ('seed_manifest', manifest_path),
                ('seed_video_evidence', evidence_path)):
            expected = record.get(receipt_key)
            if (not isinstance(expected, dict) or Path(expected.get('path', '')).resolve() != path
                    or expected.get('sha256') != _sha(path.read_bytes())):
                raise ValueError('Frozen batch source receipt changed: ' + receipt_key)
        intent = _read(intent_path)
        checks = {
            'batch': intent.get('batch') == record.get('batch'),
            'seed_pids': intent.get('seed_pids') == record.get('seed_pids'),
            'runner_config': intent.get('runner_config') == config,
            'runner_config_sha256': intent.get('runner_config_sha256') == runner_config_sha256(config),
            'library_sha256': intent.get('library_sha256') == library_hash,
            'identity_map_sha256': intent.get('identity_map_sha256') == identity_hash,
            'seed_plan': intent.get('seed_plan') == record.get('seed_plan'),
            'seed_video_evidence': intent.get('seed_video_evidence') == record.get('seed_video_evidence'),
            'request_budget': intent.get('request_budget') == record.get('request_budget'),
            'previous_growth': intent.get('previous_growth') == source_parent_receipt,
        }
        if not all(checks.values()):
            if not checks['previous_growth']:
                raise ValueError('Frozen parent mismatch: intent=' + repr(intent.get('previous_growth')) +
                                 ' expected=' + repr(source_parent_receipt))
            raise ValueError('Frozen batch intent differs from progress fields: ' +
                             ','.join(key for key, valid in checks.items() if not valid) +
                             ' (batch ' + str(record.get('batch')) + ')')
        expected_sources = [config[key] for key in (
            'track_snapshot', 'identity_map', 'baseline_state',
            'baseline_graph_export', 'rebase_report')]
        expected_sources.extend(config[key] for key in (
            'external_seed_video_evidence', 'prior_attempt_ledger') if config.get(key) is not None)
        if intent.get('source_inputs') != expected_sources:
            raise ValueError('Frozen batch intent source receipts differ from campaign config')
        collector_dir = batch_dir / 'collector'
        summary = _read(summary_path)
        collected = {row.get('persistent_id') for row in summary.get('results', [])
                     if isinstance(row, dict) and row.get('status') == 'collected'}
        snapshots = sorted(collector_dir.glob('[0-9A-F]' * 16 + '.json'))
        snapshot_receipts = [_receipt(path) for path in snapshots]
        if ({path.stem for path in snapshots} != collected
                or snapshot_receipts != record.get('snapshot_inputs')):
            raise ValueError('Frozen batch snapshots differ from collected result receipts')
        proof_result = prepare_radio_only_snapshots(
            track_snapshot_path=track_snapshot, identity_map_path=identity_map,
            batch_dir=batch_dir, collector_summary_path=summary_path,
            collector_seed_manifest_path=manifest_path, snapshot_paths=snapshots,
            output_dir=output_dir / 'radio-inputs' / ('batch-%04d' % record['batch']))
        if not proof_result['snapshot_inputs']:
            raise ValueError('Frozen batch has no strict authenticated radio-only targets')
        batch_growth = output_dir / ('growth-batch-%04d' % record['batch'])
        command = [sys.executable, str(merge_script), '--track-snapshot', str(track_snapshot),
                   '--identity-map', str(identity_map), '--baseline-state', str(baseline_state),
                   '--baseline-graph-export', str(baseline_graph_export), '--rebase-report',
                   str(rebase_report), '--collector-summary', str(summary_path),
                   '--seed-manifest', str(manifest_path), '--seed-video-evidence',
                   str(evidence_path), '--output', str(batch_growth), '--source-policy', POLICY]
        for receipt in proof_result['snapshot_inputs']:
            command.extend(['--snapshot', receipt['path']])
        if growth_parent:
            command.extend(['--previous-growth', str(growth_parent)])
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError('Typed authenticated radio merge failed for batch %s' % record['batch'])
        before_roots, _ = _graph_roots(growth_parent)
        after_roots, graph = _graph_roots(batch_growth)
        graph_receipt = _receipt(batch_growth / 'accepted-graphs.json')
        batch_reports.append({
            'batch': record['batch'],
            'collector_summary': _receipt(summary_path),
            'seed_manifest': _receipt(manifest_path),
            'batch_intent': _receipt(intent_path),
            'seed_video_evidence': _receipt(evidence_path),
            'collector_snapshots': [_receipt(path) for path in snapshots],
            'radio_source_output_dir': proof_result['output_dir'],
            'proofs': proof_result['proofs'],
            'snapshot_inputs': proof_result['snapshot_inputs'],
            'ineligible': proof_result['ineligible'],
            'growth': {'path': str(batch_growth),
                       'accepted_graphs_sha256': graph_receipt['sha256']},
            'outgoing_roots_before': before_roots,
            'outgoing_roots_after': after_roots,
            'accepted_edge_count': graph.get('accepted_edge_count'),
        })
        growth_parent = batch_growth
        original_growth_receipt = record.get('growth_receipt')
        if original_growth_receipt is not None:
            source_parent_receipt = {
                'path': str(Path(original_growth_receipt['path']).resolve()),
                'accepted_graphs_sha256': original_growth_receipt['sha256'],
            }
    if not batch_reports:
        raise ValueError('No completed collector batches were eligible for promotion')
    final_roots, final_graph = _graph_roots(growth_parent)
    from genuine_root_recovery import replay_growth_checkpoint
    final_replay = replay_growth_checkpoint(
        previous_growth=growth_parent, track_snapshot=track_snapshot,
        identity_map_path=identity_map, baseline_state_path=baseline_state,
        baseline_graph_export_path=baseline_graph_export,
        rebase_report_path=rebase_report)
    final_graph_hash = _receipt(growth_parent / 'accepted-graphs.json')['sha256']
    if (final_replay['accepted_graphs_sha256'] != final_graph_hash
            or final_replay['outgoing_root_count'] != final_roots):
        raise ValueError('Promoted radio-policy chain does not reproduce its complete typed graph')
    report = {
        'schema_version': 1,
        'producer': 'opengenius-authenticated-radio-growth-promotion-v1',
        'source_policy': POLICY,
        'source_progress': _receipt(frozen_progress),
        'track_snapshot': _receipt(track_snapshot),
        'identity_map': _receipt(identity_map),
        'baseline_state': _receipt(baseline_state),
        'baseline_graph_export': _receipt(baseline_graph_export),
        'rebase_report': _receipt(rebase_report),
        'parent_growth': parent_graph_receipt,
        'promoted_growth': {
            'path': str(growth_parent),
            'accepted_graphs_sha256': _receipt(growth_parent / 'accepted-graphs.json')['sha256'],
        },
        'http_requests_before': progress['http_requests'],
        'http_requests_after': progress['http_requests'],
        'attempted_pids_before': sorted(progress['attempted_pids']),
        'attempted_pids_after': sorted(progress['attempted_pids']),
        'http_request_delta': 0,
        'attempt_delta': 0,
        'promoted_outgoing_roots': final_roots,
        'promotion_batches': batch_reports,
    }
    report_path = output_dir / 'promotion.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    ledger_path = None
    if ledger_output is not None:
        ledger_path = Path(ledger_output).resolve()
        if ledger_path.exists():
            raise FileExistsError('Promotion attempt ledger already exists')
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = {
            'schema_version': 2,
            'producer': 'opengenius-genuine-root-attempt-ledger-v2',
            'prior_progress': _receipt(frozen_progress),
            'attempt_only_progress': [],
            'interruption_incidents': [],
            'http_requests_total': progress['http_requests'],
            'excluded_persistent_ids': sorted(progress['attempted_pids']),
            'base_outgoing_roots': final_roots,
            'promotion_receipt': _receipt(report_path),
        }
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
    return {'report_path': str(report_path), 'report_sha256': _sha(report_path.read_bytes()),
            'ledger_path': str(ledger_path) if ledger_path else None,
            'ledger_sha256': _sha(ledger_path.read_bytes()) if ledger_path else None,
            'roots_before': parent_roots, 'roots_after': final_roots,
            'root_delta': final_roots - parent_roots,
            'http_requests': progress['http_requests'],
            'attempted_pids': len(progress['attempted_pids']),
            'growth_dir': str(growth_parent)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot', 'identity-map', 'baseline-state',
                 'baseline-graph-export', 'rebase-report', 'frozen-progress', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--ledger-output', type=Path)
    args = parser.parse_args()
    result = promote(
        track_snapshot=args.track_snapshot, identity_map=args.identity_map,
        baseline_state=args.baseline_state, baseline_graph_export=args.baseline_graph_export,
        rebase_report=args.rebase_report, frozen_progress=args.frozen_progress,
        output_dir=args.output_dir, ledger_output=args.ledger_output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
