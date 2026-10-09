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

from music_identity_map import IdentityMap, load_library, version_facets
from seed_video_evidence import build_seed_video_evidence, select_seed_video_evidence
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
    return record


def _promote_orphan(args, progress, batch_dir, active_growth, config,
                    library_hash, identity_hash, tracks, matcher, script_dir):
    expected_parent = _growth_receipt(active_growth)
    recovered = recover_orphan_batch(
        batch_dir,
        expected_config_sha256=runner_config_sha256(config),
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
            graphs, _, _ = collect_graphs(genuine_paths, matcher, library_hash)
        except ValueError:
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


def _verify_progress_receipts(progress, library_hash, identity_hash):
    attempted_from_records = set()
    requests_from_records = 0
    for row in progress.get('batches', []):
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
        growth = row.get('growth_receipt')
        if growth is not None and _sha(Path(growth['path']).read_bytes()) != growth['sha256']:
            raise ValueError('A saved growth checkpoint changed')
    if progress.get('batches'):
        if attempted_from_records != set(progress.get('attempted_pids', [])):
            raise ValueError('Progress attempted PID list differs from exact collector result receipts')
        if requests_from_records != progress.get('http_requests'):
            raise ValueError('Progress request total differs from exact collector summary receipts')
    elif progress.get('http_requests') != 0 and attempted_from_records != set(progress.get('attempted_pids', [])):
        raise ValueError('Progress without batches has unreceipted network attempts')


def _legacy_progress_compatible(progress, config):
    old = progress.get('config')
    legacy = {key: value for key, value in config.items()
              if key not in ('batch_request_budget', 'deadline')}
    if old != legacy or config['deadline'] is not None:
        return False
    records = progress.get('batches', [])
    if not records:
        return True
    budgets = []
    for row in records:
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
    indexed = set(roots) | incoming
    target_only = incoming - {pid for pid, targets in roots.items() if targets}
    unindexed = set(by_pid) - indexed
    candidates = []
    for pid in target_only | unindexed:
        row = by_pid[pid]
        if pid in attempted or not all(isinstance(row.get(key), str) and row[key].strip()
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
    previous_growth_arg = getattr(args, 'previous_growth', None)
    _validate_growth_receipts(previous_growth_arg)
    output.mkdir(parents=True, exist_ok=True)
    baseline_roots = _accepted_roots(previous_growth_arg / 'accepted-graphs.json'
                                     if previous_growth_arg else args.baseline_graph_export)
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
    if progress_path.exists():
        progress = _read_json(progress_path)
        if progress.get('config') != config and not _legacy_progress_compatible(progress, config):
            raise ValueError('Resume inputs or runner limits differ from saved progress')
        # Upgrade only completed legacy receipts whose old request caps and
        # no-deadline seed manifests prove the omitted settings.
        progress['config'] = config
        _verify_progress_receipts(progress, library_hash, identity_hash)
        _write_atomic(progress_path, progress)
    else:
        progress = {'schema_version': 1, 'config': config, 'status': 'running',
                    'batches': [], 'attempted_pids': [], 'http_requests': 0,
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
        used_attempt_slots = len(attempted) + (len(planned_only) if args.plan_only else 0)
        count = min(args.batch_size, args.max_attempts - used_attempt_slots, 20)
        # Hints only affect which PID is attempted; they never add a graph edge.
        snapshots = _growth_snapshot_receipts(active_growth)
        evidence = build_seed_video_evidence(snapshots, matcher, library_hash, identity_hash)
        evidence_doc = evidence
        known_hint_pids = {row['persistent_id'] for row in evidence.get('rows', [])}
        if external_evidence:
            external_rows = external_evidence.get('rows', [])
            external_pids = {row['persistent_id'] for row in external_rows}
            used_videos = {row['video_id'] for row in external_rows}
            radio_rows = [row for row in evidence.get('rows', [])
                          if row['persistent_id'] not in external_pids
                          and row['video_id'] not in used_videos]
            evidence_doc = {**evidence, 'rows': external_rows + radio_rows,
                            **({'catalog_evidence': external_evidence['catalog_evidence']}
                               if external_evidence.get('catalog_evidence') else {}),
                            **({'identity_map_input_sha256': external_evidence['identity_map_input_sha256']}
                               if external_evidence.get('identity_map_input_sha256') else {})}
            known_hint_pids.update(external_pids)
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
            print(json.dumps({'batch': batch_number, 'planned_seeds': selected,
                              'known_seed_video_hints': len(evidence['rows']),
                              'http_requests': requests_used}))
            batch_number += 1
            planned_only.update(selected)
            continue

        if batch_dir.exists():
            raise RecoveryUnsafe('Uncheckpointed batch was not safely recovered before planning')
        batch_dir.mkdir()
        plan_path = batch_dir / 'seed-plan.json'
        _write_new(plan_path, plan)
        evidence_path = batch_dir / 'seed-video-evidence.json'
        _write_new(evidence_path, evidence_doc)
        source_inputs = [
            _file_receipt(args.track_snapshot), _file_receipt(args.identity_map),
            _file_receipt(args.baseline_state), _file_receipt(args.baseline_graph_export),
            _file_receipt(args.rebase_report),
        ]
        if external_evidence_path:
            source_inputs.append(_file_receipt(external_evidence_path))
        intent = {
            'schema_version': 1,
            'runner_config': config,
            'runner_config_sha256': runner_config_sha256(config),
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
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if (args.target_outgoing_roots < 1 or args.global_request_budget < 1
            or args.max_attempts < 1 or not 1 <= args.batch_size <= 20
            or not 1 <= args.batch_request_budget <= 100
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
