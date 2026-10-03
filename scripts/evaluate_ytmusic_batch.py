"""Evaluate saved YTMusic recommendation candidates in a shared offline Music core.

Metadata preferences and synthetic playlist groups never confirm recording identity.
"""
import argparse
from collections import deque
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

from emulate_ytmusic_candidates import candidate_graph, controlled_configs, synthetic_rows
from genius_format import pack_similarities
from music_identity_map import IdentityMap, load_library


class InvalidObservation(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise InvalidObservation(code)


def _video(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', value) is not None


def _time(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def validate_observations(snapshot, library_hash):
    """Validate actual collector envelopes before passing anything to the matcher."""
    if not isinstance(snapshot, dict) or type(snapshot.get('schema_version')) is not int or snapshot['schema_version'] != 1:
        _fail('invalid_snapshot_schema')
    if snapshot.get('origin') == 'saved_search_cache':
        _fail('search_results_are_not_recommendations')
    if snapshot.get('library_sha256') not in (None, library_hash):
        _fail('foreign_library')
    if not _video(snapshot.get('selected_video_id')):
        _fail('invalid_selected_video_id')
    observations = snapshot.get('observations')
    if not isinstance(observations, list) or not observations:
        _fail('missing_observations')
    for row in observations:
        if not isinstance(row, dict) or row.get('source') != 'ytmusic' or row.get('relation') not in ('radio', 'related'):
            _fail('unsupported_observation_source')
        if type(row.get('is_seed')) is not bool or type(row.get('position')) is not int or row['position'] < 1 or not _time(row.get('observed_at')):
            _fail('invalid_observation_fields')
        if row.get('section') is not None and not isinstance(row['section'], str):
            _fail('invalid_observation_section')
        track = row.get('track')
        if not isinstance(track, dict) or not _video(track.get('video_id')) or not isinstance(track.get('title'), str) or not track['title'].strip():
            _fail('invalid_track_metadata')
        if row['is_seed'] != (track['video_id'] == snapshot['selected_video_id']):
            _fail('inconsistent_seed_flag')
        artists = track.get('artists')
        if not isinstance(artists, list) or any(not isinstance(artist, dict) or not isinstance(artist.get('name'), str)
                or not artist['name'].strip() or (artist.get('id') is not None and not isinstance(artist['id'], str)) for artist in artists):
            _fail('invalid_artist_metadata')
        duration = track.get('duration_seconds')
        if duration is not None and (type(duration) not in (int, float) or not 0 <= duration <= 0xFFFFFFFF or not math.isfinite(duration)):
            _fail('invalid_duration_metadata')
        if track.get('length') is not None and (not isinstance(track['length'], str) or not re.fullmatch(r'\d+:\d{2}(?::\d{2})?', track['length'])):
            _fail('invalid_duration_metadata')
        album = track.get('album')
        if album is not None and (not isinstance(album, dict) or not isinstance(album.get('name'), str)
                or (album.get('id') is not None and not isinstance(album['id'], str))):
            _fail('invalid_album_metadata')
        if track.get('video_type') is not None and not isinstance(track['video_type'], str):
            _fail('invalid_track_metadata')
    if not any(row['is_seed'] for row in observations):
        _fail('missing_seed_observation')
    return snapshot


def collect_graphs(paths, matcher, library_hash):
    """Rematch each file and retain each snapshot's provenance and observations."""
    graphs, skipped, inputs = [], [], []
    for path in paths:
        entry = {'path': str(path)}
        try:
            data = path.read_bytes()
            entry['sha256'] = hashlib.sha256(data).hexdigest()
            inputs.append(entry)
            snapshot = json.loads(data)
            validate_observations(snapshot, library_hash)
            graph = candidate_graph(snapshot, matcher, library_hash)
            graph['input'] = entry.copy()
            graphs.append(graph)
        except InvalidObservation as error:
            skipped.append({**entry, 'reason': error.code})
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped.append({**entry, 'reason': 'invalid_json'})
        except OSError:
            skipped.append({**entry, 'reason': 'unreadable_observation'})
        except ValueError:
            skipped.append({**entry, 'reason': 'no_unique_duration_supported_root'})
    if not graphs:
        raise ValueError('No valid recommendation snapshots remain')
    return graphs, skipped, inputs


def merge_graphs(graphs):
    roots = {}
    for graph in graphs:
        root = graph['root_pid']
        merged = roots.setdefault(root, {'root_pid': root, 'ordered_target_pids': [], 'snapshot_inputs': [],
                                        'observation_count': 0, 'candidate_video_count': 0, 'identity_status': 'unverified'})
        merged['snapshot_inputs'].append(graph.get('input', {}))
        merged['observation_count'] += len(graph['observations'])
        seen = {root, *merged['ordered_target_pids']}
        for pid in graph['ordered_target_pids']:
            if pid not in seen:
                merged['ordered_target_pids'].append(pid)
                seen.add(pid)
    # Candidate video counts are distinct across repeated snapshots of one root.
    for root, merged in roots.items():
        groups = [group for graph in graphs if graph['root_pid'] == root for group in graph['candidate_groups']]
        videos = {group['video_id'] for group in groups}
        candidates = {group['video_id'] for group in groups if group['candidate_pids']}
        preferred = {group['video_id'] for group in groups if group.get('preferred_metadata_pid') is not None}
        merged.update(unique_nonseed_video_count=len(videos), candidate_video_count=len(candidates),
                      preferred_metadata_video_count=len(preferred),
                      candidate_video_match_rate=len(candidates) / len(videos) if videos else None,
                      preferred_metadata_match_rate=len(preferred) / len(videos) if videos else None)
    return list(roots.values())


def shared_rows(roots, matcher):
    if not roots:
        raise ValueError('Need at least one root')
    all_pids = {pid for root in roots for pid in (root['root_pid'], *root['ordered_target_pids'])}
    joint = {'root_pid': roots[0]['root_pid'], 'ordered_target_pids': sorted(all_pids - {roots[0]['root_pid']})}
    ids, metadata, similarities, mapping = synthetic_rows(joint, matcher)
    # Reset synthetic_rows' one-root edges, then assign only observed root directions.
    similarities = {identifier: pack_similarities(0, []) for identifier in ids.values()}
    for root in roots:
        similarities[ids[root['root_pid']]] = pack_similarities(0, [ids[pid] for pid in root['ordered_target_pids']])
    return ids, metadata, similarities, mapping


def jaccard(left, right):
    union = set(left) | set(right)
    return len(set(left) & set(right)) / len(union) if union else None


def relation_distances(root_pid, edges):
    """Shortest directed path in observed relations; not a native execution trace."""
    distances = {root_pid: 0}
    queue = deque([root_pid])
    while queue:
        current = queue.popleft()
        for target in edges.get(current, []):
            if target not in distances:
                distances[target] = distances[current] + 1
                queue.append(target)
    return distances


def evaluate_graphs(graphs, matcher, config, executable, limit=25, core_factory=None, profile='without-compatible-genre'):
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be 1..100')
    if profile not in ('without-compatible-genre', 'relations-only', 'artist-album-minimum-one'):
        raise ValueError('Unsupported configuration profile')
    roots = merge_graphs(graphs)
    ids, metadata, similarities, mapping = shared_rows(roots, matcher)
    by_id = {identifier: pid for pid, identifier in ids.items()}
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    if profile == 'artist-album-minimum-one':
        from genius_profiles import distance_profiles
        controlled = distance_profiles(config)[profile]
    else:
        controlled = controlled_configs(config)[profile]
    core = None
    edges = {root['root_pid']: root['ordered_target_pids'] for root in roots}
    results = []
    for root in roots:
        result = {**root, 'candidate_count': len(root['ordered_target_pids']), 'profile': profile}
        if not root['ordered_target_pids']:
            generated, playlist = {}, []
            result['status'] = 'empty_candidates'
        else:
            if core is None:
                if core_factory is None:
                    from emulate_genius import MusicCore
                    core_factory = MusicCore
                core = core_factory(executable, controlled, metadata, similarities)
            generated = core.generate(ids[root['root_pid']], limit)
            playlist = [by_id[int(identifier, 16)] for identifier in generated['result_genius_ids']]
            result['status'] = 'generated'
        distances = relation_distances(root['root_pid'], edges)
        credits = [matcher.artist_credits(tracks[pid].get('artist') or '') for pid in playlist]
        adjacent = sum(left == right for left, right in zip(credits, credits[1:]))
        result.update(core_result=generated, playlist_pids=playlist,
                      playlist=[{'persistent_id': pid, 'title': tracks[pid].get('title'),
                                 'artist': tracks[pid].get('artist'), 'observed_relation_hops': distances.get(pid),
                                 'identity_status': 'unverified'} for pid in playlist],
                      direct_candidate_count=sum(distances.get(pid) == 1 for pid in playlist),
                      indirect_candidate_count=sum(distances.get(pid, 0) > 1 for pid in playlist),
                      unreachable_candidate_count=sum(pid not in distances for pid in playlist),
                      generated_count=len(playlist), generated_nonroot_count=sum(pid != root['root_pid'] for pid in playlist),
                      unique_artist_count=len(set(credits)), adjacent_same_artist_count=adjacent,
                      adjacent_same_artist_rate=adjacent / (len(credits) - 1) if len(credits) > 1 else None,
                      identity_status='unverified')
        results.append(result)
    root_pids = {root['root_pid'] for root in roots}
    overlaps = []
    for index, left in enumerate(results):
        for right in results[index + 1:]:
            overlaps.append({'left_root_pid': left['root_pid'], 'right_root_pid': right['root_pid'],
                             'candidate_pid_jaccard': jaccard(left['ordered_target_pids'], right['ordered_target_pids']),
                             'playlist_pid_jaccard': jaccard(left['playlist_pids'], right['playlist_pids']),
                             'nonroot_playlist_pid_jaccard': jaccard(set(left['playlist_pids']) - root_pids,
                                                                    set(right['playlist_pids']) - root_pids)})
    return {'temporary_id_mapping': mapping, 'root_results': results, 'root_overlaps': overlaps,
            'controlled_config_sha256': hashlib.sha256(controlled).hexdigest(),
            'relation_rows': [{'root_pid': root['root_pid'], 'ordered_target_pids': root['ordered_target_pids']} for root in roots],
            'shared_metadata_count': len(metadata), 'total_instructions': core.steps if core is not None else 0}


def observation_paths(directory, extras, library_hash):
    manifest_data = (directory / 'seeds.json').read_bytes()
    manifest = json.loads(manifest_data)
    if not isinstance(manifest, dict) or manifest.get('library_sha256') != library_hash or not isinstance(manifest.get('seeds'), list):
        raise ValueError('Invalid or foreign seed manifest')
    pids = []
    for seed in manifest['seeds']:
        pid = seed.get('persistent_id') if isinstance(seed, dict) else None
        if not isinstance(pid, str) or re.fullmatch('[0-9A-F]{16}', pid) is None or pid in pids:
            raise ValueError('Invalid or duplicate seed manifest PID')
        pids.append(pid)
    paths = [directory / (pid + '.json') for pid in pids]
    paths.extend(extras)
    return paths, hashlib.sha256(manifest_data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot', 'identity-map', 'observations-directory', 'genius-reference', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--extra-observation', type=Path, action='append', default=[])
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--cluster-config', type=Path)
    parser.add_argument('--cluster-tag', action='append', default=[])
    parser.add_argument('--track-kind', choices=('vocal', 'bgm', 'off_vocal', 'unknown'))
    parser.add_argument('--profile', choices=('without-compatible-genre', 'relations-only', 'artist-album-minimum-one'),
                        default='without-compatible-genre')
    args = parser.parse_args()
    if (args.cluster_tag or args.track_kind) and not args.cluster_config:
        parser.error('Cluster scope requires --cluster-config')
    if not 1 <= args.limit <= 100:
        parser.error('Limit must be 1..100')
    paths = {name: getattr(args, name).resolve() for name in ('track_snapshot', 'identity_map', 'genius_reference', 'executable')}
    directory, output = args.observations_directory.resolve(), args.output.resolve()
    extras = [path.resolve() for path in args.extra_observation]
    if args.cluster_config and output == args.cluster_config.resolve():
        parser.error('Output must differ from cluster config')
    if output.exists() or output in {*paths.values(), *extras} or output.is_relative_to(directory) or output.is_relative_to(paths['executable'].parent):
        parser.error('Output must be new and outside observation inputs and executable directory')
    tracks, library_hash, provenance = load_library(track_snapshot=paths['track_snapshot'])
    map_data = paths['identity_map'].read_bytes()
    matcher = IdentityMap(json.loads(map_data), tracks, library_hash)
    observations, manifest_hash = observation_paths(directory, extras, library_hash)
    graphs, skipped, observation_inputs = collect_graphs(observations, matcher, library_hash)
    cluster_scope = None
    cluster_config_hash = None
    if args.cluster_config:
        from music_clusters import classify_tracks, scope_graphs, _unique_keys
        cluster_bytes = args.cluster_config.read_bytes()
        classifications = classify_tracks(json.loads(cluster_bytes, object_pairs_hook=_unique_keys), tracks, library_hash)
        cluster_config_hash = hashlib.sha256(cluster_bytes).hexdigest()
        if args.cluster_tag or args.track_kind:
            graphs, cluster_scope = scope_graphs(graphs, classifications, args.cluster_tag, args.track_kind)
    reference = paths['genius_reference'].read_bytes()
    from emulate_genius import database_rows
    config, _, _ = database_rows(reference)
    report = {'schema_version': 1, 'library_sha256': library_hash, 'library_input': provenance,
              'input_sha256': {'track_snapshot': provenance['snapshot_sha256'], 'identity_map': hashlib.sha256(map_data).hexdigest(),
                               'seed_manifest': manifest_hash, 'genius_reference': hashlib.sha256(reference).hexdigest(),
                               'executable': hashlib.sha256(paths['executable'].read_bytes()).hexdigest()},
              'observation_inputs': observation_inputs, 'skipped_snapshots': skipped, 'snapshot_graphs': graphs,
              'cluster_scope': cluster_scope, 'cluster_config_sha256': cluster_config_hash,
              **evaluate_graphs(graphs, matcher, config, paths['executable'], args.limit, profile=args.profile),
              'grouping_policy': {'ids': 'Sorted local PIDs in one shared temporary uint32 ID space',
                                  'metadata': 'genre 0; canonical artist credits; normalized album; credits plus original title song groups',
                                  'relations': 'Actual radio/related targets only; per-root ordered PID union; no reverse edges',
                                  'empty_candidates': 'No MusicCore call; empty playlist',
                                  'observed_relation_hops': 'Shortest directed path from root; not a Music core execution trace; null if unreachable',
                                  'artist_metrics': 'Canonical artist credit sets; complete generated playlist including seed',
                                  'overlap': 'PID set Jaccard; empty union is null; nonroot overlap excludes all batch roots'},
              'network_requests': 0, 'account_authentication_rechecked': False, 'identity_status': 'unverified', 'verified_recordings': 0,
              'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'roots': len(report['root_results']), 'skipped': len(skipped),
                      'generated_roots': sum(row['status'] == 'generated' for row in report['root_results']), 'network_requests': 0}))


if __name__ == '__main__':
    main()
