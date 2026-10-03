"""Compare distance controls on the same saved graph using actual Music code."""
import argparse
import hashlib
import json
from pathlib import Path

from emulate_genius import database_rows
from genius_profiles import distance_profiles
from evaluate_ytmusic_batch import collect_graphs, evaluate_graphs, observation_paths
from genius_format import parse_config
from music_identity_map import IdentityMap, load_library




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('track-snapshot', 'identity-map', 'observations-directory', 'genius-reference', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--limit', type=int, default=25)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ('track_snapshot', 'identity_map', 'observations_directory', 'genius_reference', 'executable')}
    output = args.output.resolve()
    if output.exists() or output in paths.values() or output.is_relative_to(paths['observations_directory']) or output.is_relative_to(paths['executable'].parent):
        parser.error('Output must be new and outside input directories')
    tracks, library_hash, provenance = load_library(track_snapshot=paths['track_snapshot'])
    map_blob = paths['identity_map'].read_bytes()
    matcher = IdentityMap(json.loads(map_blob), tracks, library_hash)
    files, manifest_hash = observation_paths(paths['observations_directory'], [], library_hash)
    graphs, skipped, inputs = collect_graphs(files, matcher, library_hash)
    reference = paths['genius_reference'].read_bytes()
    config, _, _ = database_rows(reference)
    runs = []
    for name, variant in distance_profiles(config).items():
        evaluated = evaluate_graphs(graphs, matcher, variant, paths['executable'], args.limit)
        for root in evaluated['root_results']:
            root['profile'] = name
        runs.append({'profile': name, 'config': parse_config(variant),
                     'config_sha256': hashlib.sha256(variant).hexdigest(), **evaluated})
    report = {'schema_version': 1, 'library_sha256': library_hash, 'library_input': provenance,
              'input_sha256': {'identity_map': hashlib.sha256(map_blob).hexdigest(), 'seed_manifest': manifest_hash,
                               'genius_reference': hashlib.sha256(reference).hexdigest(),
                               'executable': hashlib.sha256(paths['executable'].read_bytes()).hexdigest()},
              'observation_inputs': inputs, 'skipped_snapshots': skipped, 'runs': runs,
              'network_requests': 0, 'identity_status': 'unverified',
              'parameter_semantics': 'Distance index/group labels and parameter meanings remain partly inferred; results are empirical.',
              'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'profiles': len(runs), 'roots_per_profile': len(runs[0]['root_results'])}))


if __name__ == '__main__':
    main()
