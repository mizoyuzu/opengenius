"""Fail-closed recovery validation for interrupted genuine-root batches."""
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime


class RecoveryUnsafe(ValueError):
    """The orphaned batch cannot be continued without risking a duplicate call."""


def runner_config_sha256(config):
    if (not isinstance(config, dict)
            or type(config.get('batch_request_budget')) is not int
            or not 1 <= config['batch_request_budget'] <= 450
            or 'deadline' not in config):
        raise RecoveryUnsafe('Runner config must bind batch request budget and deadline policy')
    batch_size = config.get('batch_size')
    if batch_size is not None and (type(batch_size) is not int or not 1 <= batch_size <= 100):
        raise RecoveryUnsafe('Runner config has an invalid batch size')
    scaled = ((type(batch_size) is int and batch_size > 20)
              or config['batch_request_budget'] > 100)
    if scaled and (config.get('source_policy') != 'authenticated-watch-radio-only-v1'
                   or config.get('collector_collection_mode') != 'radio-only-v1'):
        raise RecoveryUnsafe('Scaled runner config requires authenticated radio-only source and collector modes')
    deadline = config['deadline']
    if deadline is not None:
        try:
            parsed = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        except (AttributeError, ValueError):
            raise RecoveryUnsafe('Runner config has an invalid deadline') from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RecoveryUnsafe('Runner config deadline must include a timezone')
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def scaled_collection_manifest_compatible(planned_seed_count, request_budget, manifest):
    scaled = planned_seed_count > 20 or request_budget > 100
    return (not scaled or
            (isinstance(manifest, dict)
             and manifest.get('collector_collection_mode') == 'radio-only-v1'
             and manifest.get('seed_query_fallback') is not True))


def replay_growth_checkpoint(*, previous_growth, track_snapshot, identity_map_path,
                             baseline_state_path, baseline_graph_export_path,
                             rebase_report_path):
    """Reproduce the whole previous chain before allowing a new HTTP batch."""
    try:
        from merge_rebased_radio_growth import (
            _replay_previous_growth, _verify_rebase_receipt,
        )
        from music_identity_map import IdentityMap, load_library
        from plan_genius_update import plan_genius_update

        tracks, library_hash, _ = load_library(track_snapshot=Path(track_snapshot))
        map_bytes = Path(identity_map_path).read_bytes()
        identity_hash = hashlib.sha256(map_bytes).hexdigest()
        matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
        graph_doc, _, baseline_state, provenance = _verify_rebase_receipt(
            baseline_graph_export_path, rebase_report_path, baseline_state_path,
            library_hash, identity_hash)
        baseline_replay = plan_genius_update(
            baseline_state, graph_doc['accepted_roots'], matcher,
            library_hash, identity_hash)['next_state']
        if baseline_replay['state_sha256'] != baseline_state['state_sha256']:
            raise ValueError('Verified baseline does not reproduce its typed state')
        if previous_growth is None:
            roots = graph_doc['accepted_roots']
            return {'state_sha256': baseline_state['state_sha256'], 'roots': roots,
                    'accepted_graphs_sha256': None,
                    'outgoing_root_count': sum(bool(row['ordered_target_pids']) for row in roots)}
        state, roots, _, accepted_hash = _replay_previous_growth(
            previous_growth, baseline_state, graph_doc['accepted_roots'],
            provenance, matcher, library_hash, identity_hash)
        return {'state_sha256': state['state_sha256'], 'roots': roots,
                'accepted_graphs_sha256': accepted_hash,
                'outgoing_root_count': sum(bool(row['ordered_target_pids']) for row in roots)}
    except Exception as error:
        raise RecoveryUnsafe('Previous growth did not replay against the verified rebase') from error


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RecoveryUnsafe('A recovery receipt is missing or unreadable') from None


def _check_file_receipt(receipt, label):
    if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
            or not isinstance(receipt.get('path'), str)
            or not isinstance(receipt.get('sha256'), str)):
        raise RecoveryUnsafe('Malformed ' + label + ' receipt')
    path = Path(receipt['path']).resolve()
    if sha(path) != receipt['sha256']:
        raise RecoveryUnsafe(label + ' changed after the batch intent was written')
    return path


def _valid_boundary(summary):
    return (summary.get('status') == 'stopped'
            and summary.get('account_authentication_verified') is True
            and type(summary.get('http_requests')) is int
            and type(summary.get('request_budget')) is int
            and summary['http_requests'] > 0
            and summary['http_requests'] == summary['request_budget']
            and summary.get('error_type') == 'RuntimeError'
            and isinstance(summary.get('error_location'), dict)
            and summary['error_location'].get('function') == 'request'
            and 'http_error_status' not in summary)


def _valid_collector(summary):
    if (type(summary.get('schema_version')) is not int or summary.get('schema_version') != 1
            or summary.get('account_authentication_verified') is not True):
        return False
    if summary.get('status') == 'completed':
        return (type(summary.get('http_requests')) is int
                and type(summary.get('request_budget')) is int
                and 0 < summary['http_requests'] <= summary['request_budget']
                and 'http_error_status' not in summary)
    return _valid_boundary(summary)


def _known_selector_preflight_failure(summary):
    location = summary.get('error_location')
    return (type(summary.get('schema_version')) is int
            and summary.get('schema_version') == 1
            and summary.get('status') == 'stopped'
            and summary.get('account_authentication_verified') is False
            and type(summary.get('http_requests')) is int
            and summary.get('http_requests') == 0
            and summary.get('results') == []
            and summary.get('partial_seed_persistent_id') is None
            and summary.get('error_type') == 'ValueError'
            and isinstance(location, dict)
            and location.get('file') == 'merge_rebased_radio_growth.py'
            and location.get('function') == '_replay_previous_growth'
            and 'http_error_status' not in summary)


def _snapshot_is_genuine(snapshot):
    endpoints = {row.get('endpoint') for row in snapshot.get('requests', [])
                 if isinstance(row, dict)}
    relations = {row.get('relation') for row in snapshot.get('observations', [])
                 if isinstance(row, dict)}
    return (snapshot.get('account_authentication_verified') is True
            and snapshot.get('related_status') == 'collected'
            and {'watch_radio', 'watch_seed', 'song_related'} <= endpoints
            and {'radio', 'related'} <= relations)


def _verify_growth_output(path, intent, collector_receipt, manifest_receipt,
                          genuine_snapshot_receipts):
    graph_path = Path(path) / 'accepted-graphs.json'
    graph = _read(graph_path)
    if graph.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1':
        raise RecoveryUnsafe('Orphan growth output is not a typed rebased merge')
    runs = graph.get('growth_runs')
    if not isinstance(runs, list) or not runs:
        raise RecoveryUnsafe('Orphan growth output has no replayable growth run')
    last = runs[-1]
    last_collector = last.get('collector_summary')
    last_manifest = last.get('seed_manifest')
    if (not isinstance(last_collector, dict)
            or any(last_collector.get(key) != value for key, value in collector_receipt.items())
            or not isinstance(last_manifest, dict)
            or any(last_manifest.get(key) != value for key, value in manifest_receipt.items())
            or last.get('snapshot_inputs') != genuine_snapshot_receipts):
        raise RecoveryUnsafe('Orphan growth output does not bind this collector batch')
    expected_parent = intent.get('previous_growth')
    actual_parent = graph.get('previous_growth_receipt')

    def normalized_parent(receipt):
        if receipt is None:
            return None
        if (not isinstance(receipt, dict)
                or set(receipt) != {'path', 'accepted_graphs_sha256'}
                or not isinstance(receipt.get('path'), str)
                or not isinstance(receipt.get('accepted_graphs_sha256'), str)):
            return receipt
        parent_path = Path(receipt['path']).resolve()
        if parent_path.name == 'accepted-graphs.json':
            parent_path = parent_path.parent
        return {'path': str(parent_path),
                'accepted_graphs_sha256': receipt['accepted_graphs_sha256']}

    if normalized_parent(expected_parent) != normalized_parent(actual_parent):
        raise RecoveryUnsafe('Orphan growth output has a different parent checkpoint')
    return {'path': str(graph_path.resolve()), 'sha256': sha(graph_path),
            'active_outgoing_roots': sum(bool(row.get('ordered_target_pids'))
                                         for row in graph.get('accepted_roots', []))}


def _verify_dynamic_current_radio_binding(intent, evidence, config):
    """Require each dynamic composite to bind the batch's immutable radio file."""
    if config.get('dynamic_current_radio_hints') is True:
        current_receipt = intent.get('current_radio_evidence')
        current_path = _check_file_receipt(current_receipt, 'current-radio evidence')
        bound_current = evidence.get('current_radio_source')
        if (evidence.get('producer') != 'opengenius-combined-rebound-seed-evidence-v1'
                or bound_current != {'path': str(current_path),
                                     'sha256': current_receipt['sha256']}):
            raise RecoveryUnsafe('Dynamic composite does not bind its intent current-radio evidence')
    elif 'current_radio_evidence' in intent:
        raise RecoveryUnsafe('Static evidence intent unexpectedly includes current-radio evidence')


def recover_orphan_batch(*args, **kwargs):
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope
    with radio_snapshot_proof_cache_scope():
        return _recover_orphan_batch_with_radio_proof_cache(*args, **kwargs)


def _recover_orphan_batch_with_radio_proof_cache(batch_dir, *, expected_config_sha256,
                         library_sha256, identity_map_sha256,
                         expected_previous_growth=None,
                         expected_request_budget=None):
    """Validate complete saved work and return receipts for checkpoint recovery.

    An absent summary, incomplete run, authentication/HTTP stop, changed source,
    or a mismatch between collector and typed merge fails closed. The caller
    must replay an existing growth output through the merger's receipt checker
    before promoting this result into progress.json.
    """
    batch_dir = Path(batch_dir).resolve()
    intent_path = batch_dir / 'intent.json'
    intent = _read(intent_path)
    config = intent.get('runner_config')
    if runner_config_sha256(config) != expected_config_sha256:
        raise RecoveryUnsafe('Orphan batch config hash does not match its immutable config')
    if (intent.get('schema_version') != 1
            or intent.get('runner_config_sha256') != expected_config_sha256
            or intent.get('library_sha256') != library_sha256
            or intent.get('identity_map_sha256') != identity_map_sha256
            or intent.get('source_policy') != config.get('source_policy')):
        raise RecoveryUnsafe('Orphan batch intent differs from current sources or runner configuration')
    if intent.get('previous_growth') != expected_previous_growth:
        raise RecoveryUnsafe('Orphan batch was planned from a different growth checkpoint')
    source_receipts = intent.get('source_inputs')
    if not isinstance(source_receipts, list):
        raise RecoveryUnsafe('Orphan batch lacks source input receipts')
    config_source_keys = ('track_snapshot', 'identity_map', 'baseline_state',
                          'baseline_graph_export', 'rebase_report')
    configured_sources = []
    for key in config_source_keys:
        receipt = config.get(key)
        if not isinstance(receipt, dict):
            raise RecoveryUnsafe('Runner config lacks a bound ' + key + ' source')
        configured_sources.append({'path': receipt.get('path'),
                                   'sha256': receipt.get('sha256')})
    for optional_key, label in (
            ('external_seed_video_evidence', 'external evidence'),
            ('prior_attempt_ledger', 'prior-attempt ledger')):
        optional_source = config.get(optional_key)
        if optional_source is not None:
            if not isinstance(optional_source, dict):
                raise RecoveryUnsafe('Runner config has malformed ' + label + ' source')
            configured_sources.append({'path': optional_source.get('path'),
                                       'sha256': optional_source.get('sha256')})
    normalized_sources = []
    for receipt in source_receipts:
        source_path = _check_file_receipt(receipt, 'source input')
        normalized_sources.append({'path': str(source_path), 'sha256': receipt['sha256']})
    expected_sources = []
    for receipt in configured_sources:
        if (not isinstance(receipt.get('path'), str)
                or not isinstance(receipt.get('sha256'), str)):
            raise RecoveryUnsafe('Runner config has a malformed source receipt')
        expected_sources.append({'path': str(Path(receipt['path']).resolve()),
                                 'sha256': receipt['sha256']})
    if normalized_sources != expected_sources:
        raise RecoveryUnsafe('Orphan source receipt set differs from runner config')
    # The immutable config binds the starting checkpoint. Later batches bind
    # their evolving parent through intent.previous_growth and typed replay.
    if intent.get('batch') == 1:
        configured_parent = config.get('previous_growth')
        if configured_parent is None:
            expected_parent = None
        else:
            configured_path = Path(configured_parent['path'])
            if configured_path.is_dir():
                configured_path = configured_path / 'accepted-graphs.json'
            expected_parent = {
                'path': str(configured_path.resolve()),
                'accepted_graphs_sha256': configured_parent['sha256'],
            }
        if expected_parent != expected_previous_growth:
            raise RecoveryUnsafe('Runner config parent receipt differs from first checkpoint')
    plan_path = _check_file_receipt(intent.get('seed_plan'), 'seed plan')
    evidence_path = _check_file_receipt(intent.get('seed_video_evidence'), 'seed-video evidence')
    plan = _read(plan_path)
    if (plan.get('schema_version') != 1 or plan.get('source_library_sha256') != library_sha256
            or not isinstance(plan.get('seeds'), list) or not plan['seeds']):
        raise RecoveryUnsafe('Orphan batch seed plan is not bound to the current Library')
    planned = [row.get('persistent_id') for row in plan['seeds'] if isinstance(row, dict)]
    if len(planned) != len(plan['seeds']) or len(planned) != len(set(planned)):
        raise RecoveryUnsafe('Orphan batch seed plan has duplicate or malformed PIDs')
    if intent.get('seed_pids') != planned:
        raise RecoveryUnsafe('Orphan batch intent seed list differs from its plan')
    evidence = _read(evidence_path)
    if (evidence.get('library_sha256') != library_sha256
            or evidence.get('identity_map_sha256') != identity_map_sha256):
        raise RecoveryUnsafe('Orphan seed-video evidence has foreign source hashes')
    _verify_dynamic_current_radio_binding(intent, evidence, config)

    collector_dir = batch_dir / 'collector'
    summaries = sorted(collector_dir.glob('summary-*.json'))
    if not summaries:
        raise RecoveryUnsafe('Collector summary is absent; refusing to repeat a possibly partial batch')
    if len(summaries) != 1:
        raise RecoveryUnsafe('Multiple collector summaries make the orphan batch ambiguous')
    summary_path = summaries[0]
    summary = _read(summary_path)
    preflight_failure = _known_selector_preflight_failure(summary)
    if not _valid_collector(summary) and not preflight_failure:
        raise RecoveryUnsafe('Collector did not finish or stop at an authenticated request boundary')
    if summary.get('library_sha256') != library_sha256:
        raise RecoveryUnsafe('Collector summary is bound to a different Library')
    if (type(intent.get('request_budget')) is not int
            or summary.get('request_budget') != intent['request_budget']
            or intent['request_budget'] > config['batch_request_budget']
            or (expected_request_budget is not None
                and intent['request_budget'] != expected_request_budget)):
        raise RecoveryUnsafe('Collector request cap differs from its durable batch intent')
    collector_receipt = {'path': str(summary_path.resolve()), 'sha256': sha(summary_path),
                         'http_requests': summary['http_requests'],
                         'request_budget': summary['request_budget']}
    if preflight_failure:
        # This exact historical selector failure occurs before auth or request
        # setup. Recover it only when the collector directory contains no
        # manifest, cache/request records, snapshots, or any other output.
        unexpected = [path for path in collector_dir.rglob('*')
                      if path.is_file() and path != summary_path]
        if unexpected:
            raise RecoveryUnsafe('Preflight failure has request, cache, or collector output files')
        return {
            'batch': intent.get('batch'),
            'seed_pids': planned,
            'attempted_pids': [],
            'http_requests': 0,
            'status': 'preflight_failed',
            'outcome': 'preflight_failed',
            'collector_summary': collector_receipt,
            'preflight_intent_receipt': {
                'path': str(intent_path.resolve()), 'sha256': sha(intent_path)},
            'seed_plan': intent['seed_plan'],
            'seed_manifest': None,
            'seed_video_evidence': intent['seed_video_evidence'],
            'snapshot_inputs': [],
            'genuine_snapshot_inputs': [],
            'growth_receipt': None,
            'requires_typed_growth_replay': False,
        }
    manifest_path = collector_dir / 'seeds.json'
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_receipt = {'path': str(manifest_path.resolve()),
                        'sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                        'seed_count': len(planned)}
    manifest_pids = [row.get('persistent_id') for row in manifest.get('seeds', [])
                     if isinstance(row, dict)]
    if not scaled_collection_manifest_compatible(
            len(planned), intent.get('request_budget', 0), manifest):
        raise RecoveryUnsafe('Scaled collector manifest must bind radio-only collection without title fallback')
    if (manifest.get('library_sha256') != library_sha256
            or manifest.get('identity_map_sha256') != identity_map_sha256
            or manifest.get('seed_plan_sha256') != intent['seed_plan']['sha256']
            or manifest.get('seed_video_evidence_sha256') != intent['seed_video_evidence']['sha256']
            or manifest_pids != planned):
        raise RecoveryUnsafe('Collector seed manifest does not exactly bind the orphan plan and hints')

    results = summary.get('results')
    if not isinstance(results, list):
        raise RecoveryUnsafe('Collector summary has no result list')
    result_pids = [row.get('persistent_id') for row in results if isinstance(row, dict)]
    if (len(result_pids) != len(results) or len(result_pids) != len(set(result_pids))
            or set(result_pids) - set(planned)
            or any(row.get('status') not in ('collected', 'search_unresolved', 'deadline_partial')
                   for row in results)):
        raise RecoveryUnsafe('Collector results contain duplicate or unplanned PIDs')
    partial = summary.get('partial_seed_persistent_id')
    if partial is not None and (partial not in planned or partial in result_pids):
        raise RecoveryUnsafe('Collector partial seed receipt is inconsistent')
    attempted = set(result_pids)
    if partial:
        attempted.add(partial)

    collected = {row['persistent_id'] for row in results if row.get('status') == 'collected'}
    snapshot_receipts, genuine_receipts = [], []
    original_snapshot_paths = []
    for path in sorted(collector_dir.glob('[0-9A-F]' * 16 + '.json')):
        pid = path.stem
        if pid not in collected:
            raise RecoveryUnsafe('Orphan collector directory has a snapshot without a collected result')
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        try:
            from evaluate_ytmusic_batch import validate_observations
            validate_observations(snapshot, library_sha256)
        except Exception as error:
            raise RecoveryUnsafe('Orphan snapshot failed observation validation') from error
        if (snapshot.get('library_sha256') != library_sha256
                or snapshot.get('identity_map_sha256') != identity_map_sha256
                or snapshot.get('account_authentication_verified') is not True
                or snapshot.get('local_seed', {}).get('persistent_id') != pid):
            raise RecoveryUnsafe('Orphan snapshot is not bound to its authenticated seed and source map')
        receipt = {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}
        snapshot_receipts.append(receipt)
        original_snapshot_paths.append(path.resolve())
        if _snapshot_is_genuine(snapshot):
            genuine_receipts.append(receipt)
    if collected != {Path(receipt['path']).stem for receipt in snapshot_receipts}:
        raise RecoveryUnsafe('A collected seed is missing its saved snapshot')

    radio_source = None
    if config.get('source_policy') == 'authenticated-watch-radio-only-v1':
        try:
            from radio_source_integration import prepare_radio_only_snapshots
            radio_source = prepare_radio_only_snapshots(
                track_snapshot_path=config['track_snapshot']['path'],
                identity_map_path=config['identity_map']['path'],
                batch_dir=batch_dir,
                collector_summary_path=summary_path,
                collector_seed_manifest_path=manifest_path,
                snapshot_paths=original_snapshot_paths)
        except Exception as error:
            raise RecoveryUnsafe('Radio-only source proofs failed to build or replay') from error
        genuine_receipts = radio_source['snapshot_inputs']
    elif config.get('source_policy') is not None:
        raise RecoveryUnsafe('Orphan runner config has an unsupported source policy')

    growth_dir = batch_dir / 'growth'
    growth_receipt = None
    if growth_dir.exists():
        growth_receipt = _verify_growth_output(
            growth_dir, intent, collector_receipt, manifest_receipt, genuine_receipts)
    return {
        'batch': intent.get('batch'),
        'seed_pids': planned,
        'attempted_pids': sorted(attempted),
        'http_requests': summary['http_requests'],
        'status': summary['status'],
        'outcome': ('merged' if growth_receipt else
                    'authenticated_no_graphs' if not genuine_receipts else 'ready_to_merge'),
        'collector_summary': collector_receipt,
        'seed_plan': intent['seed_plan'],
        'seed_manifest': manifest_receipt,
        'seed_video_evidence': intent['seed_video_evidence'],
        'snapshot_inputs': snapshot_receipts,
        'genuine_snapshot_inputs': genuine_receipts,
        'radio_only_source': radio_source,
        'growth_receipt': growth_receipt,
        'requires_typed_growth_replay': growth_receipt is not None,
    }
