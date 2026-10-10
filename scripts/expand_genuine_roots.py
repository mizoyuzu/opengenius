"""Automatically collect paced batches of genuine YT Music recommendation roots.

This runner only consumes a verified rebased graph and batches produced by
probe_ytmusic_batch.py. Every accepted checkpoint is merged through
merge_rebased_radio_growth.py, whose receipts are rechecked on resume.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from music_identity_map import IdentityMap, load_library, version_facets
from seed_video_evidence import (
    REBOUND_PRODUCER, build_combined_rebound_seed_video_evidence,
    build_seed_video_evidence, select_seed_video_evidence,
)
from genuine_root_recovery import (
    RecoveryUnsafe, recover_orphan_batch, replay_growth_checkpoint,
    runner_config_sha256,
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def _write_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _file_receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _growth_receipt(growth_dir):
    if growth_dir is None:
        return None
    receipt = _file_receipt(Path(growth_dir) / 'accepted-graphs.json')
    return {'path': receipt['path'], 'accepted_graphs_sha256': receipt['sha256']}


def _merge_batch(args, batch_dir, summary_path, evidence_path, snapshot_paths,
                 active_growth, script_dir):
    collector_dir = batch_dir / 'collector'
    output = batch_dir / 'growth'
    command = [sys.executable, str(script_dir / 'merge_rebased_radio_growth.py'),
               '--track-snapshot', str(args.track_snapshot),
               '--identity-map', str(args.identity_map),
               '--baseline-state', str(args.baseline_state),
               '--baseline-graph-export', str(args.baseline_graph_export),
               '--rebase-report', str(args.rebase_report),
               '--collector-summary', str(summary_path), '--seed-manifest',
               str(collector_dir / 'seeds.json'), '--seed-video-evidence',
               str(evidence_path), '--output', str(output)]
    source_policy = getattr(args, 'source_policy', None)
    if source_policy is not None:
        command.extend(['--source-policy', source_policy])
    for snapshot_path in snapshot_paths:
        command.extend(['--snapshot', str(snapshot_path)])
    if active_growth:
        command.extend(['--previous-growth', str(active_growth)])
    if _invoke(command) != 0:
        raise RecoveryUnsafe('Typed merge rejected the recovered collector batch')
    return output


def _replay_checkpoint(args, growth_dir):
    return replay_growth_checkpoint(
        previous_growth=growth_dir,
        track_snapshot=args.track_snapshot,
        identity_map_path=args.identity_map,
        baseline_state_path=args.baseline_state,
        baseline_graph_export_path=args.baseline_graph_export,
        rebase_report_path=args.rebase_report)


def _batch_record_from_recovery(recovered, growth_dir):
    growth_file = Path(growth_dir) / 'accepted-graphs.json' if growth_dir else None
    radio_source = recovered.get('radio_only_source') or {}
    record = {
        'batch': recovered['batch'],
        'seed_pids': recovered['seed_pids'],
        'seed_plan': recovered['seed_plan'],
        'seed_manifest': recovered['seed_manifest'],
        'seed_video_evidence': recovered['seed_video_evidence'],
        'collector_summary': recovered['collector_summary'],
        'collector_summary_sha256': recovered['collector_summary']['sha256'],
        'status': recovered['status'],
        'http_requests': recovered['http_requests'],
        'request_budget': recovered['collector_summary']['request_budget'],
        'outcome': recovered['outcome'],
        'snapshots': [row['path'] for row in recovered['genuine_snapshot_inputs']],
        'snapshot_inputs': recovered['snapshot_inputs'],
        'growth_output': str(Path(growth_dir).resolve()) if growth_dir else None,
        'growth_receipt': (_file_receipt(growth_file) if growth_file else None),
        'attempted_pids': recovered['attempted_pids'],
    }
    if recovered.get('preflight_intent_receipt') is not None:
        record['preflight_intent_receipt'] = recovered['preflight_intent_receipt']
    if radio_source:
        record['source_policy'] = radio_source['source_policy']
        record['radio_only_source'] = radio_source
    return record


def _promote_orphan(args, progress, batch_dir, active_growth, config,
                    library_hash, identity_hash, tracks, matcher, script_dir):
    expected_parent = _growth_receipt(active_growth)
    recovered = recover_orphan_batch(
        batch_dir,
        expected_config_sha256=_orphan_runner_config_sha256(
            Path(batch_dir) / 'intent.json', config),
        library_sha256=library_hash,
        identity_map_sha256=config['identity_map']['sha256'],
        expected_previous_growth=expected_parent,
        expected_request_budget=min(config['batch_request_budget'],
                                     config['global_request_budget'] - progress['http_requests']))
    select_seed_video_evidence(
        _read_json(recovered['seed_video_evidence']['path']),
        Path(recovered['seed_video_evidence']['path']),
        [{'persistent_id': pid} for pid in recovered['seed_pids']],
        tracks, matcher, library_hash, identity_hash)
    growth_dir = Path(batch_dir) / 'growth'
    if recovered['requires_typed_growth_replay']:
        replayed = _replay_checkpoint(args, growth_dir)
        if replayed['accepted_graphs_sha256'] != recovered['growth_receipt']['sha256']:
            raise RecoveryUnsafe('Orphan typed growth changed during full-chain replay')
        active_growth = growth_dir
    elif recovered['outcome'] == 'ready_to_merge':
        summary_path = recovered['collector_summary']['path']
        evidence_path = recovered['seed_video_evidence']['path']
        genuine_paths = [Path(row['path']) for row in recovered['genuine_snapshot_inputs']]
        from evaluate_ytmusic_batch import collect_graphs
        try:
            source_policy = config.get('source_policy')
            if source_policy is None:
                graphs, _, _ = collect_graphs(genuine_paths, matcher, library_hash)
            else:
                graphs, _, _ = collect_graphs(
                    genuine_paths, matcher, library_hash,
                    source_policy=source_policy,
                    identity_map_sha256=identity_hash)
        except ValueError as error:
            if config.get('source_policy') is not None:
                raise RecoveryUnsafe('Proof-backed collector snapshots failed typed merge validation') from error
            graphs = []
        if not graphs:
            # Authenticated endpoints alone do not create an accepted root.
            # Preserve the attempt and continue planning elsewhere.
            recovered['outcome'] = 'authenticated_no_graphs'
        else:
            active_growth = _merge_batch(args, batch_dir, summary_path, evidence_path,
                                         genuine_paths, active_growth, script_dir)
            replayed = _replay_checkpoint(args, active_growth)
            if replayed['accepted_graphs_sha256'] != _sha((active_growth / 'accepted-graphs.json').read_bytes()):
                raise RecoveryUnsafe('New orphan merge failed full-chain replay')
            recovered['outcome'] = 'merged'
    roots = _accepted_roots(active_growth / 'accepted-graphs.json'
                            if active_growth else args.baseline_graph_export)
    progress['batches'].append(_batch_record_from_recovery(recovered, active_growth))
    progress['attempted_pids'] = sorted(set(progress['attempted_pids']) |
                                        set(recovered['attempted_pids']))
    progress['http_requests'] += recovered['http_requests']
    progress['successful_outgoing_roots'] = sum(bool(edges) for edges in roots.values())
    progress['rejected_or_unresolved_pids'] = sorted(
        set(progress['rejected_or_unresolved_pids']) |
        (set(recovered['attempted_pids']) -
         {pid for pid, edges in roots.items() if edges}))
    progress['status'] = 'running'
    return active_growth, roots


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _verify_preflight_progress_record(row, progress, library_hash, identity_hash,
                                     prior_http_requests, requests_from_records,
                                     expected_previous_growth):
    """Replay the one receipted no-call selector preflight recovery shape."""
    config = progress.get('config')
    if not isinstance(config, dict):
        raise ValueError('Preflight recovery has no immutable campaign config')
    plan_receipt = row.get('seed_plan')
    if not isinstance(plan_receipt, dict) or not isinstance(plan_receipt.get('path'), str):
        raise ValueError('Preflight recovery lacks its saved seed plan')
    batch_dir = Path(plan_receipt['path']).resolve().parent
    remaining = config.get('global_request_budget', 0) - prior_http_requests - requests_from_records
    if remaining < 0:
        raise ValueError('Preflight recovery exceeds the campaign request budget')
    expected_budget = min(config.get('batch_request_budget', 0), remaining)
    try:
        recovered = recover_orphan_batch(
            batch_dir,
            expected_config_sha256=_orphan_runner_config_sha256(
                batch_dir / 'intent.json', config),
            library_sha256=library_hash,
            identity_map_sha256=identity_hash,
            expected_previous_growth=expected_previous_growth,
            expected_request_budget=expected_budget)
    except RecoveryUnsafe as error:
        raise ValueError('Preflight recovery receipt did not replay: ' + str(error)) from error
    expected_growth_row = (None if expected_previous_growth is None else {
        'path': expected_previous_growth['path'],
        'sha256': expected_previous_growth['accepted_graphs_sha256'],
    })
    if (recovered.get('outcome') != 'preflight_failed'
            or row.get('status') != 'preflight_failed'
            or row.get('outcome') != 'preflight_failed'
            or row.get('http_requests') != 0
            or row.get('attempted_pids') != []
            or row.get('seed_manifest') is not None
            or row.get('seed_pids') != recovered.get('seed_pids')
            or row.get('seed_plan') != recovered.get('seed_plan')
            or row.get('seed_video_evidence') != recovered.get('seed_video_evidence')
            or row.get('collector_summary') != recovered.get('collector_summary')
            or row.get('collector_summary_sha256') != recovered['collector_summary']['sha256']
            or row.get('request_budget') != recovered['collector_summary']['request_budget']
            or row.get('snapshot_inputs') != [] or row.get('snapshots') != []
            or row.get('growth_receipt') != expected_growth_row):
        raise ValueError('Preflight progress record differs from its zero-call recovery receipt')
    intent_receipt = recovered['preflight_intent_receipt']
    recorded_intent = row.get('preflight_intent_receipt')
    if recorded_intent is not None and recorded_intent != intent_receipt:
        raise ValueError('Preflight intent receipt changed after progress promotion')
    # Earlier v03 progress promoted this narrowly recognized artifact before
    # storing the intent receipt. Upgrade it now so every later resume checks
    # the exact durable intent hash as well as the replayed fields.
    row['preflight_intent_receipt'] = intent_receipt
    return recovered


def _verify_progress_receipts(progress, library_hash, identity_hash,
                             prior_attempted_pids=(), prior_http_requests=0):
    attempted_from_records = set()
    requests_from_records = 0
    config = progress.get('config') or {}
    configured_parent = config.get('previous_growth')
    if configured_parent is None:
        expected_previous_growth = None
    else:
        configured_path = Path(configured_parent['path'])
        if configured_path.is_dir():
            configured_path = configured_path / 'accepted-graphs.json'
        expected_previous_growth = {
            'path': str(configured_path.resolve()),
            'accepted_graphs_sha256': configured_parent['sha256'],
        }
    for row in progress.get('batches', []):
        if row.get('outcome') == 'preflight_failed' or row.get('status') == 'preflight_failed':
            _verify_preflight_progress_record(
                row, progress, library_hash, identity_hash, prior_http_requests,
                requests_from_records, expected_previous_growth)
            # A preflight failure has no request, result, or attempt to charge.
            continue
        for key in ('collector_summary', 'seed_manifest', 'seed_plan', 'seed_video_evidence'):
            receipt = row.get(key)
            if (not isinstance(receipt, dict) or not isinstance(receipt.get('path'), str)
                    or not isinstance(receipt.get('sha256'), str)):
                raise ValueError('Saved progress lacks a batch file receipt')
            path = Path(receipt['path'])
            if _sha(path.read_bytes()) != receipt['sha256']:
                raise ValueError('A file bound by saved progress changed')
        plan = _read_json(row['seed_plan']['path'])
        manifest = _read_json(row['seed_manifest']['path'])
        summary = _read_json(row['collector_summary']['path'])
        planned = [seed.get('persistent_id') for seed in plan.get('seeds', [])
                   if isinstance(seed, dict)]
        manifest_pids = [seed.get('persistent_id') for seed in manifest.get('seeds', [])
                         if isinstance(seed, dict)]
        result_rows = summary.get('results')
        result_pids = [result.get('persistent_id') for result in result_rows
                       if isinstance(result, dict)] if isinstance(result_rows, list) else []
        valid_statuses = {'collected', 'search_unresolved', 'deadline_partial'}
        if (plan.get('source_library_sha256') != library_hash
                or manifest.get('library_sha256') != library_hash
                or manifest.get('identity_map_sha256') != identity_hash
                or manifest.get('seed_plan_sha256') != row['seed_plan']['sha256']
                or manifest.get('seed_video_evidence_sha256') != row['seed_video_evidence']['sha256']
                or planned != manifest_pids or planned != row.get('seed_pids')
                or len(planned) != len(set(planned))
                or not isinstance(result_rows, list) or len(result_rows) != len(result_pids)
                or len(result_pids) != len(set(result_pids))
                or set(result_pids) - set(planned)
                or any(result.get('status') not in valid_statuses for result in result_rows)
                or not _valid_collector_batch(summary)
                or summary.get('library_sha256') != library_hash
                or row['collector_summary'].get('sha256') != row.get('collector_summary_sha256')
                or row.get('request_budget') != summary.get('request_budget')
                or row.get('http_requests') != summary.get('http_requests')):
            raise ValueError('Saved batch receipts do not bind a valid collector run to the planned PIDs')
        attempted_from_records.update(result_pids)
        requests_from_records += summary['http_requests']
        partial = summary.get('partial_seed_persistent_id')
        if partial is not None:
            if partial not in planned or partial in result_pids:
                raise ValueError('Saved partial seed receipt differs from its batch plan')
            attempted_from_records.add(partial)
        collector_dir = Path(row['seed_manifest']['path']).resolve().parent
        collected_pids = {result['persistent_id'] for result in result_rows
                          if result.get('status') == 'collected'}
        observed_snapshot_pids = set()
        snapshot_receipts = []
        for snapshot_path in sorted(collector_dir.glob('[0-9A-F]' * 16 + '.json')):
            pid = snapshot_path.stem
            raw = snapshot_path.read_bytes()
            snapshot = json.loads(raw)
            if (pid not in collected_pids
                    or snapshot.get('library_sha256') != library_hash
                    or snapshot.get('identity_map_sha256') != identity_hash
                    or snapshot.get('account_authentication_verified') is not True
                    or snapshot.get('local_seed', {}).get('persistent_id') != pid):
                raise ValueError('Saved snapshot does not match an authenticated collected result')
            observed_snapshot_pids.add(pid)
            snapshot_receipts.append({'path': str(snapshot_path.resolve()),
                                      'sha256': hashlib.sha256(raw).hexdigest()})
        if collected_pids != observed_snapshot_pids:
            raise ValueError('A collected result is missing its saved snapshot')
        # Upgrade legacy receipts so future resume checks bind every saved
        # collector snapshot, including roots rejected as radio/related-incomplete.
        row['snapshot_inputs'] = snapshot_receipts
        for receipt in row.get('snapshot_inputs', []):
            if _sha(Path(receipt['path']).read_bytes()) != receipt['sha256']:
                raise ValueError('A saved collector snapshot changed')
        if config.get('source_policy') == 'authenticated-watch-radio-only-v1':
            from radio_source_integration import prepare_radio_only_snapshots
            source = row.get('radio_only_source')
            if (not isinstance(source, dict)
                    or source.get('source_policy') != config['source_policy']
                    or not isinstance(source.get('output_dir'), str)):
                raise ValueError('Radio-policy progress lacks its proof-backed source receipt')
            reproduced = prepare_radio_only_snapshots(
                track_snapshot_path=config['track_snapshot']['path'],
                identity_map_path=config['identity_map']['path'],
                batch_dir=collector_dir.parent,
                collector_summary_path=row['collector_summary']['path'],
                collector_seed_manifest_path=row['seed_manifest']['path'],
                snapshot_paths=[Path(receipt['path']) for receipt in snapshot_receipts],
                output_dir=source['output_dir'], create_missing=False)
            if (reproduced != source
                    or row.get('snapshots') != [receipt['path']
                                                for receipt in reproduced['snapshot_inputs']]):
                raise ValueError('Radio-policy progress differs from full proof and snapshot replay')
        elif row.get('source_policy') is not None or row.get('radio_only_source') is not None:
            raise ValueError('Legacy progress contains an unconfigured radio-only source receipt')
        growth = row.get('growth_receipt')
        if growth is not None and _sha(Path(growth['path']).read_bytes()) != growth['sha256']:
            raise ValueError('A saved growth checkpoint changed')
        if growth is not None:
            expected_previous_growth = {
                'path': str(Path(growth['path']).resolve()),
                'accepted_graphs_sha256': growth['sha256'],
            }
    if progress.get('batches'):
        if (attempted_from_records | set(prior_attempted_pids)
                != set(progress.get('attempted_pids', []))):
            raise ValueError('Progress attempted PID list differs from exact collector result receipts')
        if requests_from_records + prior_http_requests != progress.get('http_requests'):
            raise ValueError('Progress request total differs from exact collector summary receipts')
    elif (progress.get('http_requests') != prior_http_requests
          or set(progress.get('attempted_pids', [])) != set(prior_attempted_pids)):
        raise ValueError('Progress without local batches differs from prior attempt ledger')


def _legacy_progress_compatible(progress, config):
    old = progress.get('config')
    legacy = {key: value for key, value in config.items()
              if key not in ('batch_request_budget', 'deadline', 'prior_attempt_ledger',
                             'dynamic_current_radio_hints')}
    if (old != legacy or config['deadline'] is not None
            or config.get('dynamic_current_radio_hints', False) is not False):
        return False
    records = progress.get('batches', [])
    if not records:
        return True
    budgets = []
    for row in records:
        if (row.get('outcome') == 'preflight_failed'
                and row.get('status') == 'preflight_failed'):
            # The receipt verifier below separately validates this exact
            # zero-call record. It intentionally has no collector manifest.
            continue
        if (row.get('outcome') == 'preflight_failed'
                or row.get('status') == 'preflight_failed'):
            return False
        if type(row.get('request_budget')) is not int:
            return False
        budgets.append(row['request_budget'])
        manifest = row.get('seed_manifest')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('path'), str):
            return False
        try:
            if 'deadline' in _read_json(manifest['path']):
                return False
        except (OSError, json.JSONDecodeError):
            return False
    # The legacy progress omitted the configured cap. Its recorded batch caps
    # prove the setting only when all non-final requests used the same maximum.
    return max(budgets) == config['batch_request_budget'] and all(
        budget <= config['batch_request_budget'] for budget in budgets)


def _current_progress_compatible(progress, config):
    """Allow only the added no-op dynamic flag on existing current-schema runs."""
    if config.get('dynamic_current_radio_hints', False) is not False:
        return False
    old = progress.get('config')
    without_dynamic = {key: value for key, value in config.items()
                       if key != 'dynamic_current_radio_hints'}
    return old == without_dynamic


def _orphan_runner_config_sha256(intent_path, config):
    """Accept only an old intent differing by the legacy false dynamic flag."""
    intent = _read_json(intent_path)
    recorded_config = intent.get('runner_config')
    recorded_hash = intent.get('runner_config_sha256')
    current_hash = runner_config_sha256(config)
    if recorded_hash == current_hash:
        return current_hash
    current_without_dynamic = {
        key: value for key, value in config.items()
        if key != 'dynamic_current_radio_hints'
    }
    if (not isinstance(recorded_config, dict)
            or runner_config_sha256(recorded_config) != recorded_hash
            or config.get('dynamic_current_radio_hints', False) is not False
            or recorded_config != current_without_dynamic):
        raise RecoveryUnsafe('Orphan intent config differs from current runner config')
    return recorded_hash


def _validate_prior_attempt_ledger(path, library_hash, track_snapshot_sha256,
                                   _seen_ledgers=None,
                                   authorized_rebound_retry_pids=None,
                                   current_identity_map_sha256=None):
    """Verify imported request and PID accounting before starting a new stage."""
    if _seen_ledgers is None:
        _seen_ledgers = set()
    ledger_path = Path(path).resolve()
    if ledger_path in _seen_ledgers:
        raise RecoveryUnsafe('Prior-attempt ledger chain contains a cycle')
    _seen_ledgers = set(_seen_ledgers)
    _seen_ledgers.add(ledger_path)
    ledger = _read_json(path)
    required = {'schema_version', 'producer', 'prior_progress',
                'attempt_only_progress', 'interruption_incidents', 'http_requests_total',
                'excluded_persistent_ids', 'base_outgoing_roots'}
    ledger_version = ledger.get('schema_version') if isinstance(ledger, dict) else None
    if ledger_version == 2:
        required = required | {'promotion_receipt'}
        expected_producer = 'opengenius-genuine-root-attempt-ledger-v2'
    else:
        expected_producer = 'opengenius-genuine-root-attempt-ledger-v1'
    if (not isinstance(ledger, dict) or set(ledger) != required
            or ledger_version not in (1, 2)
            or ledger.get('producer') != expected_producer
            or type(ledger.get('http_requests_total')) is not int
            or ledger['http_requests_total'] < 0
            or type(ledger.get('base_outgoing_roots')) is not int
            or ledger['base_outgoing_roots'] < 0):
        raise RecoveryUnsafe('Prior-attempt ledger schema or totals are invalid')

    def checked_receipt(receipt, label):
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not isinstance(receipt.get('sha256'), str)):
            raise RecoveryUnsafe('Malformed ' + label + ' receipt')
        source = Path(receipt['path']).resolve()
        if _sha(source.read_bytes()) != receipt['sha256']:
            raise RecoveryUnsafe(label + ' hash differs from its ledger receipt')
        return source

    prior_progress_path = checked_receipt(ledger.get('prior_progress'), 'prior progress')
    prior = _read_json(prior_progress_path)
    prior_config = prior.get('config')
    if (not isinstance(prior_config, dict)
            or prior_config.get('track_snapshot', {}).get('sha256') is None
            or prior_config.get('identity_map', {}).get('sha256') is None):
        raise RecoveryUnsafe('Prior progress lacks source hashes')
    if prior_config['track_snapshot']['sha256'] != track_snapshot_sha256:
        raise RecoveryUnsafe('Prior progress is bound to a different Library snapshot')
    prior_parent_attempts = set()
    prior_parent_requests = 0
    parent_ledger_receipt = prior_config.get('prior_attempt_ledger')
    if parent_ledger_receipt is not None:
        parent_ledger_path = checked_receipt(parent_ledger_receipt,
                                             'prior progress attempt ledger')
        parent_ledger = _validate_prior_attempt_ledger(
            parent_ledger_path, library_hash, track_snapshot_sha256, _seen_ledgers,
            authorized_rebound_retry_pids, current_identity_map_sha256)
        has_release_field = 'authorized_retry_release_pids' in prior_config
        releases = prior_config.get('authorized_retry_release_pids', [])
        if (not isinstance(releases, list)
                or any(not isinstance(pid, str) or not re.fullmatch(r'[0-9A-F]{16}', pid)
                       for pid in releases)
                or len(releases) != len(set(releases))
                or (has_release_field and (
                    authorized_rebound_retry_pids is None
                    or set(releases) != (set(authorized_rebound_retry_pids) -
                                         parent_ledger['same_map_attempted_pids'])))
                or (not has_release_field and releases)):
            raise RecoveryUnsafe('Prior progress retry releases differ from its parent ledger')
        prior_parent_attempts = parent_ledger['attempted_pids'] - set(releases)
        prior_parent_requests = parent_ledger['http_requests']
    _verify_progress_receipts(
        prior, library_hash, prior_config['identity_map']['sha256'],
        prior_parent_attempts, prior_parent_requests)
    prior_attempt_rows = prior.get('attempted_pids', [])
    if (not isinstance(prior_attempt_rows, list)
            or any(not isinstance(pid, str) or not re.fullmatch(r'[0-9A-F]{16}', pid)
                   for pid in prior_attempt_rows)
            or len(prior_attempt_rows) != len(set(prior_attempt_rows))):
        raise RecoveryUnsafe('Prior progress attempted PID list is malformed or duplicated')
    prior_attempted = set(prior_attempt_rows)
    same_map_attempts = set()
    if (current_identity_map_sha256 is not None
            and prior_config['identity_map']['sha256'] == current_identity_map_sha256):
        for batch in prior.get('batches', []):
            rows = batch.get('attempted_pids', [])
            if isinstance(rows, list):
                same_map_attempts.update(pid for pid in rows if isinstance(pid, str))
    prior_requests = prior.get('http_requests')
    prior_roots = prior.get('successful_outgoing_roots')
    if (type(prior_requests) is not int or prior_requests < 0
            or type(prior_roots) is not int or prior_roots < 0
            or not isinstance(prior.get('batches'), list)):
        raise RecoveryUnsafe('Prior progress totals are malformed')

    historical_attempts = set()
    historical_receipts = ledger.get('attempt_only_progress')
    if not isinstance(historical_receipts, list):
        raise RecoveryUnsafe('Prior attempt-only progress receipts must be a list')
    initial_parent = prior_config.get('previous_growth')
    for receipt in historical_receipts:
        historical_path = checked_receipt(receipt, 'attempt-only progress')
        historical = _read_json(historical_path)
        historical_config = historical.get('config')
        if (not isinstance(historical_config, dict)
                or historical_config.get('track_snapshot', {}).get('sha256') != track_snapshot_sha256
                or historical_config.get('identity_map', {}).get('sha256')
                != prior_config['identity_map']['sha256']):
            raise RecoveryUnsafe('Attempt-only progress is bound to a different Library or map')
        _verify_progress_receipts(
            historical, library_hash, historical_config['identity_map']['sha256'])
        old_pids = historical.get('attempted_pids', [])
        if (not isinstance(old_pids, list)
                or any(not isinstance(pid, str) or not re.fullmatch(r'[0-9A-F]{16}', pid)
                       for pid in old_pids)
                or len(old_pids) != len(set(old_pids))):
            raise RecoveryUnsafe('Attempt-only progress has malformed attempted PIDs')
        if not historical.get('batches'):
            raise RecoveryUnsafe('Attempt-only progress has no receipted growth checkpoint')
        historical_growth = historical['batches'][-1].get('growth_receipt')
        if (not isinstance(initial_parent, dict)
                or not isinstance(historical_growth, dict)
                or historical_growth.get('sha256') != initial_parent.get('sha256')):
            raise RecoveryUnsafe('Attempt-only progress does not bind the next stage parent graph')
        parent_path = Path(initial_parent.get('path', '')).resolve()
        if parent_path.is_dir():
            parent_path = parent_path / 'accepted-graphs.json'
        if historical_growth.get('path') != str(parent_path):
            raise RecoveryUnsafe('Attempt-only progress graph path differs from next stage parent')
        parent_graph = _read_json(parent_path)
        parent_rows = parent_graph.get('accepted_roots')
        if (not isinstance(parent_rows, list)
                or historical.get('successful_outgoing_roots')
                != sum(bool(row.get('ordered_target_pids')) for row in parent_rows
                       if isinstance(row, dict))):
            raise RecoveryUnsafe('Attempt-only progress root count differs from its child stage')
        historical_attempts.update(old_pids)
        if (current_identity_map_sha256 is not None
                and historical_config['identity_map']['sha256'] == current_identity_map_sha256):
            same_map_attempts.update(old_pids)

    incident_pids, incident_paths, incident_requests = set(), set(), 0
    incidents = ledger.get('interruption_incidents')
    if not isinstance(incidents, list):
        raise RecoveryUnsafe('Prior-attempt ledger incidents must be a list')
    for receipt in incidents:
        incident_path = checked_receipt(receipt, 'interruption incident')
        if incident_path in incident_paths:
            raise RecoveryUnsafe('Prior-attempt ledger repeats an interruption incident')
        incident_paths.add(incident_path)
        incident = _read_json(incident_path)
        pid = incident.get('seed_persistent_id_to_exclude_from_future_attempts')
        starts = incident.get('request_starts_counted_by_collector')
        if (incident.get('schema_version') != 1
                or incident.get('type') != 'operator_interrupted_request'
                or incident.get('status') != 'uncheckpointed_no_retry'
                or not isinstance(pid, str) or not re.fullmatch(r'[0-9A-F]{16}', pid)
                or type(starts) is not int or starts < 1):
            raise RecoveryUnsafe('Prior interruption incident is malformed')
        if pid in incident_pids or pid in prior_attempted:
            raise RecoveryUnsafe('Prior interruption incident duplicates an attempted PID')
        summary_path = checked_receipt(incident.get('summary_receipt'), 'interrupted summary')
        summary = _read_json(summary_path)
        plan_path = checked_receipt(incident.get('seed_plan_receipt'), 'interrupted seed plan')
        plan = _read_json(plan_path)
        seeds = [row.get('persistent_id') for row in plan.get('seeds', [])
                 if isinstance(row, dict)]
        if (summary.get('status') != 'running'
                or summary.get('account_authentication_verified') is not True
                or summary.get('http_requests') != starts
                or type(summary.get('http_requests')) is not int
                or type(summary.get('request_budget')) is not int
                or starts >= summary['request_budget']
                or summary.get('results') != []
                or summary.get('partial_seed_persistent_id') is not None
                or 'http_error_status' in summary
                or summary.get('library_sha256') != library_hash
                or not seeds or seeds[0] != pid):
            raise RecoveryUnsafe('Interrupted collector receipt does not prove a no-result request stop')
        intent_path = checked_receipt(incident.get('intent_receipt'), 'interrupted batch intent')
        intent = _read_json(intent_path)
        if (intent.get('seed_pids') != seeds
                or Path(intent_path).parent != Path(plan_path).parent
                or intent.get('library_sha256') != library_hash
                or intent.get('identity_map_sha256') != prior_config['identity_map']['sha256']):
            raise RecoveryUnsafe('Interrupted intent does not bind the saved seed plan and prior map')
        checkpoint = incident.get('last_typed_checkpoint')
        checkpoint_hash = incident.get('last_typed_checkpoint_graph_sha256')
        if (not isinstance(checkpoint, str) or not isinstance(checkpoint_hash, str)
                or not prior.get('batches')
                or prior.get('batches')[-1].get('growth_receipt', {}).get('sha256') != checkpoint_hash
                or Path(prior.get('batches')[-1].get('growth_output', '')).resolve()
                != Path(checkpoint).resolve()
                or incident.get('verified_outgoing_root_count') != prior_roots):
            raise RecoveryUnsafe('Interruption incident does not bind the prior typed checkpoint')
        prior_growth_receipt = prior['batches'][-1].get('growth_receipt')
        expected_parent = (None if prior_growth_receipt is None else {
            'path': prior_growth_receipt.get('path'),
            'accepted_graphs_sha256': prior_growth_receipt.get('sha256'),
        })
        if intent.get('previous_growth') != expected_parent:
            raise RecoveryUnsafe('Interrupted intent parent differs from prior progress checkpoint')
        incident_pids.add(pid)
        incident_requests += starts
        if (current_identity_map_sha256 is not None
                and intent.get('identity_map_sha256') == current_identity_map_sha256):
            same_map_attempts.add(pid)

    if prior_attempted & incident_pids:
        raise RecoveryUnsafe('Prior interruption incident duplicates an attempted PID')
    expected_excluded = sorted(prior_attempted | incident_pids | historical_attempts)
    if ledger.get('excluded_persistent_ids') != expected_excluded:
        raise RecoveryUnsafe('Prior-attempt PID exclusions differ from hash-verified receipts')
    promoted_roots = None
    if ledger_version == 2:
        promotion_path = checked_receipt(ledger.get('promotion_receipt'),
                                         'radio source promotion')
        promotion = _read_json(promotion_path)
        promotion_required = {
            'schema_version', 'producer', 'source_policy', 'source_progress',
            'track_snapshot', 'identity_map', 'baseline_state',
            'baseline_graph_export', 'rebase_report', 'parent_growth',
            'promoted_growth', 'http_requests_before', 'http_requests_after',
            'attempted_pids_before', 'attempted_pids_after',
            'http_request_delta', 'attempt_delta', 'promoted_outgoing_roots',
            'promotion_batches',
        }
        if (set(promotion) != promotion_required
                or promotion.get('schema_version') != 1
                or promotion.get('producer') != 'opengenius-authenticated-radio-growth-promotion-v1'
                or promotion.get('source_policy') != 'authenticated-watch-radio-only-v1'
                or promotion.get('source_progress') != ledger.get('prior_progress')
                or promotion.get('http_requests_before') != prior_requests
                or promotion.get('http_requests_after') != prior_requests
                or promotion.get('http_request_delta') != 0
                or promotion.get('attempted_pids_before') != sorted(expected_excluded)
                or promotion.get('attempted_pids_after') != sorted(expected_excluded)
                or promotion.get('attempt_delta') != 0
                or not isinstance(promotion.get('promotion_batches'), list)
                or not promotion['promotion_batches']):
            raise RecoveryUnsafe('Radio source promotion does not preserve frozen request/PID accounting')
        for key in ('track_snapshot', 'identity_map', 'baseline_state',
                    'baseline_graph_export', 'rebase_report'):
            receipt = promotion.get(key)
            if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                    or not isinstance(receipt.get('path'), str)
                    or not isinstance(receipt.get('sha256'), str)
                    or checked_receipt(receipt, 'promotion ' + key).resolve()
                    != Path(prior_config[key]['path']).resolve()
                    or receipt['sha256'] != prior_config[key]['sha256']):
                raise RecoveryUnsafe('Radio source promotion changed a frozen campaign input')
        parent_growth = (prior.get('batches', [])[-1].get('growth_receipt')
                         if prior.get('batches') else None)
        if promotion.get('parent_growth') != parent_growth:
            raise RecoveryUnsafe('Radio source promotion does not bind the frozen parent graph')
        promoted = promotion.get('promoted_growth')
        if (not isinstance(promoted, dict)
                or set(promoted) != {'path', 'accepted_graphs_sha256'}
                or not isinstance(promoted.get('path'), str)
                or not isinstance(promoted.get('accepted_graphs_sha256'), str)):
            raise RecoveryUnsafe('Radio source promotion lacks its final typed growth receipt')
        promoted_dir = Path(promoted['path']).resolve()
        promoted_graph_path = promoted_dir / 'accepted-graphs.json'
        if _sha(promoted_graph_path.read_bytes()) != promoted['accepted_graphs_sha256']:
            raise RecoveryUnsafe('Promoted accepted graph changed after its receipt')
        promoted_doc = _read_json(promoted_graph_path)
        source_batches = [row for row in prior.get('batches', [])
                          if row.get('outcome') != 'preflight_failed'
                          and row.get('status') != 'preflight_failed']
        promotion_batches = promotion.get('promotion_batches')
        if (len(promotion_batches) != len(source_batches)
                or [row.get('batch') for row in promotion_batches]
                != [row.get('batch') for row in source_batches]):
            raise RecoveryUnsafe('Radio promotion batch list differs from frozen collector progress')
        try:
            from radio_source_integration import prepare_radio_only_snapshots
            parent_doc = _read_json(parent_growth['path'])
            old_run_count = len(parent_doc.get('growth_runs', []))
            promoted_runs = promoted_doc.get('growth_runs')
            if (not isinstance(promoted_runs, list)
                    or len(promoted_runs) != old_run_count + len(promotion_batches)):
                raise ValueError('Radio promotion graph run count differs from its report')
            for index, (source_row, batch_row) in enumerate(
                    zip(source_batches, promotion_batches)):
                batch_dir = Path(source_row['seed_plan']['path']).resolve().parent
                intent_path = batch_dir / 'intent.json'
                summary_path = Path(source_row['collector_summary']['path']).resolve()
                manifest_path = Path(source_row['seed_manifest']['path']).resolve()
                evidence_path = Path(source_row['seed_video_evidence']['path']).resolve()
                original_snapshots = source_row.get('snapshot_inputs')
                expected_batch = {
                    'batch': source_row['batch'],
                    'collector_summary': {'path': str(summary_path),
                                          'sha256': source_row['collector_summary']['sha256']},
                    'seed_manifest': {'path': str(manifest_path),
                                      'sha256': source_row['seed_manifest']['sha256']},
                    'batch_intent': {'path': str(intent_path),
                                     'sha256': _sha(intent_path.read_bytes())},
                    'seed_video_evidence': {'path': str(evidence_path),
                                            'sha256': source_row['seed_video_evidence']['sha256']},
                    'collector_snapshots': original_snapshots,
                }
                if any(batch_row.get(key) != value for key, value in expected_batch.items()):
                    raise ValueError('Radio promotion source receipts differ from frozen batch progress')
                output_dir = batch_row.get('radio_source_output_dir')
                if not isinstance(output_dir, str):
                    raise ValueError('Radio promotion lacks its proof sidecar directory')
                reproduced = prepare_radio_only_snapshots(
                    track_snapshot_path=prior_config['track_snapshot']['path'],
                    identity_map_path=prior_config['identity_map']['path'],
                    batch_dir=batch_dir,
                    collector_summary_path=summary_path,
                    collector_seed_manifest_path=manifest_path,
                    snapshot_paths=[Path(row['path']) for row in original_snapshots],
                    output_dir=output_dir, create_missing=False)
                original_by_path = {row['path']: row for row in original_snapshots}
                proof_by_snapshot = {}
                for proof_receipt in reproduced['proofs']:
                    proof = _read_json(proof_receipt['path'])
                    source = proof.get('collector_snapshot')
                    if (not isinstance(source, dict)
                            or source.get('path') not in original_by_path
                            or source.get('sha256')
                            != original_by_path[source['path']].get('sha256')
                            or source['path'] in proof_by_snapshot):
                        raise ValueError(
                            'Radio proof does not bind one exact frozen collector snapshot')
                    proof_by_snapshot[source['path']] = proof_receipt
                expected_snapshot_inputs = []
                for snapshot_receipt in reproduced['snapshot_inputs']:
                    saved = _read_json(snapshot_receipt['path'])
                    source_receipt = saved.get('radio_only_source_proof')
                    source_path = next((path for path, receipt in proof_by_snapshot.items()
                                        if receipt == source_receipt), None)
                    if (source_path is None
                            or source_receipt != proof_by_snapshot[source_path]):
                        raise ValueError(
                            'Radio-only snapshot does not bind its exact reproduced proof')
                    expected_snapshot_inputs.append({
                        **snapshot_receipt,
                        'radio_only_source_proof': source_receipt})
                if (batch_row.get('radio_source_output_dir') != reproduced.get('output_dir')
                        or batch_row.get('proofs') != reproduced.get('proofs')
                        or batch_row.get('snapshot_inputs') != expected_snapshot_inputs
                        or batch_row.get('ineligible') != reproduced.get('ineligible')):
                    raise ValueError('Radio promotion proof or eligibility disposition did not replay')
                run = promoted_runs[old_run_count + index]
                expected_run_inputs = []
                for snapshot_receipt in reproduced['snapshot_inputs']:
                    saved = _read_json(snapshot_receipt['path'])
                    source_receipt = saved.get('radio_only_source_proof')
                    original_path = next((path for path, receipt in proof_by_snapshot.items()
                                          if receipt == source_receipt), None)
                    if original_path is None:
                        raise ValueError('Radio-only snapshot has a foreign proof pointer')
                    expected_run_inputs.append({**snapshot_receipt,
                                                'radio_only_source_proof': source_receipt})
                summary = _read_json(summary_path)
                manifest = _read_json(manifest_path)
                if (run.get('source_policy') != 'authenticated-watch-radio-only-v1'
                        or run.get('snapshot_inputs') != expected_run_inputs
                        or run.get('collector_summary') != {
                            'path': str(summary_path),
                            'sha256': _sha(summary_path.read_bytes()),
                            'http_requests': summary['http_requests'],
                            'request_budget': summary['request_budget']}
                        or run.get('seed_manifest') != {
                            'path': str(manifest_path),
                            'sha256': _sha(manifest_path.read_bytes()),
                            'seed_count': len(manifest['seeds']),
                            'seed_video_evidence': {
                                'path': str(evidence_path),
                                'sha256': source_row['seed_video_evidence']['sha256']}}):
                    raise ValueError('Promoted growth run differs from its radio proof report')
                if index == 0:
                    transition = run.get('source_policy_transition')
                    if (not isinstance(transition, dict)
                            or transition.get('legacy_run_count') != old_run_count
                            or transition.get('promotion_http_requests') != 0
                            or transition.get('promotion_attempt_delta') != 0):
                        raise ValueError('First radio run lacks the zero-call historical transition')
                elif 'source_policy_transition' in run:
                    raise ValueError('A later radio run repeats the historical policy transition')
        except Exception as error:
            raise RecoveryUnsafe('Radio promotion proofs and per-batch receipts do not fully replay') from error
        try:
            replay = replay_growth_checkpoint(
                previous_growth=promoted_dir,
                track_snapshot=prior_config['track_snapshot']['path'],
                identity_map_path=prior_config['identity_map']['path'],
                baseline_state_path=prior_config['baseline_state']['path'],
                baseline_graph_export_path=prior_config['baseline_graph_export']['path'],
                rebase_report_path=prior_config['rebase_report']['path'])
        except Exception as error:
            raise RecoveryUnsafe('Promoted radio-only growth fails full typed replay') from error
        if (replay['accepted_graphs_sha256'] != promoted['accepted_graphs_sha256']
                or replay['outgoing_root_count'] != promotion.get('promoted_outgoing_roots')):
            raise RecoveryUnsafe('Promoted radio-only graph root count or replay receipt differs')
        promoted_roots = replay['outgoing_root_count']
    if (ledger['http_requests_total'] != prior_requests + incident_requests
            or ledger['base_outgoing_roots'] !=
               (promoted_roots if promoted_roots is not None else prior_roots)):
        raise RecoveryUnsafe('Prior-attempt request or root total differs from verified receipts')
    return {'attempted_pids': set(expected_excluded),
            'same_map_attempted_pids': same_map_attempts,
            'http_requests': ledger['http_requests_total'],
            'outgoing_roots': promoted_roots if promoted_roots is not None else prior_roots}


def _accepted_roots(graph_path):
    doc = _read_json(graph_path)
    rows = doc.get('accepted_roots')
    if not isinstance(rows, list):
        raise ValueError('Accepted graph file has no accepted_roots')
    result = {}
    for row in rows:
        pid, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if not isinstance(pid, str) or not isinstance(targets, list) or pid in result:
            raise ValueError('Malformed or duplicate accepted root')
        if any(not isinstance(target, str) for target in targets) or len(set(targets)) != len(targets):
            raise ValueError('Malformed accepted targets')
        result[pid] = list(targets)
    return result


def choose_batch(tracks, matcher, roots, attempted, count, tie_seed, known_hint_pids=()):
    """Choose target-frontier roots first, balancing canonical artist credits."""
    by_pid = {row['persistent_id']: row for row in tracks}
    incoming = {target for targets in roots.values() for target in targets}
    active_roots = {pid for pid, targets in roots.items() if targets}
    target_only = incoming - active_roots
    candidates = []
    # The graph export can contain indexed empty rows that are neither
    # incoming nor outgoing. They are still unprobed library candidates;
    # exclude only accepted roots and prior attempts, then retain the same
    # incoming-frontier priority below.
    for pid in set(by_pid) - active_roots - set(attempted):
        row = by_pid[pid]
        if not all(isinstance(row.get(key), str) and row[key].strip()
                   for key in ('title', 'artist', 'album')):
            continue
        duration = row.get('duration_ms')
        if type(duration) is not int or duration <= 0:
            continue
        if set(version_facets(row['title'])['kinds']) & {'off_vocal', 'karaoke', 'instrumental'}:
            continue
        credits = tuple(sorted(matcher.artist_credits(row['artist']))) or (row['artist'],)
        candidates.append((pid, row, credits, pid in target_only))
    prior = {pid for pid, edges in roots.items() if edges} | set(attempted)
    loads = Counter()
    for pid in prior:
        row = by_pid.get(pid)
        if row:
            for credit in set(matcher.artist_credits(row.get('artist') or '')):
                loads[credit] += 1
    selected, used_albums, used_credits = [], set(), Counter()
    # Incoming targets are the most promising frontier. Unindexed tracks fill
    # the batch when that frontier is exhausted.
    candidates.sort(key=lambda item: (not item[3], item[0]))
    while candidates and len(selected) < count:
        best_index, best_key = None, None
        for index, (pid, row, credits, is_target) in enumerate(candidates):
            key = (pid not in known_hint_pids,
                   not is_target,
                   min(loads[c] + used_credits[c] for c in credits),
                   row['album'] in used_albums,
                   hashlib.sha256((tie_seed + '\0' + pid).encode()).digest())
            if best_key is None or key < best_key:
                best_index, best_key = index, key
        pid, row, credits, _ = candidates.pop(best_index)
        selected.append(pid)
        used_albums.add(row['album'])
        for credit in set(credits):
            used_credits[credit] += 1
    return selected


def _snapshot_paths(batch_dir):
    return sorted(path for path in batch_dir.glob('collector/*.json')
                  if re.fullmatch(r'[0-9A-F]{16}\.json', path.name))


def _growth_snapshot_receipts(growth_dir):
    if growth_dir is None:
        return []
    doc = _read_json(growth_dir / 'accepted-graphs.json')
    receipts = []
    for run in doc.get('growth_runs', []):
        receipts.extend(run.get('snapshot_inputs', []))
    receipts.extend(doc.get('snapshot_inputs', []))
    unique = {}
    for row in receipts:
        if isinstance(row, dict) and isinstance(row.get('path'), str) and isinstance(row.get('sha256'), str):
            unique[(row['path'], row['sha256'])] = row
    return [{'input': row} for row in unique.values()]


def _validate_growth_receipts(growth_dir):
    if growth_dir is None:
        return
    doc = _read_json(growth_dir / 'accepted-graphs.json')
    if doc.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1':
        raise ValueError('Previous growth is not a typed rebased YT Music merge')

    def check(receipt):
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('path'), str)
                or not isinstance(receipt.get('sha256'), str)):
            raise ValueError('Previous growth has a malformed file receipt')
        path = Path(receipt['path'])
        if _sha(path.read_bytes()) != receipt['sha256']:
            raise ValueError('A source bound by previous growth changed')

    for run in doc.get('growth_runs', []):
        for key in ('collector_summary', 'seed_manifest'):
            check(run.get(key))
        evidence = run.get('seed_manifest', {}).get('seed_video_evidence')
        if evidence is not None:
            check(evidence)
        for receipt in run.get('snapshot_inputs', []):
            check(receipt)


def _valid_collector_batch(summary):
    if (type(summary.get('schema_version')) is not int or summary.get('schema_version') != 1
            or summary.get('account_authentication_verified') is not True):
        return False
    if summary.get('status') == 'completed':
        return (type(summary.get('http_requests')) is int
                and type(summary.get('request_budget')) is int
                and 0 < summary['http_requests'] <= summary['request_budget']
                and 'http_error_status' not in summary)
    return (summary.get('status') == 'stopped'
            and type(summary.get('http_requests')) is int
            and type(summary.get('request_budget')) is int
            and summary['http_requests'] > 0
            and summary['http_requests'] == summary['request_budget']
            and summary.get('error_type') == 'RuntimeError'
            and isinstance(summary.get('error_location'), dict)
            and summary['error_location'].get('function') == 'request'
            and 'http_error_status' not in summary)


def _genuine_snapshot(path):
    try:
        snapshot = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    endpoints = {row.get('endpoint') for row in snapshot.get('requests', [])
                 if isinstance(row, dict)}
    relations = {row.get('relation') for row in snapshot.get('observations', [])
                 if isinstance(row, dict)}
    return (snapshot.get('account_authentication_verified') is True
            and snapshot.get('related_status') == 'collected'
            and {'watch_radio', 'watch_seed', 'song_related'} <= endpoints
            and {'radio', 'related'} <= relations)


def _compose_batch_seed_evidence(current_evidence, external_evidence):
    """Choose collector hints without retagging a rebound source as current-map data."""
    current_rows = current_evidence.get('rows', [])
    current_pids = {row['persistent_id'] for row in current_rows}
    if not external_evidence:
        return current_evidence, current_pids
    if external_evidence.get('producer') == REBOUND_PRODUCER:
        # The rebound selector replays its typed map transition and source
        # receipts. Keep that document intact; current-growth hints can be
        # added in a separately typed composite after they have their own
        # current-map provenance and selector coverage.
        rows = external_evidence.get('rows', [])
        return external_evidence, {row['persistent_id'] for row in rows}

    external_rows = external_evidence.get('rows', [])
    external_pids = {row['persistent_id'] for row in external_rows}
    used_videos = {row['video_id'] for row in external_rows}
    radio_rows = [row for row in current_rows
                  if row['persistent_id'] not in external_pids
                  and row['video_id'] not in used_videos]
    combined = {**current_evidence, 'rows': external_rows + radio_rows,
                **({'catalog_evidence': external_evidence['catalog_evidence']}
                   if external_evidence.get('catalog_evidence') else {}),
                **({'identity_map_input_sha256': external_evidence['identity_map_input_sha256']}
                   if external_evidence.get('identity_map_input_sha256') else {})}
    return combined, current_pids | external_pids


def _dynamic_rebound_hint_pids(current_evidence, rebound_evidence):
    """Return the exact collision-filtered hint frontier used by the typed builder."""
    if (not isinstance(rebound_evidence, dict)
            or rebound_evidence.get('producer') != REBOUND_PRODUCER):
        raise ValueError('Dynamic current-radio hints require typed rebound evidence')
    rebound_rows = rebound_evidence.get('rows', [])
    current_rows = current_evidence.get('rows', [])
    rebound_pids = {row['persistent_id'] for row in rebound_rows}
    rebound_videos = {row['video_id'] for row in rebound_rows}
    filtered = [row for row in current_rows
                if row['persistent_id'] not in rebound_pids
                and row['video_id'] not in rebound_videos]
    return rebound_pids | {row['persistent_id'] for row in filtered}


def _authorized_rebound_retry_pids(document, identity_map_sha256):
    """Return only prior search failures uniquely resolved by a verified rebound sidecar.

    Call after select_seed_video_evidence has replayed and validated the full
    typed transition and all source receipts.
    """
    if not isinstance(document, dict) or document.get('producer') != REBOUND_PRODUCER:
        return set()
    transition = document.get('typed_map_transition') or {}
    old_map = transition.get('old_identity_map') or {}
    new_map = transition.get('new_identity_map') or {}
    if new_map.get('sha256') != identity_map_sha256:
        return set()
    old_hash = old_map.get('sha256')
    result = set()
    for row in document.get('rows', []):
        if not isinstance(row, dict):
            continue
        evidence_rows = [row, *[item for item in row.get('additional_source_evidence', [])
                                if isinstance(item, dict)]]
        if any(item.get('source_relation') == 'rebound_cached_search_candidate'
               and item.get('historical_result_status') == 'search_unresolved'
               and item.get('historical_old_identity_map_sha256') == old_hash
               and item.get('revalidated_identity_map_sha256') == identity_map_sha256
               and isinstance(row.get('persistent_id'), str)
               for item in evidence_rows):
            result.add(row['persistent_id'])
    return result


def _invoke(command):
    return subprocess.run(command, check=False).returncode


def run(args):
    output = args.output.resolve()
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    identity_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    candidate_limit = getattr(args, 'candidate_limit', 100)
    external_evidence_path = getattr(args, 'seed_video_evidence', None)
    external_evidence = None
    if external_evidence_path:
        external_evidence = _read_json(external_evidence_path)
        select_seed_video_evidence(external_evidence, external_evidence_path, [], tracks,
                                   matcher, library_hash, identity_hash)
    dynamic_current = getattr(args, 'dynamic_current_radio_hints', False)
    if dynamic_current and (
            external_evidence is None
            or external_evidence.get('producer') != REBOUND_PRODUCER):
        raise RecoveryUnsafe('Dynamic current-radio hints require the immutable typed rebound source')
    retry_release_pids = _authorized_rebound_retry_pids(external_evidence, identity_hash)
    previous_growth_arg = getattr(args, 'previous_growth', None)
    _validate_growth_receipts(previous_growth_arg)
    output.mkdir(parents=True, exist_ok=True)
    baseline_roots = _accepted_roots(previous_growth_arg / 'accepted-graphs.json'
                                     if previous_growth_arg else args.baseline_graph_export)
    prior_ledger_path = getattr(args, 'prior_attempt_ledger', None)
    prior_ledger = None
    if prior_ledger_path:
        prior_ledger = _validate_prior_attempt_ledger(
            prior_ledger_path, library_hash, _sha(args.track_snapshot.read_bytes()),
            authorized_rebound_retry_pids=retry_release_pids,
            current_identity_map_sha256=identity_hash)
        retry_release_pids -= prior_ledger['same_map_attempted_pids']
        if prior_ledger['http_requests'] > args.global_request_budget:
            raise RecoveryUnsafe('Global request budget is below prior-attempt consumption')
        if prior_ledger['outgoing_roots'] != sum(bool(edges) for edges in baseline_roots.values()):
            raise RecoveryUnsafe('Prior-attempt ledger root count differs from the starting graph')
    progress_path = output / 'progress.json'
    config = {
        'schema_version': 1,
        'track_snapshot': {'path': str(args.track_snapshot.resolve()), 'sha256': _sha(args.track_snapshot.read_bytes())},
        'identity_map': {'path': str(args.identity_map.resolve()), 'sha256': identity_hash},
        'baseline_state': {'path': str(args.baseline_state.resolve()), 'sha256': _sha(args.baseline_state.read_bytes())},
        'baseline_graph_export': {'path': str(args.baseline_graph_export.resolve()), 'sha256': _sha(args.baseline_graph_export.read_bytes())},
        'rebase_report': {'path': str(args.rebase_report.resolve()), 'sha256': _sha(args.rebase_report.read_bytes())},
        'auth_path': str(args.auth.resolve()),
        'external_seed_video_evidence': ({'path': str(external_evidence_path.resolve()),
                                          'sha256': _sha(external_evidence_path.read_bytes())}
                                         if external_evidence_path else None),
        'dynamic_current_radio_hints': getattr(args, 'dynamic_current_radio_hints', False),
        'prior_attempt_ledger': ({'path': str(prior_ledger_path.resolve()),
                                  'sha256': _sha(prior_ledger_path.read_bytes())}
                                 if prior_ledger_path else None),
        'authorized_retry_release_pids': sorted(retry_release_pids),
        'previous_growth': ({'path': str(previous_growth_arg.resolve()),
                             'sha256': _sha((previous_growth_arg / 'accepted-graphs.json').read_bytes())}
                            if previous_growth_arg else None),
        'target_outgoing_roots': args.target_outgoing_roots,
        'global_request_budget': args.global_request_budget,
        'max_attempts': args.max_attempts,
        'batch_size': args.batch_size,
        'batch_request_budget': args.batch_request_budget,
        'candidate_limit': candidate_limit,
        'interval': args.interval,
        'deadline': args.deadline,
    }
    if getattr(args, 'source_policy', None) is not None:
        config['source_policy'] = args.source_policy
    if progress_path.exists():
        progress = _read_json(progress_path)
        if (progress.get('config') != config
                and not (_legacy_progress_compatible(progress, config)
                         or _current_progress_compatible(progress, config))):
            raise ValueError('Resume inputs or runner limits differ from saved progress')
        # Upgrade only completed legacy receipts whose old request caps and
        # no-deadline seed manifests prove the omitted settings.
        progress['config'] = config
        _verify_progress_receipts(
            progress, library_hash, identity_hash,
            (prior_ledger['attempted_pids'] - retry_release_pids) if prior_ledger else (),
            prior_ledger['http_requests'] if prior_ledger else 0)
        _write_atomic(progress_path, progress)
    else:
        progress = {'schema_version': 1, 'config': config, 'status': 'running',
                    'batches': [],
                    'attempted_pids': sorted(prior_ledger['attempted_pids'] - retry_release_pids)
                    if prior_ledger else [],
                    'http_requests': prior_ledger['http_requests'] if prior_ledger else 0,
                    'successful_outgoing_roots': sum(bool(edges) for edges in baseline_roots.values()),
                    'rejected_or_unresolved_pids': []}
        _write_new(progress_path, progress)

    attempted = set(progress['attempted_pids'])
    requests_used = progress['http_requests']
    # Older plan-only runs persisted planned PIDs as if they were attempts.
    # With no network receipts those PIDs are safe to release for collection.
    if not progress['batches'] and requests_used == 0:
        attempted.clear()
        progress['attempted_pids'] = []
    planned_only = set()
    active_growth = previous_growth_arg.resolve() if previous_growth_arg else None
    for record in progress['batches']:
        if record.get('growth_output'):
            active_growth = Path(record['growth_output'])
    roots = _accepted_roots(active_growth / 'accepted-graphs.json'
                            if active_growth else args.baseline_graph_export)
    if progress['successful_outgoing_roots'] != sum(bool(edges) for edges in roots.values()):
        raise ValueError('Saved outgoing-root metric differs from the accepted graph checkpoint')
    batch_number = len(progress['batches']) + 1
    plan_only_batch_count = 0
    script_dir = Path(__file__).resolve().parent

    # Replay the typed baseline and every accepted run before any new request.
    replayed = _replay_checkpoint(args, active_growth)
    if (replayed['outgoing_root_count'] != progress['successful_outgoing_roots']
            or _accepted_roots(active_growth / 'accepted-graphs.json'
                               if active_growth else args.baseline_graph_export) != roots):
        raise RecoveryUnsafe('Current growth checkpoint differs from its full typed replay')

    recorded_batches = {row.get('batch') for row in progress['batches']}
    disk_batches = []
    for path in output.glob('batch-*'):
        match = re.fullmatch(r'batch-(\d{4})', path.name)
        if path.is_dir() and match:
            disk_batches.append((int(match.group(1)), path))
    orphaned = [(number, path) for number, path in disk_batches
                if number not in recorded_batches]
    if orphaned:
        if len(orphaned) != 1 or orphaned[0][0] != batch_number:
            raise RecoveryUnsafe('Uncheckpointed batch directories are ambiguous; no request was repeated')
        orphan_dir = orphaned[0][1]
        if not (orphan_dir / 'intent.json').is_file():
            raise RecoveryUnsafe('Uncheckpointed batch has no durable intent; no request was repeated')
        active_growth, roots = _promote_orphan(
            args, progress, orphan_dir, active_growth, config, library_hash,
            identity_hash, tracks, matcher, script_dir)
        progress['updated_at'] = datetime.now(timezone.utc).isoformat()
        _write_atomic(progress_path, progress)
        attempted = set(progress['attempted_pids'])
        requests_used = progress['http_requests']
        batch_number += 1

    while (progress.get('status') != 'stopped_auth_or_http'
           and progress['successful_outgoing_roots'] < args.target_outgoing_roots
           and len(attempted) + (len(planned_only) if args.plan_only else 0) < args.max_attempts
           and requests_used < args.global_request_budget):
        pause_file = getattr(args, 'pause_file', None)
        stop_after_batches = getattr(args, 'stop_after_batches', None)
        plan_only_batches = getattr(args, 'plan_only_batches', None)
        if args.plan_only and plan_only_batches is not None \
                and plan_only_batch_count >= plan_only_batches:
            progress['status'] = 'paused_at_checkpoint'
            progress['pause_reason'] = 'plan_only_batch_limit'
            break
        if pause_file is not None and Path(pause_file).is_file():
            progress['status'] = 'paused_at_checkpoint'
            progress['pause_reason'] = 'pause_file'
            break
        if stop_after_batches is not None and len(progress['batches']) >= stop_after_batches:
            progress['status'] = 'paused_at_checkpoint'
            progress['pause_reason'] = 'stop_after_batches'
            break
        used_attempt_slots = len(attempted) + (len(planned_only) if args.plan_only else 0)
        count = min(args.batch_size, args.max_attempts - used_attempt_slots, 20)
        # Hints only affect which PID is attempted; they never add a graph edge.
        snapshots = _growth_snapshot_receipts(active_growth)
        evidence = build_seed_video_evidence(snapshots, matcher, library_hash, identity_hash)
        if dynamic_current:
            known_hint_pids = _dynamic_rebound_hint_pids(evidence, external_evidence)
            evidence_doc = None
        else:
            evidence_doc, known_hint_pids = _compose_batch_seed_evidence(
                evidence, external_evidence)
        selected = choose_batch(tracks, matcher, roots, attempted | planned_only, count, library_hash,
                                known_hint_pids)
        if not selected:
            progress['status'] = 'exhausted_candidates'
            break
        batch_dir = output / ('batch-%04d' % batch_number)
        plan = {'schema_version': 1, 'source_library_sha256': library_hash,
                'seeds': [{'persistent_id': pid, 'series': 'automatic-frontier',
                           'sample_kind': 'automatic-frontier'} for pid in selected]}
        if args.plan_only:
            if dynamic_current:
                with tempfile.TemporaryDirectory(prefix='genuine-root-dynamic-plan-') as temp_dir:
                    current_radio_path = Path(temp_dir) / 'current-radio-evidence.json'
                    evidence_path = Path(temp_dir) / 'seed-video-evidence.json'
                    current_radio_path.write_text(
                        json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
                    evidence_doc = build_combined_rebound_seed_video_evidence(
                        external_evidence_path, current_radio_path,
                        library_hash, identity_hash)
                    select_seed_video_evidence(
                        evidence_doc, evidence_path,
                        [{'persistent_id': pid} for pid in selected],
                        tracks, matcher, library_hash, identity_hash)
                    current_hint_count = evidence_doc['audit']['current_radio_hint_count']
            else:
                current_hint_count = None
            print(json.dumps({'batch': batch_number, 'planned_seeds': selected,
                              'known_seed_video_hints': len(known_hint_pids),
                              'dynamic_current_radio_hints': current_hint_count,
                              'http_requests': requests_used}))
            batch_number += 1
            plan_only_batch_count += 1
            planned_only.update(selected)
            continue

        if batch_dir.exists():
            raise RecoveryUnsafe('Uncheckpointed batch was not safely recovered before planning')
        batch_dir.mkdir()
        plan_path = batch_dir / 'seed-plan.json'
        _write_new(plan_path, plan)
        evidence_path = batch_dir / 'seed-video-evidence.json'
        if dynamic_current:
            current_radio_path = batch_dir / 'current-radio-evidence.json'
            _write_new(current_radio_path, evidence)
            evidence_doc = build_combined_rebound_seed_video_evidence(
                external_evidence_path, current_radio_path,
                library_hash, identity_hash)
            _write_new(evidence_path, evidence_doc)
            select_seed_video_evidence(
                evidence_doc, evidence_path,
                [{'persistent_id': pid} for pid in selected],
                tracks, matcher, library_hash, identity_hash)
        else:
            _write_new(evidence_path, evidence_doc)
        source_inputs = [
            _file_receipt(args.track_snapshot), _file_receipt(args.identity_map),
            _file_receipt(args.baseline_state), _file_receipt(args.baseline_graph_export),
            _file_receipt(args.rebase_report),
        ]
        if external_evidence_path:
            source_inputs.append(_file_receipt(external_evidence_path))
        if prior_ledger_path:
            source_inputs.append(_file_receipt(prior_ledger_path))
        intent = {
            'schema_version': 1,
            'runner_config': config,
            'runner_config_sha256': runner_config_sha256(config),
            'source_policy': config.get('source_policy'),
            'library_sha256': library_hash,
            'identity_map_sha256': identity_hash,
            'batch': batch_number,
            'request_budget': min(args.batch_request_budget,
                                  args.global_request_budget - requests_used),
            'seed_pids': selected,
            'source_inputs': source_inputs,
            'seed_plan': _file_receipt(plan_path),
            'seed_video_evidence': _file_receipt(evidence_path),
            'previous_growth': _growth_receipt(active_growth),
        }
        if dynamic_current:
            intent['current_radio_evidence'] = _file_receipt(current_radio_path)
        _write_new(batch_dir / 'intent.json', intent)
        request_budget = min(args.batch_request_budget,
                             args.global_request_budget - requests_used)
        collector_dir = batch_dir / 'collector'
        command = [sys.executable, str(script_dir / 'probe_ytmusic_batch.py'),
                   '--track-snapshot', str(args.track_snapshot),
                   '--identity-map', str(args.identity_map), '--seed-plan', str(plan_path),
                   '--seed-video-evidence', str(evidence_path), '--seeds', str(len(selected)),
                   '--auth', str(args.auth), '--output', str(collector_dir),
                   '--request-budget', str(request_budget), '--interval', str(args.interval),
                   '--candidate-limit', str(candidate_limit)]
        if args.deadline:
            command.extend(['--deadline', args.deadline])
        _invoke(command)
        active_growth, roots = _promote_orphan(
            args, progress, batch_dir, active_growth, config, library_hash,
            identity_hash, tracks, matcher, script_dir)
        attempted = set(progress['attempted_pids'])
        requests_used = progress['http_requests']
        progress['updated_at'] = datetime.now(timezone.utc).isoformat()
        _write_atomic(progress_path, progress)
        batch_number += 1
    else:
        if progress.get('status') != 'stopped_auth_or_http':
            progress['status'] = ('target_reached'
                                  if progress['successful_outgoing_roots'] >= args.target_outgoing_roots
                                  else 'budget_or_attempt_limit')
    if progress.get('status') == 'running':
        progress['status'] = 'target_reached' if progress['successful_outgoing_roots'] >= args.target_outgoing_roots else 'budget_or_attempt_limit'
    progress['attempted_pids'] = sorted(attempted)
    progress['http_requests'] = requests_used
    progress['finished_at'] = datetime.now(timezone.utc).isoformat()
    _write_atomic(progress_path, progress)
    print(json.dumps({'status': progress['status'],
                      'successful_outgoing_roots': progress['successful_outgoing_roots'],
                      'target_outgoing_roots': args.target_outgoing_roots,
                      'attempted_seeds': len(attempted),
                      'planned_seeds': len(planned_only), 'http_requests': requests_used,
                      'growth_output': str(active_growth) if active_growth else None}))
    return progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--baseline-state', type=Path, required=True)
    parser.add_argument('--baseline-graph-export', type=Path, required=True)
    parser.add_argument('--rebase-report', type=Path, required=True)
    parser.add_argument('--previous-growth', type=Path,
                        help='Existing receipt-checked genuine growth directory to extend')
    parser.add_argument('--seed-video-evidence', type=Path,
                        help='Optional strict current-map saved radio/catalog seed-video evidence')
    parser.add_argument('--dynamic-current-radio-hints', action='store_true',
                        help='Build a per-batch typed composite from the rebound sidecar and current accepted radio snapshots')
    parser.add_argument('--source-policy', choices=('authenticated-watch-radio-only-v1',),
                        help='Versioned source qualification policy for new collector snapshots')
    parser.add_argument('--prior-attempt-ledger', type=Path,
                        help='Hash-verified prior-stage request and attempted-PID ledger')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--auth', type=Path, default=Path('../browser.json'))
    parser.add_argument('--target-outgoing-roots', type=int, default=300)
    parser.add_argument('--global-request-budget', type=int, default=3000)
    parser.add_argument('--max-attempts', type=int, default=500)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--batch-request-budget', type=int, default=90)
    parser.add_argument('--candidate-limit', type=int, default=100,
                        help='Maximum radio window per root, forwarded to the authenticated collector')
    parser.add_argument('--interval', type=float, default=3)
    parser.add_argument('--deadline')
    parser.add_argument('--pause-file', type=Path,
                        help='Pause before planning another batch when this file exists')
    parser.add_argument('--stop-after-batches', type=int,
                        help='Pause at a completed checkpoint after this many total batches')
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--plan-only-batches', type=int,
                        help='In plan-only mode, validate at most this many dynamic evidence batches')
    args = parser.parse_args()
    if (args.target_outgoing_roots < 1 or args.global_request_budget < 1
            or args.max_attempts < 1 or not 1 <= args.batch_size <= 20
            or not 1 <= args.batch_request_budget <= 100
            or (args.stop_after_batches is not None and args.stop_after_batches < 0)
            or (args.plan_only_batches is not None and args.plan_only_batches < 1)
            or (args.plan_only_batches is not None and not args.plan_only)
            or not 25 <= args.candidate_limit <= 100
            or not math.isfinite(args.interval) or args.interval < 3):
        parser.error('roots, request budget and attempts must be positive; batch size 1..20; batch request budget 1..100; interval >=3')
    if args.deadline is not None:
        from probe_ytmusic_batch import parse_deadline
        try:
            parse_deadline(args.deadline)
        except ValueError:
            parser.error('--deadline must be a timezone-aware ISO timestamp')
    run(args)


if __name__ == '__main__':
    main()
