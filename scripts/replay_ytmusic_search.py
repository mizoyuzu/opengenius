"""Convert saved song-search responses into offline observations, without network.

Search results are identity candidates, not song recommendations or Genius ranks.
"""
import argparse
import hashlib
import json
from pathlib import Path

from probe_ytmusic import clean_track


def replay_search_cache(directory):
    observations, inputs = [], []
    for path in sorted(directory.glob('*.json')):
        data = path.read_bytes()
        cached = json.loads(data)
        if cached.get('endpoint') != 'search':
            continue
        arguments = cached['arguments']
        if arguments.get('filter') != 'songs':
            continue
        file_hash = hashlib.sha256(data).hexdigest()
        inputs.append({'file_name': path.name, 'sha256': file_hash,
                       'observed_at': cached['observed_at'], 'query': arguments['query']})
        for position, row in enumerate(cached['response'], 1):
            if not isinstance(row, dict) or not row.get('videoId'):
                continue
            observations.append({
                'source': 'ytmusic', 'relation': 'search_candidate', 'section': None,
                'position': position, 'observed_at': cached['observed_at'], 'is_seed': False,
                'track': clean_track(row), 'identity_status': 'unverified', 'genius_rank': None,
                'search_query': arguments['query'], 'request_file_sha256': file_hash,
            })
    if not inputs:
        raise ValueError('No cached song-search responses found')
    return {'schema_version': 1, 'origin': 'saved_search_cache', 'network_requests': 0,
            'account_authentication_rechecked': False, 'input_files': inputs,
            'observations': observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache_directory', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(args.cache_directory.resolve()):
        parser.error('output must be new and outside the cache directory')
    snapshot = replay_search_cache(args.cache_directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'cached_search_requests': len(snapshot['input_files']),
                      'search_candidate_observations': len(snapshot['observations']), 'network_requests': 0}))


if __name__ == '__main__':
    main()
