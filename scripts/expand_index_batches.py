"""Collect bounded YTMusic batches while advancing a virtual Genius index.

This orchestrator only writes new evidence and synthetic planning artifacts. It
never writes an Apple Library or Genius database.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from evaluate_ytmusic_batch import collect_graphs, merge_graphs
from music_identity_map import IdentityMap, load_library, version_facets
from plan_genius_update import plan_genius_update
from plan_index_expansion import plan_index_expansion
from probe_ytmusic_batch import select_planned_seeds
from seed_video_evidence import select_seed_video_evidence
from update_genius_bundle import state_rows


DEFAULT_EXCLUDED_PIDS = ()
DEFAULT_ATTEMPT_BUDGET = 100
DEFAULT_BATCH_SIZE = 20
DEFAULT_REQUEST_BUDGET = 90
DEFAULT_UNINDEXED_PER_BATCH = 5


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _relation_document(library_hash, state, roots):
    return {
        'schema_version': 1,
        'source_library_sha256': library_hash,
        'source_state_sha256': state['state_sha256'],
        'relation_rows': [
            {'root_pid': row['root_pid'],
             'ordered_target_pids': list(row['ordered_target_pids'])}
            for row in roots
        ],
    }


def _merge_accepted(old_roots, new_roots):
    """Keep every accepted old edge and append genuinely new targets in order."""
    by_root = {}
    for row in old_roots:
        pid = row['root_pid']
        if pid in by_root:
            raise ValueError('Duplicate accepted baseline root')
        by_root[pid] = {**row, 'ordered_target_pids': list(row['ordered_target_pids'])}
    for row in new_roots:
        pid = row['root_pid']
        merged = by_root.setdefault(pid, {**row, 'ordered_target_pids': []})
        seen = set(merged['ordered_target_pids'])
        # If an observation collides with a previous root, old ordered edges win.
        for target in row['ordered_target_pids']:
            if target not in seen:
                merged['ordered_target_pids'].append(target)
                seen.add(target)
        for key, value in row.items():
            if key not in ('root_pid', 'ordered_target_pids'):
                if key == 'snapshot_inputs':
                    merged[key] = [*merged.get(key, []), *value]
                elif key == 'observation_count':
                    merged[key] = merged.get(key, 0) + value
                elif key not in merged:
                    merged[key] = value
    return [by_root[pid] for pid in sorted(by_root)]


def _summary_path(directory):
    paths = sorted(directory.glob('summary-*.json'))
    if len(paths) != 1:
        raise ValueError('Collector output must contain exactly one run summary')
    return paths[0]


def _collector_command(python, script, track_snapshot, identity_map, auth,
                       seed_plan, output, count, request_budget,
                       candidate_limit=25, seed_video_evidence=None):
    return [str(python), str(script), '--track-snapshot', str(track_snapshot),
            '--identity-map', str(identity_map), '--seed-plan', str(seed_plan),
            '--seeds', str(count), '--interval', '5', '--request-budget', str(request_budget),
            '--candidate-limit', str(candidate_limit),
            *(('--seed-video-evidence', str(seed_video_evidence)) if seed_video_evidence else ()),
            '--auth', str(auth), '--output', str(output)]


def _invoke_collector(command):
    """Capture collector output so account or response data cannot reach logs."""
    # Each collector subprocess creates a fresh paced session. Keep its first
    # request at least five seconds after the previous child has finished.
    time.sleep(5)
    return subprocess.run(command, check=False, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)


def _collector_result(directory, returncode):
    summary_path = _summary_path(directory)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    if (not isinstance(summary, dict) or type(summary.get('schema_version')) is not int
            or summary.get('schema_version') != 1
            or type(summary.get('http_requests')) is not int
            or summary['http_requests'] < 0):
        return {'status': 'invalid_summary', 'http_requests': 0, 'results': []}, summary_path, 'collector_invalid_summary'
    status = summary.get('status')
    if status != 'completed' or not summary.get('account_authentication_verified'):
        return summary, summary_path, 'collector_stopped'
    if returncode != 0:
        return summary, summary_path, 'collector_exit_failure'
    return summary, summary_path, None


def _snapshot_paths(batch_dir, seeds):
    paths = [batch_dir / (pid + '.json') for pid in seeds]
    return [path for path in paths if path.is_file()]


def _progress(library_hash, attempt_count, request_count, state, roots,
              attempted_pids, seed_history, valid_seed_pids, target_attempt_count,
              stop_reason=None):
    ids, _, _ = state_rows(state)
    by_root = {row['root_pid']: row['ordered_target_pids'] for row in roots}
    indexed = set(ids)
    all_relation_nodes = set(by_root)
    all_relation_nodes.update(pid for targets in by_root.values() for pid in targets)
    return {
        'schema_version': 1,
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'source_library_sha256': library_hash,
        'requests': request_count,
        'attempted_seeds': attempt_count,
        'target_attempted_seed_count': target_attempt_count,
        'attempted_pids': list(attempted_pids),
        'seed_history': seed_history,
        'seeds_collected': sum(row.get('status') == 'collected' for row in seed_history),
        'seeds_valid': len(set(valid_seed_pids)),
        'indexed_tracks': len(indexed),
        'total_indexed_tracks': len(indexed),
        'roots': len(by_root),
        'edges': sum(len(targets) for targets in by_root.values()),
        'relation_nodes': len(all_relation_nodes),
        'stop_reason': stop_reason,
    }


def _edge_map(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + ' needs relation_rows')
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('root_pid'), str)
                or not isinstance(row.get('ordered_target_pids'), list)
                or row['root_pid'] in result):
            raise ValueError(label + ' has invalid or duplicate relation roots')
        result[row['root_pid']] = row['ordered_target_pids']
    return result


def run_expansion(*, track_snapshot, identity_map_path, baseline_state_path,
                  baseline_relations_path, baseline_evaluation_path,
                  accepted_snapshots_path, output_dir, auth_path,
                  prefetched_batch=None, prefetched_plan=None,
                  seed_video_evidence_path=None, candidate_limit=25,
                  seed_plan_override_path=None,
                  max_requests=450, max_attempts=100,
                  batch_size=20, unindexed_per_batch=DEFAULT_UNINDEXED_PER_BATCH,
                  exclude_pids=(), python=sys.executable,
                  collector_script=None, collector_runner=None):
    """Execute batches. ``collector_runner`` is injectable for offline tests."""
    if type(max_requests) is not int or not 1 <= max_requests <= 500:
        raise ValueError('max_requests must be between 1 and 500')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 200:
        raise ValueError('max_attempts must be between 1 and 200')
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise ValueError('batch_size must be between 1 and 20')
    if type(unindexed_per_batch) is not int or not 0 <= unindexed_per_batch <= batch_size:
        raise ValueError('unindexed_per_batch must be between 0 and batch_size')
    if type(candidate_limit) is not int or not 25 <= candidate_limit <= 100:
        raise ValueError('candidate_limit must be between 25 and 100')
    if seed_plan_override_path is not None and prefetched_batch is not None:
        raise ValueError('Explicit seed plan cannot be combined with a prefetched batch')
    if seed_plan_override_path is not None and seed_video_evidence_path is None:
        raise ValueError('Explicit seed plan requires strict seed-video evidence')
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    output_dir.mkdir(parents=True)
    tracks, library_hash, library_input = load_library(track_snapshot=Path(track_snapshot))
    map_bytes = Path(identity_map_path).read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    evidence_bytes = None
    evidence_document = None
    if seed_video_evidence_path is not None:
        evidence_bytes = Path(seed_video_evidence_path).read_bytes()
        evidence_document = json.loads(evidence_bytes)
    override_bytes = None
    override_plan = None
    if seed_plan_override_path is not None:
        override_bytes = Path(seed_plan_override_path).read_bytes()
        override_plan = json.loads(override_bytes)
    state = json.loads(Path(baseline_state_path).read_text(encoding='utf-8'))
    baseline_relations = json.loads(Path(baseline_relations_path).read_text(encoding='utf-8'))
    baseline_evaluation = json.loads(Path(baseline_evaluation_path).read_text(encoding='utf-8'))
    accepted_snapshots = json.loads(Path(accepted_snapshots_path).read_text(encoding='utf-8'))
    if not isinstance(accepted_snapshots, list):
        raise ValueError('Accepted snapshots must be a list of saved graph provenance')
    for row in accepted_snapshots:
        source = row.get('input') if isinstance(row, dict) else None
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Each accepted baseline graph needs saved input provenance')
        source_path = Path(source['path'])
        if not source_path.is_absolute():
            source_path = Path(__file__).resolve().parents[1] / source_path
        if _sha(source_path.read_bytes()) != source['sha256']:
            raise ValueError('An accepted baseline observation hash changed')
    if baseline_evaluation.get('library_sha256') != library_hash:
        raise ValueError('Baseline evaluation belongs to another Library')
    baseline_rows = baseline_relations.get('relation_rows')
    eval_rows = baseline_evaluation.get('relation_rows')
    eval_edge_map = _edge_map(eval_rows, 'Baseline evaluation')
    baseline_edge_map = _edge_map(baseline_rows, 'Baseline relations')
    if baseline_edge_map != eval_edge_map:
        raise ValueError('Baseline evaluation relations differ from accepted relations')
    baseline_roots = [{'root_pid': row['root_pid'],
                       'ordered_target_pids': list(row['ordered_target_pids'])}
                      for row in baseline_rows]
    provenance_roots = merge_graphs(accepted_snapshots) if accepted_snapshots else []
    if _edge_map([{'root_pid': row['root_pid'],
                   'ordered_target_pids': row['ordered_target_pids']}
                  for row in provenance_roots], 'Saved graph provenance') != baseline_edge_map:
        raise ValueError('Saved accepted graph provenance differs from baseline relations')
    # This also binds the initial graph and state to the supplied Library/hash.
    try:
        plan_index_expansion(tracks, library_hash, state, baseline_relations, 1,
                             excluded_pids=list(exclude_pids))
    except ValueError as error:
        # Candidate exhaustion occurs after full source/state/graph validation.
        if not str(error).startswith('Only '):
            raise
    baseline_check = plan_genius_update(state, baseline_roots, matcher,
                                        library_hash, map_hash)
    if baseline_check['next_state']['state_sha256'] != state['state_sha256']:
        raise ValueError('Baseline relation graph does not reproduce its supplied state')
    attempted = list(dict.fromkeys(exclude_pids))
    override_seeds = None
    if override_plan is not None:
        rows = override_plan.get('seeds') if isinstance(override_plan, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError('Explicit seed plan needs a nonempty seeds list')
        if len(rows) > max_attempts:
            raise ValueError('Explicit seed plan exceeds max_attempts')
        selected = select_planned_seeds(override_plan, tracks, library_hash, len(rows))
        override_pids = [row['persistent_id'] for row in selected]
        if set(override_pids) & set(attempted):
            raise ValueError('Explicit seed plan repeats a previously attempted PID')
        if len(set(override_pids)) != len(override_pids):
            raise ValueError('Explicit seed plan contains duplicate PIDs')
        ids, _, _ = state_rows(state)
        incoming = {target for root in baseline_roots for target in root['ordered_target_pids']}
        roots_seen = {root['root_pid'] for root in baseline_roots}
        eligible_pids = ((incoming & set(ids)) - roots_seen) | ({t['persistent_id'] for t in tracks} - set(ids))
        by_pid = {track['persistent_id']: track for track in tracks}
        for pid in override_pids:
            track = by_pid[pid]
            if pid not in eligible_pids:
                raise ValueError('Explicit seed plan includes a PID that is not an expansion candidate')
            if (not all(isinstance(track.get(field), str) and track[field].strip()
                        for field in ('title', 'artist', 'album'))
                    or type(track.get('duration_ms')) is not int or track['duration_ms'] <= 0
                    or set(version_facets(track['title'])['kinds']) &
                    {'off_vocal', 'karaoke', 'instrumental'}):
                raise ValueError('Explicit seed plan includes an ineligible Library record')
        hints = select_seed_video_evidence(
            evidence_document, seed_video_evidence_path, selected, tracks, matcher,
            library_hash, map_hash)
        if set(hints) != set(override_pids):
            raise ValueError('Every explicit seed must have strict saved-video evidence')
        override_seeds = [row for row in override_plan['seeds']]
    target_attempt_count = len(override_seeds) if override_seeds is not None else max_attempts
    roots = provenance_roots
    raw_snapshots = list(accepted_snapshots)
    requests = 0
    attempted_count = 0
    history, batch_rows, valid_seed_pids = [], [], []
    baseline_state_sha = state['state_sha256']
    baseline_ids = set(state_rows(state)[0])
    original_roots = [dict(root_pid=row['root_pid'],
                           ordered_target_pids=list(row['ordered_target_pids']))
                      for row in roots]
    stop_reason = None
    if collector_runner is None:
        collector_runner = _invoke_collector
    script = Path(collector_script or (Path(__file__).with_name('probe_ytmusic_batch.py'))).resolve()

    def save_progress(reason=None):
        progress = _progress(library_hash, attempted_count, requests, state, roots,
                             attempted, history, valid_seed_pids, target_attempt_count, reason)
        current_ids = set(state_rows(state)[0])
        progress['newly_indexed_tracks'] = len(current_ids - baseline_ids)
        progress['unindexed_library_tracks'] = len({t['persistent_id'] for t in tracks} - current_ids)
        progress_path = output_dir / 'progress.json'
        temporary = output_dir / '.progress.json.tmp'
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
        os.replace(temporary, progress_path)
        return progress

    _write_json(output_dir / 'inputs.json', {
        'schema_version': 1, 'library_input': library_input,
        'input_sha256': {
            'track_snapshot': _sha(Path(track_snapshot).read_bytes()),
            'identity_map': _sha(map_bytes),
            'baseline_state': _sha(Path(baseline_state_path).read_bytes()),
            'baseline_relations': _sha(Path(baseline_relations_path).read_bytes()),
            'baseline_evaluation': _sha(Path(baseline_evaluation_path).read_bytes()),
            'accepted_snapshots': _sha(Path(accepted_snapshots_path).read_bytes()),
            **({'seed_video_evidence': _sha(evidence_bytes)} if evidence_bytes is not None else {}),
            **({'seed_plan_override': _sha(override_bytes)} if override_bytes is not None else {}),
        },
    })

    def accept_batch(batch_number, batch_dir, seeds, summary, summary_path,
                     *, prefetched=False, snapshot_dir=None):
        nonlocal state, roots, raw_snapshots, attempted_count, requests
        attempted.extend(pid for pid in seeds if pid not in attempted)
        attempted_count += len(seeds)
        requests += summary.get('http_requests', 0)
        batch_record = {'batch': batch_number, 'seeds': list(seeds),
                        'http_requests': summary.get('http_requests', 0),
                        'summary': str(summary_path), 'status': summary.get('status'),
                        'prefetched': prefetched}
        batch_rows.append(batch_record)
        history.extend({'persistent_id': row.get('persistent_id'),
                        'status': row.get('status')}
                       for row in summary.get('results', [])
                       if isinstance(row, dict) and row.get('persistent_id'))
        graphs, skipped, inputs = [], [], []
        for pid in seeds:
            path = (snapshot_dir or batch_dir) / (pid + '.json')
            if not path.is_file():
                skipped.append({'path': str(path), 'reason': 'no_snapshot_for_attempted_seed',
                                'expected_root_pid': pid})
                continue
            try:
                snapshot = json.loads(path.read_text(encoding='utf-8'))
                local_seed = snapshot.get('local_seed')
                snapshot_pid = local_seed.get('persistent_id') if isinstance(local_seed, dict) else None
                if snapshot_pid != pid:
                    skipped.append({'path': str(path), 'reason': 'seed_pid_mismatch',
                                    'expected_root_pid': pid, 'snapshot_root_pid': snapshot_pid})
                    continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # collect_graphs records the specific unreadable/invalid JSON reason.
                pass
            try:
                one_graphs, one_skipped, one_inputs = collect_graphs([path], matcher, library_hash)
            except ValueError as error:
                if str(error) != 'No valid recommendation snapshots remain':
                    raise
                skipped.append({'path': str(path), 'reason': 'no_unique_duration_supported_root',
                                'expected_root_pid': pid})
                continue
            inputs.extend(one_inputs)
            skipped.extend(one_skipped)
            if one_graphs and one_graphs[0].get('root_pid') != pid:
                skipped.append({'path': str(path), 'reason': 'candidate_root_pid_mismatch',
                                'expected_root_pid': pid,
                                'candidate_root_pid': one_graphs[0].get('root_pid')})
                continue
            graphs.extend(graph for graph in one_graphs if graph.get('root_pid') == pid)
        new_roots = merge_graphs(graphs)
        valid_seed_pids.extend(row['root_pid'] for row in graphs)
        raw_snapshots.extend(graphs)
        roots = _merge_accepted(roots, new_roots)
        full_rows = [{'root_pid': row['root_pid'],
                      'ordered_target_pids': list(row['ordered_target_pids'])}
                     for row in roots]
        update = plan_genius_update(state, full_rows, matcher, library_hash,
                                    _sha(map_bytes))
        state = update['next_state']
        bound = _relation_document(library_hash, state, full_rows)
        batch_dir.mkdir(parents=True, exist_ok=True)
        accepted_doc = {'schema_version': 1, 'library_sha256': library_hash,
                        'baseline_state_sha256': baseline_state_sha,
                        'accepted_roots': roots, 'new_graphs': new_roots,
                        'skipped_snapshots': skipped, 'snapshot_inputs': inputs}
        _write_json(batch_dir / 'accepted-graphs.json', accepted_doc)
        _write_json(batch_dir / 'accepted-snapshots.json', raw_snapshots)
        _write_json(batch_dir / 'relations.json', bound)
        _write_json(batch_dir / 'state.json', state)
        update['next_state'] = {'state_sha256': state['state_sha256'],
                                'row_count': len(state['rows'])}
        _write_json(batch_dir / 'virtual-update-audit.json', update)
        evaluation = dict(baseline_evaluation)
        evaluation['relation_rows'] = bound['relation_rows']
        evaluation['virtual_state_sha256'] = state['state_sha256']
        evaluation['network_requests'] = 0
        evaluation['new_roots_not_core_evaluated'] = sorted(
            {row['root_pid'] for row in new_roots})
        _write_json(batch_dir / 'evaluation-relations.json', evaluation)
        accepted_evaluation = {
            'schema_version': 1, 'library_sha256': library_hash,
            'relation_rows': bound['relation_rows'],
            'network_requests': 0,
            'virtual_state_sha256': state['state_sha256'],
            'evaluation_scope': 'accepted relation graph; new roots are not core-evaluated',
        }
        _write_json(batch_dir / 'accepted-evaluation.json', accepted_evaluation)
        record = {**batch_record, 'valid_new_graph_count': len(graphs),
                  'skipped_snapshot_count': len(skipped),
                  'skipped_by_reason': dict(sorted(Counter(
                      row.get('reason', 'unspecified') for row in skipped).items())),
                  'snapshot_skip_audit': skipped,
                  'accepted_root_count': len(roots),
                  'accepted_edge_count': sum(len(row['ordered_target_pids']) for row in roots),
                  'indexed_track_count': len(state['rows']),
                  'state_sha256': state['state_sha256'],
                  'relations_path': str(batch_dir / 'relations.json'),
                  'state_path': str(batch_dir / 'state.json'),
                  'accepted_graphs_path': str(batch_dir / 'accepted-snapshots.json'),
                  'evaluation_path': str(batch_dir / 'accepted-evaluation.json')}
        if summary.get('accepted_boundary_stop_reason'):
            record['collector_boundary_stop'] = summary['accepted_boundary_stop_reason']
        _write_json(batch_dir / 'batch-audit.json', record)
        batch_rows[-1] = record
        save_progress()

    # Optional already-collected batch: validate its summary before trusting it.
    if prefetched_batch is not None:
        pre_dir = Path(prefetched_batch).resolve()
        summary_path = _summary_path(pre_dir)
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        if summary.get('status') != 'completed' or not summary.get('account_authentication_verified'):
            boundary_stop = (
                summary.get('status') == 'stopped'
                and summary.get('account_authentication_verified') is True
                and type(summary.get('http_requests')) is int
                and type(summary.get('request_budget')) is int
                and summary['http_requests'] == summary['request_budget']
                and summary.get('error_type') == 'RuntimeError'
                and isinstance(summary.get('error_location'), dict)
                and summary['error_location'].get('function') == 'request'
                and 'http_error_status' not in summary
            )
            if not boundary_stop:
                save_progress('prefetched_batch_not_successfully_authenticated')
                raise ValueError('Prefetched batch did not complete or stop at its request budget boundary')
            summary['accepted_boundary_stop_reason'] = 'request_budget_boundary'
        if summary.get('library_sha256') != library_hash:
            raise ValueError('Prefetched batch belongs to another Library')
        if type(summary.get('http_requests')) is not int or summary['http_requests'] < 0:
            raise ValueError('Prefetched request count is invalid')
        seed_manifest = json.loads((pre_dir / 'seeds.json').read_text(encoding='utf-8'))
        seeds = [row['persistent_id'] for row in seed_manifest.get('seeds', [])]
        if not seeds or len(set(seeds)) != len(seeds) or len(seeds) > batch_size:
            raise ValueError('Prefetched seed manifest is invalid or exceeds batch size')
        if seed_manifest.get('library_sha256') != library_hash:
            raise ValueError('Prefetched seed manifest belongs to another Library')
        if prefetched_plan is None:
            raise ValueError('Prefetched batch requires its original seed plan for hash validation')
        plan_bytes = Path(prefetched_plan).read_bytes()
        if seed_manifest.get('seed_plan_sha256') != _sha(plan_bytes):
            raise ValueError('Prefetched seed manifest does not match the original seed plan')
        plan_doc = json.loads(plan_bytes)
        plan_seeds = select_planned_seeds(plan_doc, tracks, library_hash, len(seeds))
        if [row['persistent_id'] for row in plan_seeds] != seeds:
            raise ValueError('Prefetched collector seeds differ from the original seed plan')
        manifest_by_pid = {row.get('persistent_id'): row for row in seed_manifest['seeds']}
        fields = ('persistent_id', 'title', 'artist', 'album', 'duration_ms')
        for planned in plan_seeds:
            actual = manifest_by_pid.get(planned['persistent_id'])
            if not isinstance(actual, dict) or any(actual.get(key) != planned.get(key) for key in fields):
                raise ValueError('Prefetched seed metadata differs from the exact Library record')
        if set(seeds) & set(attempted):
            raise ValueError('Prefetched batch repeats a previously attempted PID')
        if len(seeds) > max_attempts or summary.get('http_requests', 0) > max_requests:
            raise ValueError('Prefetched batch exceeds global attempt or request budget')
        # Validate supplied snapshot envelopes and only accept files for this batch.
        attempted_count = 0
        batch_dir = output_dir / 'batch-001'
        batch_dir.mkdir()
        (batch_dir / 'seed-plan.json').write_bytes(plan_bytes)
        _write_json(batch_dir / 'seed-plan-audit.json', {
            'schema_version': 1, 'source_library_sha256': library_hash,
            'requested_seed_count': len(seeds), 'selected_seed_count': len(seeds),
            'seed_plan_sha256': _sha(plan_bytes), 'prefetched': True,
            'collector_summary_sha256': _sha(summary_path.read_bytes()),
            'network_requests': 0,
        })
        accept_batch(1, batch_dir, seeds, summary, summary_path,
                     prefetched=True, snapshot_dir=pre_dir)
        save_progress()
    else:
        save_progress()

    while attempted_count < target_attempt_count and requests < max_requests:
        remaining_attempts = target_attempt_count - attempted_count
        count = min(batch_size, remaining_attempts)
        if override_seeds is not None:
            chunk = override_seeds[attempted_count:attempted_count + count]
            seed_plan = {'schema_version': 1, 'source_library_sha256': library_hash,
                         'seeds': chunk}
            audit = {'schema_version': 1, 'source_library_sha256': library_hash,
                     'requested_seed_count': count, 'selected_seed_count': len(chunk),
                     'selected': [{'persistent_id': row['persistent_id'],
                                   'selection_reason': 'explicit_strict_saved_video_plan'}
                                  for row in chunk],
                     'network_requests': 0, 'explicit_override': True,
                     'seed_plan_override_sha256': _sha(override_bytes)}
        else:
            try:
                seed_plan, audit = plan_index_expansion(
                    tracks, library_hash, state, _relation_document(library_hash, state, roots),
                    count, excluded_pids=attempted,
                    unindexed_count=min(unindexed_per_batch, count))
            except ValueError as error:
                if str(error).startswith('Only ') or 'No eligible roots' in str(error):
                    stop_reason = 'no_more_eligible_roots'
                    break
                raise
        batch_number = len(batch_rows) + 1
        batch_dir = output_dir / ('batch-%03d' % batch_number)
        batch_dir.mkdir()
        plan_path, audit_path = batch_dir / 'seed-plan.json', batch_dir / 'seed-plan-audit.json'
        _write_json(plan_path, seed_plan)
        audit.update(batch=batch_number, excluded_pids=attempted.copy())
        _write_json(audit_path, audit)
        seeds = [row['persistent_id'] for row in seed_plan['seeds']]
        req_budget = min(DEFAULT_REQUEST_BUDGET, max_requests - requests)
        command = _collector_command(python, script, track_snapshot, identity_map_path,
                                     auth_path, plan_path, batch_dir, count, req_budget,
                                     candidate_limit=candidate_limit,
                                     seed_video_evidence=seed_video_evidence_path)
        result = collector_runner(command)
        returncode = getattr(result, 'returncode', 0)
        summary, summary_path, failure = _collector_result(batch_dir, returncode)
        attempted.extend(pid for pid in seeds if pid not in attempted)
        attempted_count += len(seeds)
        requests += summary.get('http_requests', 0)
        history.extend({'persistent_id': row.get('persistent_id'), 'status': row.get('status')}
                       for row in summary.get('results', [])
                       if isinstance(row, dict) and row.get('persistent_id'))
        batch_record = {'batch': batch_number, 'seeds': seeds,
                        'http_requests': summary.get('http_requests', 0),
                        'summary': str(summary_path), 'status': summary.get('status'),
                        'prefetched': False}
        batch_rows.append(batch_record)
        if failure:
            stop_reason = failure
            save_progress(stop_reason)
            break
        if summary['http_requests'] > req_budget:
            stop_reason = 'collector_exceeded_batch_request_budget'
            save_progress(stop_reason)
            break
        # accept_batch records attempts itself; undo accounting already applied above.
        attempted_count -= len(seeds)
        requests -= summary.get('http_requests', 0)
        history = history[:-len(summary.get('results', []))] if summary.get('results') else history
        batch_rows.pop()
        accept_batch(batch_number, batch_dir, seeds, summary, summary_path)
        if requests > max_requests:
            stop_reason = 'global_request_budget_exhausted'
            break
    if stop_reason is None and attempted_count >= target_attempt_count:
        stop_reason = 'attempt_budget_reached'
    if stop_reason is None and requests >= max_requests:
        stop_reason = 'request_budget_reached'
    final_progress = save_progress(stop_reason)
    _write_json(output_dir / 'run-audit.json', {
        'schema_version': 1, 'status': 'stopped' if stop_reason not in
        ('attempt_budget_reached', 'request_budget_reached', 'no_more_eligible_roots') else 'completed',
        'stop_reason': stop_reason, 'batch_count': len(batch_rows),
        'attempted_seed_count': attempted_count, 'request_count': requests,
        'target_attempted_seed_count': target_attempt_count,
        'baseline_state_sha256': baseline_state_sha,
        'baseline_indexed_pid_count': len(state_rows(json.loads(
            Path(baseline_state_path).read_text(encoding='utf-8')))[0]),
        'virtual_state_only': True, 'persisted_database_updated': False,
        'persisted_indexed_pid_count': None,
        'final_virtual_state_sha256': state['state_sha256'],
        'baseline_root_graph_preserved': all(
            next((row['ordered_target_pids'] for row in roots if row['root_pid'] == old['root_pid']), None)
            == old['ordered_target_pids'] for old in original_roots),
        'batches': batch_rows,
    })
    return final_progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--baseline-state', type=Path, required=True)
    parser.add_argument('--baseline-relations', type=Path, required=True)
    parser.add_argument('--baseline-evaluation', type=Path, required=True)
    parser.add_argument('--accepted-snapshots', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--auth', type=Path, required=True)
    parser.add_argument('--prefetched-batch', type=Path)
    parser.add_argument('--prefetched-plan', type=Path,
                        help='Original seed plan whose hash is recorded by a prefetched collector')
    parser.add_argument('--seed-video-evidence', type=Path)
    parser.add_argument('--seed-plan', type=Path,
                        help='Explicit plan to run in chunks; every PID must have strict saved-video evidence')
    parser.add_argument('--candidate-limit', type=int, default=25)
    parser.add_argument('--max-requests', type=int, default=450)
    parser.add_argument('--max-attempts', type=int, default=DEFAULT_ATTEMPT_BUDGET)
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--unindexed-per-batch', type=int, default=DEFAULT_UNINDEXED_PER_BATCH)
    parser.add_argument('--exclude-pid', action='append', default=[])
    args = parser.parse_args()
    excluded = list(dict.fromkeys([*DEFAULT_EXCLUDED_PIDS, *args.exclude_pid]))
    try:
        progress = run_expansion(
            track_snapshot=args.track_snapshot, identity_map_path=args.identity_map,
            baseline_state_path=args.baseline_state,
            baseline_relations_path=args.baseline_relations,
            baseline_evaluation_path=args.baseline_evaluation,
            accepted_snapshots_path=args.accepted_snapshots, output_dir=args.output,
            auth_path=args.auth, prefetched_batch=args.prefetched_batch,
            prefetched_plan=args.prefetched_plan,
            seed_video_evidence_path=args.seed_video_evidence,
            seed_plan_override_path=args.seed_plan,
            candidate_limit=args.candidate_limit,
            max_requests=args.max_requests, max_attempts=args.max_attempts,
            batch_size=args.batch_size, unindexed_per_batch=args.unindexed_per_batch,
            exclude_pids=excluded)
    except Exception as error:
        # Do not echo exception text: it can contain service or local auth data.
        parser.exit(1, 'Stopped (' + type(error).__name__ + '). No automatic retry.\n')
    print(json.dumps({key: progress[key] for key in
                      ('requests', 'attempted_seeds', 'indexed_tracks', 'roots', 'edges', 'stop_reason')}))


if __name__ == '__main__':
    main()
