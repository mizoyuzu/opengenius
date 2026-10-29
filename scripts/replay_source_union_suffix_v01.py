"""Replay standard collector exports following the fixed, verified source union.

The union's own export remains verified by its immutable original consumer.
Later exports use the ordinary collector envelope, not the union envelope.
"""
import hashlib
import json
from pathlib import Path

PREFIX = Path('/tmp/opengenius-radio-source-union-v06-candidate-v01')
PREFIX_HASH = 'f951fb462d2f31d4e3c95e83d99a7f5dfb63052f96015cf1f52d160a55e50789'
POLICY = 'authenticated-watch-radio-only-v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def replay(previous_dir, accepted, baseline_state, baseline_roots,
           baseline_provenance, matcher, library_hash, identity_hash,
           merger, prefix_consumer):
    previous_dir = Path(previous_dir).resolve()
    prefix = read(PREFIX / 'accepted-graphs.json')
    require(digest(PREFIX / 'accepted-graphs.json') == PREFIX_HASH,
            'Fixed source-union parent changed')
    runs = accepted.get('growth_runs')
    require(isinstance(runs, list) and len(runs) > 2
            and canonical(runs[:2]) == canonical(prefix['growth_runs']),
            'Collector suffix does not extend the exact source-union prefix')
    # Validate the original four prefix exports with the original normal
    # consumer. No saved report is substituted for this replay.
    state, roots, normalized, prefix_hash = prefix_consumer.replay_union_checkpoint(
        PREFIX, prefix, baseline_state, baseline_roots, baseline_provenance,
        matcher, library_hash, identity_hash, merger)
    require(prefix_hash == PREFIX_HASH, 'Source-union replay returned another parent')

    # Recover every real output directory from its immutable parent receipt.
    directories = []
    cursor = previous_dir
    for index in range(len(runs) - 1, 1, -1):
        graph = read(cursor / 'accepted-graphs.json')
        require(canonical(graph.get('growth_runs')) == canonical(runs[:index + 1]),
                'Collector output ancestry has a different run prefix')
        receipt = graph.get('previous_growth_receipt')
        require(isinstance(receipt, dict) and set(receipt) == {'path', 'accepted_graphs_sha256'},
                'Collector output lacks its exact parent receipt')
        parent = Path(receipt['path']).resolve()
        require(parent != cursor and digest(parent / 'accepted-graphs.json') == receipt['accepted_graphs_sha256'],
                'Collector output parent changed')
        directories.append(cursor)
        cursor = parent
    require(cursor == PREFIX, 'Collector ancestry does not end at the fixed source union')

    parent = PREFIX
    for run, directory in zip(runs[2:], reversed(directories)):
        require(isinstance(run, dict) and run.get('producer') is None
                and run.get('source_policy') == POLICY,
                'Only standard authenticated collector runs may follow source union')
        new_roots, verified_run = merger._validate_run_receipt(
            run, matcher.tracks, matcher, library_hash, identity_hash)
        parent_state_hash = state['state_sha256']
        roots, _ = merger._append_graphs(roots, new_roots)
        state = merger.plan_genius_update(state, roots, matcher, library_hash, identity_hash)['next_state']
        require(run.get('resulting_state_sha256') == state['state_sha256'],
                'Collector suffix state seal does not replay')
        verified_run['resulting_state_sha256'] = state['state_sha256']
        normalized.append(verified_run)
        inputs = verified_run['snapshot_inputs']
        graphs, skipped, _ = merger.collect_graphs(
            [Path(row['path']) for row in inputs], matcher, library_hash,
            source_policy=POLICY, identity_map_sha256=identity_hash)
        require(canonical(skipped) == canonical(verified_run['skipped_snapshots']),
                'Collector suffix skipped snapshots do not replay')
        seed = verified_run['seed_manifest']
        evidence = seed.get('seed_video_evidence')
        seed_export = {k: seed[k] for k in ('path', 'sha256', 'seed_count')}
        if evidence is not None:
            seed_export.update(seed_video_evidence_path=evidence['path'],
                               seed_video_evidence_sha256=evidence['sha256'])
        graph_export = {
            'schema_version': 1, 'producer': prefix['producer'],
            'library_sha256': library_hash, 'identity_map_sha256': identity_hash,
            'baseline_state_sha256': prefix['baseline_state_sha256'],
            'direct_parent_state_sha256': parent_state_hash,
            'baseline_rebase_provenance': prefix['baseline_rebase_provenance'],
            'collector_summary': verified_run['collector_summary'],
            'seed_manifest': seed_export, 'source_policy': POLICY,
            'source_policy_transition': prefix['source_policy_transition'],
            'baseline_accepted_roots': prefix['baseline_accepted_roots'],
            'growth_runs': list(normalized),
            'previous_growth_receipt': {'path': str(parent),
                                      'accepted_graphs_sha256': digest(parent / 'accepted-graphs.json')},
            'accepted_roots': roots,
            'new_graphs': [{'root_pid': g['root_pid'], 'ordered_target_pids': g['ordered_target_pids'],
                            'input': g['input'], 'observation_count': len(g['observations']),
                            'source_relations': sorted({r['relation'] for r in g['observations']}),
                            'identity_status': 'unverified_metadata_candidate'} for g in graphs],
            'snapshot_inputs': inputs, 'skipped_snapshots': skipped,
        }
        relations = {'schema_version': 1, 'source_library_sha256': library_hash,
                     'source_state_sha256': state['state_sha256'], 'relation_rows': roots}
        evaluation = {'schema_version': 1, 'library_sha256': library_hash,
                      'relation_rows': roots, 'network_requests': 0,
                      'virtual_state_sha256': state['state_sha256'],
                      'evaluation_scope': 'accepted YT Music recommendation relation graph; new roots are not core-evaluated'}
        for name, wanted in [('accepted-graphs.json', graph_export), ('state.json', state),
                             ('relations.json', relations), ('accepted-evaluation.json', evaluation)]:
            require(canonical(read(directory / name)) == canonical(wanted),
                    'Collector suffix export differs from trusted recomputation: ' + name)
        parent = directory
    return state, roots, normalized, digest(previous_dir / 'accepted-graphs.json')
