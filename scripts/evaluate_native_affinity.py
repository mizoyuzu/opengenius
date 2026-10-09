"""Compare MusicCore affinity through config spacing and actual edge order.

All graph changes are an in-memory derived experiment. The accepted radio
graph, source bundle, and Library files are read-only inputs.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from evaluate_ytmusic_batch import evaluate_graphs
from emulate_ytmusic_candidates import controlled_configs
from genius_profiles import artist_preference_config, seed_affinity_order
from music_identity_map import IdentityMap


def sha(data):
    return hashlib.sha256(data).hexdigest()


def early_affinity_exposure(rows, root_pid, artist_key, album_key, rank_key=None):
    """Summarize near-seed exposure, counting recommendation slots after the seed.

    Rows may be ordered playlist entries (rank_key=None) or saved replay rows
    carrying an explicit rank. Mean ranks are one-based among non-seed tracks.
    """
    candidates = [row for row in rows if row.get('persistent_id') != root_pid]
    if rank_key is None:
        ranked = [(rank, row) for rank, row in enumerate(candidates, 1)]
    else:
        ranked = [(row[rank_key] - 1, row) for row in candidates]
        if any(type(rank) is not int or rank < 1 for rank, _ in ranked):
            raise ValueError('Candidate ranks must follow the seed and be positive')
        ranked.sort(key=lambda pair: pair[0])

    def matched(row, key):
        return row.get(key) is True

    def summarize(predicate):
        matching_ranks = [rank for rank, row in ranked if predicate(row)]
        return {
            'first_5_count': sum(rank <= 5 for rank in matching_ranks),
            'first_10_count': sum(rank <= 10 for rank in matching_ranks),
            'mean_rank': (sum(matching_ranks) / len(matching_ranks)
                          if matching_ranks else None),
        }

    return {
        'recommendation_slot_basis': 'seed excluded; first_5 is the first five non-seed tracks',
        'artist_credit_matches': summarize(lambda row: matched(row, artist_key)),
        'album_title_matches': summarize(lambda row: matched(row, album_key)),
        'both_artist_and_album_matches': summarize(
            lambda row: matched(row, artist_key) and matched(row, album_key)),
    }


def affinity_order_graphs(graphs, tracks, matcher, root_pids, preference='slight'):
    """Copy selected graphs and reorder only their already-observed targets."""
    if not isinstance(graphs, list) or not isinstance(root_pids, (list, tuple)) or not root_pids:
        raise ValueError('Need graph rows and a nonempty root subset')
    if not isinstance(preference, str) or preference not in ('slight', 'moderate'):
        raise ValueError('Edge-order experiment requires slight or moderate affinity')
    if len(set(root_pids)) != len(root_pids) or any(not isinstance(pid, str) or not pid for pid in root_pids):
        raise ValueError('Invalid or duplicate root subset')
    selected = set(root_pids)
    by_root = {}
    for graph in graphs:
        if (not isinstance(graph, dict) or not isinstance(graph.get('root_pid'), str)
                or not isinstance(graph.get('ordered_target_pids'), list)):
            raise ValueError('Invalid graph row')
        if graph['root_pid'] in by_root:
            raise ValueError('Duplicate graph root')
        by_root[graph['root_pid']] = graph
    if selected - set(by_root):
        raise ValueError('Affinity root is not present in the source graph')
    result, provenance = copy.deepcopy(graphs), {}
    for graph in result:
        root = graph['root_pid']
        if root not in selected:
            continue
        targets = graph['ordered_target_pids']
        if len(targets) != len(set(targets)) or root in targets:
            raise ValueError('Source targets must be unique and exclude their root')
        ordered, affinity = seed_affinity_order([root, *targets], tracks, matcher, root, preference)
        reordered = [pid for pid in ordered if pid != root]
        if len(reordered) != len(targets) or set(reordered) != set(targets):
            raise ValueError('Affinity ordering changed the observed target set')
        graph['ordered_target_pids'] = reordered
        provenance[root] = {
            'preference': preference,
            'candidate_count': len(targets),
            'original_target_order': list(targets),
            'affinity_target_order': list(reordered),
            'candidate_pid_set_unchanged': True,
            'candidate_pid_count_unchanged': True,
            'affinity': affinity,
        }
    return result, provenance


def _playlist_metrics(row, matcher, tracks, root_pid):
    root_artist = matcher.artist_credits(tracks[root_pid].get('artist') or '')
    nonroot = [entry for entry in row['playlist'] if entry['persistent_id'] != root_pid]
    by_pid = {entry['persistent_id']: entry for entry in nonroot}
    artist_matches = album_matches = both_matches = 0
    for pid in by_pid:
        track = tracks[pid]
        artist_key = matcher.artist_credits(track.get('artist') or '')
        artist_match = (bool((tracks[root_pid].get('artist') or '').strip()) and bool(artist_key)
                        and artist_key == root_artist)
        root_album = (tracks[root_pid].get('album') or '').strip().casefold()
        album = (track.get('album') or '').strip().casefold()
        album_match = bool(root_album and album and root_album == album)
        artist_matches += bool(artist_match)
        album_matches += bool(album_match)
        both_matches += bool(artist_match and album_match)
    outside_artist_credits = {
        tuple(sorted(matcher.artist_credits(tracks[pid].get('artist') or '')))
        for pid in by_pid
        if not ((tracks[pid].get('artist') or '').strip()
                and matcher.artist_credits(tracks[pid].get('artist') or '') == root_artist)
    }
    return {
        'generated_nonroot_count': len(nonroot),
        'seed_artist_credit_match_count': artist_matches,
        'seed_artist_credit_match_share': artist_matches / len(nonroot) if nonroot else None,
        'seed_album_title_match_count': album_matches,
        'seed_album_title_match_share': album_matches / len(nonroot) if nonroot else None,
        'both_seed_artist_and_album_match_count': both_matches,
        'both_seed_artist_and_album_match_share': both_matches / len(nonroot) if nonroot else None,
        'early_affinity_exposure': early_affinity_exposure(
            row['playlist'], root_pid, 'seed_artist_credit_match', 'seed_album_title_match'),
        'outside_seed_artist_credit_diversity': len(outside_artist_credits),
        'direct_radio_candidate_count': sum(item.get('observed_relation_hops') == 1 for item in row['playlist']),
        'indirect_radio_candidate_count': sum(type(item.get('observed_relation_hops')) is int
                                             and item['observed_relation_hops'] > 1 for item in row['playlist']),
        'unreachable_from_root_count': sum(item.get('observed_relation_hops') is None for item in row['playlist']),
    }


def _condition_rows(evaluation, tracks, matcher):
    rows = []
    for result in evaluation['root_results']:
        rows.append({
            'root_pid': result['root_pid'],
            'status': result['status'],
            'profile': result['profile'],
            'artist_preference': result['artist_preference'],
            'playlist_pids': result['playlist_pids'],
            'metrics': _playlist_metrics(result, matcher,
                                         {pid: tracks[pid] for pid in tracks}, result['root_pid']),
            'playlist': result['playlist'],
            'music_core_instructions': result['core_result'].get('instructions', 0),
        })
    return rows


def run_experiment(graphs, matcher, config, executable, tracks, root_pids, limit=25,
                   *, edge_preference='slight'):
    """Run the three core conditions against a fixed PID metadata universe."""
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be 1..100')
    roots = set(root_pids)
    source = [copy.deepcopy(graph) for graph in graphs if graph['root_pid'] in roots]
    if {graph['root_pid'] for graph in source} != roots:
        raise ValueError('Each selected root needs an accepted source graph')
    # Accepted radio rows are a compact PID graph, not raw snapshot envelopes.
    # The normal evaluator also carries these evidence-count fields, so supply
    # empty metadata-only defaults in this derived in-memory view.
    for graph in source:
        graph.setdefault('observations', [])
        graph.setdefault('candidate_groups', [])
    controlled = controlled_configs(config)['without-compatible-genre']
    base_config = artist_preference_config(controlled, 'neutral')
    spacing_config = artist_preference_config(controlled, 'moderate')
    edge_graphs, edge_provenance = affinity_order_graphs(
        source, list(tracks.values()), matcher, root_pids, edge_preference)
    common = dict(limit=limit, profile='without-compatible-genre', root_pids=list(root_pids))
    baseline = evaluate_graphs(source, matcher, base_config, executable, **common)
    spacing = evaluate_graphs(source, matcher, spacing_config, executable,
                              artist_preference='moderate', **common)
    edge_order = evaluate_graphs(edge_graphs, matcher, base_config, executable, **common)
    reports = {
        'baseline': baseline,
        'artist_spacing_moderate': spacing,
        'radio_edge_order_affinity_' + edge_preference: edge_order,
    }
    comparisons = {}
    indexed = {name: {row['root_pid']: row for row in _condition_rows(evaluation, tracks, matcher)}
               for name, evaluation in reports.items()}
    for root_pid in root_pids:
        comparisons[root_pid] = {
            name: rows[root_pid] for name, rows in indexed.items()
        }
        before_targets = source[[g['root_pid'] for g in source].index(root_pid)]['ordered_target_pids']
        after_targets = edge_graphs[[g['root_pid'] for g in edge_graphs].index(root_pid)]['ordered_target_pids']
        if len(before_targets) != len(after_targets) or set(before_targets) != set(after_targets):
            raise ValueError('Edge-order condition changed the observed candidate set')
    deltas = {}
    for root_pid, conditions in comparisons.items():
        baseline_row = conditions['baseline']
        deltas[root_pid] = {}
        for name, row in conditions.items():
            base_pids, pids = baseline_row['playlist_pids'], row['playlist_pids']
            metric_delta = {
                key: row['metrics'][key] - baseline_row['metrics'][key]
                for key in ('seed_artist_credit_match_count', 'seed_album_title_match_count',
                            'both_seed_artist_and_album_match_count',
                            'outside_seed_artist_credit_diversity')
            }
            deltas[root_pid][name] = {
                'playlist_pid_set_unchanged_from_baseline': set(base_pids) == set(pids),
                'playlist_order_identical_to_baseline': base_pids == pids,
                'positions_changed_from_baseline': sum(left != right for left, right in zip(base_pids, pids)),
                'metric_count_deltas_from_baseline': metric_delta,
            }
    return {
        'profile': 'without-compatible-genre',
        'limit': limit,
        'root_pids': list(root_pids),
        'input_config_sha256': sha(config),
        'baseline_controlled_config_sha256': sha(base_config),
        'artist_spacing_moderate_controlled_config_sha256': sha(spacing_config),
        'edge_affinity_preference': edge_preference,
        'edge_ordering': edge_provenance,
        'comparisons': comparisons,
        'condition_deltas': deltas,
        'root_metadata': {
            root: {key: tracks[root].get(key) for key in ('title', 'artist', 'album')}
            for root in root_pids
        },
        'all_conditions_same_root_set': True,
        'edge_order_condition_pid_sets_unchanged': True,
        'native_selection_order_verified': False,
        'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
        'identity_status': 'unverified',
        'network_requests': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('accepted-graphs', 'track-snapshot', 'identity-map', 'genius-bundle', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--root-pid', action='append', required=True)
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--edge-preference', choices=('slight', 'moderate'), default='slight')
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve(strict=True) for name in
             ('accepted_graphs', 'track_snapshot', 'identity_map', 'genius_bundle', 'executable')}
    output = args.output.resolve()
    if output.exists() or any(output == path or output.is_relative_to(path)
                              for path in paths.values() if path.is_file()):
        parser.error('Output must be a new file separate from input files')
    if output.is_relative_to(paths['genius_bundle']):
        parser.error('Output must remain outside the source Library bundle')

    snapshot_bytes = paths['track_snapshot'].read_bytes()
    snapshot = json.loads(snapshot_bytes)
    tracks = snapshot['tracks']
    library_hash = snapshot['source_sha256']
    graph_bytes = paths['accepted_graphs'].read_bytes()
    graph_doc = json.loads(graph_bytes)
    if graph_doc.get('library_sha256') != library_hash:
        parser.error('Accepted graphs and track snapshot Library hashes differ')
    map_bytes = paths['identity_map'].read_bytes()
    identity_document = json.loads(map_bytes)
    matcher = IdentityMap(identity_document, tracks, library_hash)
    track_map = {track['persistent_id']: track for track in tracks}

    # Recover the reference config from the source bundle in memory only.
    from decrypt_genius import decrypt_pages, derive_key, extract_key_header
    from emulate_genius import database_rows
    pref = paths['genius_bundle'] / 'Library Preferences.musicdb'
    encrypted = paths['genius_bundle'] / 'Genius.itdb'
    pref_bytes, encrypted_bytes = pref.read_bytes(), encrypted.read_bytes()
    key, key_derivation_steps = derive_key(paths['executable'], extract_key_header(pref_bytes))
    clear = decrypt_pages(encrypted_bytes, key)
    config, _, _ = database_rows(clear)

    accepted_roots = graph_doc.get('accepted_roots')
    new_graphs = graph_doc.get('new_graphs')
    if not isinstance(accepted_roots, list) or not isinstance(new_graphs, list):
        parser.error('Accepted graph file has an unsupported version')
    all_graphs = [*accepted_roots, *new_graphs]
    report = run_experiment(all_graphs, matcher, config, paths['executable'], track_map,
                            args.root_pid, args.limit, edge_preference=args.edge_preference)
    report.update({
        'schema_version': 1,
        'experiment': 'current MusicCore config baseline vs artist-spacing config vs affinity-reordered actual radio target edges',
        'graph_source_relation': 'Accepted YTMusic radio graphs only; no added or inferred edge',
        'source_library_sha256': library_hash,
        'source_paths': {
            'accepted_graphs': str(paths['accepted_graphs']),
            'track_snapshot': str(paths['track_snapshot']),
            'identity_map': str(paths['identity_map']),
            'genius_reference_bundle': str(paths['genius_bundle']),
            'music_executable': str(paths['executable']),
        },
        'source_hashes': {
            'accepted_graphs': sha(graph_bytes),
            'track_snapshot': sha(snapshot_bytes),
            'identity_map': sha(map_bytes),
            'encrypted_reference_genius_itdb': sha(encrypted_bytes),
            'reference_library_preferences': sha(pref_bytes),
            'recovered_reference_genius_itdb': sha(clear),
            'music_executable': sha(paths['executable'].read_bytes()),
        },
        'reference_config': {'source': str(paths['genius_bundle']),
                             'version': 2,
                             'key_derivation_instructions': key_derivation_steps,
                             'config_sha256': sha(config)},
        'experiment_safety': {
            'accepted_source_graph_modified': False,
            'source_bundle_modified': False,
            'reordered_graphs_persisted': False,
            'music_core_generation_is_emulation': True,
        },
        'selection_limitation': (
            'The application policy only reorders the current generated playlist; it does not '
            'change its selected PID set or add audio-embedding or cross-artist style similarity. '
            'This native-core experiment only reordered already-observed radio targets and showed '
            'no playlist-order change in the tested YUZUSOFT samples.'
        ),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'roots': len(args.root_pid),
                      'config_sha256': report['baseline_controlled_config_sha256'],
                      'edge_preference': args.edge_preference,
                      'network_requests': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
