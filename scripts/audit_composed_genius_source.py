"""Source partition checks for a historical Genius graph plus own-radio roots."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def vectors(rows):
    require(isinstance(rows, list), 'graph root rows must be a list')
    result = {}
    for row in rows:
        pid, targets = row.get('root_pid'), row.get('ordered_target_pids')
        require(isinstance(pid, str) and pid and pid not in result,
                'graph contains a missing or duplicate root PID')
        require(isinstance(targets, list)
                and all(isinstance(t, str) and t for t in targets)
                and len(targets) == len(set(targets)) and pid not in targets,
                'graph contains invalid, duplicate or self targets')
        result[pid] = list(targets)
    return result


def validate_partition(old_rows, final_rows, own_sources, *, native_pids,
                       historical_yt_pids, known_pids, minimum_roots=1000):
    """Require exact preservation and exact, outgoing-source-backed additions.

    ``own_sources`` must already have passed its stage's independent raw-receipt
    audit and the union's global identity-collision filtering. Incoming rows and
    catalog matches are deliberately absent from this source set.
    """
    old, final = vectors(old_rows), vectors(final_rows)
    old_active = {pid for pid, targets in old.items() if targets}
    final_active = {pid for pid, targets in final.items() if targets}
    native, historical = set(native_pids), set(historical_yt_pids)
    require(not native & historical and old_active == native | historical,
            'historical roots are not exactly partitioned by source')
    require(set(old) <= set(final), 'an established graph row was removed')
    require(all(final[pid] == targets for pid, targets in old.items() if targets),
            'an established outgoing recommendation vector changed')
    own = {}
    for source in own_sources:
        pid, targets = source.get('root_pid'), source.get('ordered_target_pids')
        checked = vectors([{'root_pid': pid, 'ordered_target_pids': targets}])[pid]
        require(checked, 'a new own-radio source has no outgoing targets')
        require(pid not in old_active, 'a historical root was counted as newly collected')
        require(pid not in own or own[pid] == checked,
                'repeated own-radio sources disagree on recommendation order')
        own[pid] = checked
    require(all(final.get(pid) == targets for pid, targets in own.items()),
            'final outgoing vector differs from its own-radio evidence')
    require(final_active == old_active | set(own),
            'final active graph contains unsupported or missing outgoing roots')
    require(set(final) <= known_pids
            and all(set(targets) <= known_pids for targets in final.values()),
            'graph references a PID outside the provided Library')
    require(len(final_active) >= minimum_roots,
            'fewer than the required total genuine outgoing roots')
    return {
        'native_only_pids': sorted(native),
        'ytmusic_source_backed_pids': sorted(historical | set(own)),
        'native_ytmusic_overlap_pids': [], 'unknown_pids': [],
        'counts': {'native_only': len(native),
                   'ytmusic_source_backed': len(historical | set(own)),
                   'native_ytmusic_overlap': 0, 'unknown': 0,
                   'total_active': len(final_active)},
        'new_own_radio_roots': len(own),
        'ordered_edge_count': sum(map(len, final.values())),
    }


def verify_closure(closure):
    require(isinstance(closure, dict) and closure, 'source file closure is missing')
    physical = {path: digest for path, digest in closure.items()
                if not path.startswith('receipt-original:')}
    for path, digest in physical.items():
        require(Path(path).is_file() and sha(path) == digest,
                'source file changed: ' + path)
    # Historical mutable worktree code may have been preserved byte-for-byte
    # under a pinned runtime. The old audit records both identities explicitly.
    for key, digest in closure.items():
        if key.startswith('receipt-original:'):
            original = Path(key.removeprefix('receipt-original:'))
            require(original.suffix == '.py' and any(
                Path(path).name == original.name and actual == digest
                for path, actual in physical.items()),
                'historical code receipt has no matching archived source file')


def audit_union(union_dir, historical_path, historical_sha, output):
    """Recheck raw new sources and compose them with a fully replayed parent.

    The historical auditor proves the old 870 independently. Completed-stage
    and interrupted-prefix auditors prove every new own radio/seed pair. This
    final check recomputes collisions and the exact combined outgoing vectors;
    it never promotes a metadata candidate or an incoming-only row to a root.
    """
    import compose_work_video_source_union as source
    import audit_work_video_radio as stage_audit
    from music_identity_map import IdentityMap, load_library
    from plan_genius_update import plan_genius_update

    union_dir, historical_path, output = map(
        lambda p: Path(p).resolve(), (union_dir, historical_path, output))
    require(not output.exists(), 'source audit output must be new')
    require(sha(historical_path) == historical_sha,
            'reviewed historical source audit hash differs')
    historical = read(historical_path)
    require(historical.get('producer') ==
            'opengenius-independent-historical-exhausted-870-source-audit-v1'
            and historical.get('status') == 'passed_historical_exhausted_source_replay'
            and historical.get('independent_full_chain_replay', {}).get('status')
            == 'passed_historical_exhausted_870'
            and historical.get('final_files_sha256', {}).get('accepted_graphs')
            == source.PINS['old_graph'],
            'historical 870 roots lack their full independent source replay')
    closure = dict(historical.get('source_closure_sha256', {}))
    verify_closure(closure)
    input_paths, _, _, _ = source._verify_inputs()
    closure.update({str(path.resolve()): sha(path) for path in input_paths.values()})
    graph_path = union_dir / 'candidate-union-graphs.json'
    state_path = union_dir / 'candidate-state.json'
    composition_path = union_dir / 'composition-receipt.json'
    graph, state, composition = map(read, (graph_path, state_path, composition_path))
    require(composition.get('candidate_graph_sha256') == sha(graph_path)
            and composition.get('candidate_state_sha256') == sha(state_path),
            'candidate files differ from their composition receipt')
    require(graph.get('library_sha256') ==
            '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
            and graph.get('identity_map_sha256') == source.PINS['identity_map'],
            'composed graph belongs to a different Library or identity map')
    old_graph = read(source.OLD_GRAPH)
    binder = source.verify_frozen_runtime(source.RUNTIME)
    _, _, official, tracks, _ = binder.load_binding_inputs(
        source.WORK_BINDINGS, source.WORK_REPORT, source.WORK_HTML,
        source.TRACK_SNAPSHOT, repo=source.ROOT)
    batches = []
    receipts = composition.get('input_receipts', {})
    require(receipts == graph.get('source_receipts'),
            'graph and composition provenance differ')
    stage_rows = receipts.get('audited_stages', [])
    require(isinstance(stage_rows, list) and stage_rows,
            'composed graph has no independently audited radio stages')
    stage_paths = []
    for row in stage_rows:
        pin_path = row.get('stage_audit_pin_path')
        if pin_path is None:
            require(not batches, 'legacy hard-pinned first stage appears twice')
            stage, audit_dir = source.WORK_STAGE, source.WORK_AUDIT
            stage_binder, pin = binder, None
            runtime_path = source.RUNTIME
        else:
            pin = source.load_reviewed_stage_pin(
                Path(pin_path), row['stage_audit_pin_sha256'])
            stage, audit_dir = Path(pin['stage_path']), Path(pin['audit_dir'])
            profile = read(pin['reviewed_profile_path'])
            runtime_path = Path(pin['runtime_path'])
            stage_binder = source.verify_frozen_runtime(
                Path(pin['runtime_path']), pin['runtime_manifest_sha256'],
                profile.get('runtime_module_count', 187))
            stage_binder = stage_audit.adapt_reviewed_binder(stage_binder, official, tracks)
            closure[str(Path(pin_path))] = row['stage_audit_pin_sha256']
            closure[pin['reviewed_profile_path']] = pin['reviewed_profile_sha256']
            closure.update(profile['capture_input_pins'])
        manifest_path = runtime_path / 'runtime-manifest.json'
        closure[str(manifest_path)] = sha(manifest_path)
        for module in read(manifest_path)['modules']:
            closure[str(runtime_path / module['path'])] = module['sha256']
        require(stage not in stage_paths, 'a capture stage was counted twice')
        stage_paths.append(stage)
        batch = source.load_audited_work_stage(
            stage, audit_dir, binder=stage_binder, official=official,
            local_tracks=tracks, stage_pin=pin,
            stage_pin_path=Path(pin_path) if pin_path else None,
            stage_pin_sha256=row.get('stage_audit_pin_sha256'))
        require(batch['receipt_sha256'] == row['audit_receipt_sha256']
                and batch['validated_graph_sha256'] == row['validated_graph_sha256'],
                'stage replay differs from composed source provenance')
        batches.append(batch)
        for path in (stage / 'process.json', stage / 'candidate-graphs.json',
                     audit_dir / 'audit-receipt.json',
                     audit_dir / 'validated-candidate-graphs.json'):
            closure[str(path)] = sha(path)
        for entry in read(stage / 'process.json')['request_cache_manifest']:
            closure[str(stage / entry['path'])] = entry['sha256']
    for row in receipts.get('audited_interrupted_prefixes', []):
        pin_path = Path(row['prefix_audit_pin_path'])
        batch = source.load_reviewed_work_prefix(pin_path, row['prefix_audit_pin_sha256'])
        require(batch['prefix_receipt_sha256'] == row['prefix_receipt_sha256']
                and batch['prefix_projection_sha256'] == row['prefix_projection_sha256'],
                'interrupted prefix replay differs from composition provenance')
        batches.append(batch)
        closure[str(pin_path)] = row['prefix_audit_pin_sha256']
        pin = read(pin_path)
        for key in ('prefix_receipt_path', 'prefix_projection_path', 'reviewed_profile_path'):
            closure[pin[key]] = sha(pin[key])
        stage = Path(pin['stage_path'])
        for name in ('process.json', 'progress.json'):
            closure[str(stage / name)] = sha(stage / name)
        for entry in read(stage / 'process.json')['request_cache_manifest']:
            closure[str(stage / entry['path'])] = entry['sha256']
    bad_pids, bad_videos = source.global_ambiguities([b['bindings'] for b in batches])
    own = []
    for batch in batches:
        for item in batch['sources']:
            if item['root_pid'] in bad_pids or item['video_id'] in bad_videos:
                continue
            targets = [pid for pid in item['ordered_target_pids'] if pid not in bad_pids]
            if targets:
                own.append({**item, 'ordered_target_pids': targets})
    historical_origins = historical['source_root_origin']
    native = set(historical_origins['native_only_pids'])
    old_yt = set(historical_origins['ytmusic_source_backed_pids'])
    require(len(native) == 159 and len(old_yt) == 711
            and not historical_origins.get('unknown_pids')
            and not historical_origins.get('native_ytmusic_overlap_pids'),
            'historical source origin does not prove 159 Native plus 711 YT roots')
    origins = validate_partition(old_graph['accepted_roots'], graph['accepted_roots'], own,
        native_pids=native, historical_yt_pids=old_yt,
        known_pids={track['persistent_id'] for track in tracks})
    loaded_tracks, library_sha, _ = load_library(track_snapshot=source.TRACK_SNAPSHOT)
    matcher = IdentityMap(read(source.IDENTITY_MAP), loaded_tracks, library_sha)
    recomputed = plan_genius_update(read(source.OLD_STATE), graph['accepted_roots'],
                                   matcher, library_sha, source.PINS['identity_map'])
    # The planner uses tuples for equivalence labels; persisted JSON uses
    # arrays. Compare the complete wire representation, including every field.
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False)
    require(canonical(recomputed['next_state']) == canonical(state),
            'candidate synthetic state does not encode the source-backed graph')
    for path in (historical_path, graph_path, state_path, composition_path,
                 source.TRACK_SNAPSHOT, source.IDENTITY_MAP, Path(__file__).resolve(),
                 Path(source.__file__).resolve(), Path(stage_audit.__file__).resolve(),
                 Path(__file__).with_name('audit_work_video_prefix_candidates.py'),
                 Path(__file__).with_name('audit_final_ytmusic_source_v23.py')):
        closure[str(path)] = sha(path)
    verify_closure(closure)
    document = {
        'schema_version': 1,
        'producer': 'opengenius-independent-composed-radio-source-audit-v1',
        'status': 'passed', 'goal_target_reached': True,
        'historical_audit': {'path': str(historical_path), 'sha256': historical_sha},
        'source_root_origin': {**origins, 'goal_metric': 'total-genuine',
                               'qualifying_root_count': origins['counts']['total_active']},
        'independent_full_chain_replay': {
            'status': 'passed', 'kind': 'historical_full_replay_plus_each_own_radio_stage',
            'checkpoint': str(union_dir),
            'outgoing_root_count': origins['counts']['total_active'],
            'accepted_graphs_sha256': sha(graph_path), 'state_sha256': sha(state_path),
            'replayed_state_sha256': state['state_sha256'], 'graph_receipt_matches': True},
        'final_files_sha256': {'accepted_graphs': sha(graph_path), 'state': sha(state_path)},
        'source_closure_sha256': closure,
        'global_ambiguous_new_pids': sorted(bad_pids),
        'global_ambiguous_new_videos': sorted(bad_videos),
        'catalog_roots_credited': 0, 'incoming_roots_credited': 0,
        'network_requests': 0, 'database_written': False,
        'recording_identity_verified': False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--union-dir', type=Path, required=True)
    parser.add_argument('--historical-audit', type=Path, required=True)
    parser.add_argument('--historical-audit-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit_union(args.union_dir, args.historical_audit,
                         args.historical_audit_sha256, args.output)
    print(json.dumps({'status': result['status'],
                      'counts': result['source_root_origin']['counts'],
                      'output_sha256': sha(args.output)}, sort_keys=True))


if __name__ == '__main__':
    main()
