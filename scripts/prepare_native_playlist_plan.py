#!/usr/bin/env python3
"""Prepare complete observed playlists for private native Music validation.

Never truncates a recommendation to fit the available audio fixture. This plan
creates ordinary playlists; it is not evidence of native Genius generation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

PID = re.compile(r'[0-9A-F]{16}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def prepare(report, manifest, roots, source_sha256):
    if not isinstance(source_sha256, str) or not SHA.fullmatch(source_sha256):
        raise ValueError('invalid source Library digest')
    if report.get('source_library_sha256') != source_sha256:
        raise ValueError('recommendations belong to a different source Library')
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8 or len(set(roots)) != len(roots):
        raise ValueError('select 1..8 distinct playlist roots')
    if any(not isinstance(p, str) or not PID.fullmatch(p) for p in roots):
        raise ValueError('invalid playlist root')
    available = {t['persistent_id'] for t in manifest['tracks']}
    results = report.get('results')
    if not isinstance(results, list):
        raise ValueError('missing recommendation results')
    indexed = {}
    for result in results:
        root = result['root_pid']
        if root in indexed:
            raise ValueError('duplicate recommendation root')
        indexed[root] = result
    plans = []
    for index, root in enumerate(roots, 1):
        if root not in indexed:
            raise ValueError('selected root has no observed playlist')
        result = indexed[root]
        pids = [t['persistent_id'] for t in result['playlist']]
        if not 2 <= len(pids) <= 100 or pids[0] != root or len(set(pids)) != len(pids):
            raise ValueError('invalid observed playlist order or membership')
        if any(not isinstance(p, str) or not PID.fullmatch(p) for p in pids):
            raise ValueError('invalid playlist member')
        if not set(pids) <= available:
            raise ValueError('complete playlist audio is unavailable; refusing partial plan')
        plans.append({'name': f'OpenGenius validation {index}', 'persistent_ids': pids})
    return {**manifest, 'recommendation_playlists': plans}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--source-library', required=True, type=Path)
    parser.add_argument('--root', required=True, action='append')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report_bytes, manifest_bytes = args.report.read_bytes(), args.manifest.read_bytes()
    digest = hashlib.sha256(args.source_library.read_bytes()).hexdigest()
    planned = prepare(json.loads(report_bytes), json.loads(manifest_bytes), args.root, digest)
    # Exclusive creation keeps a previously tested fixture immutable.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(planned, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'playlist_count': len(planned['recommendation_playlists']),
                      'playlist_entry_count': sum(len(p['persistent_ids']) for p in planned['recommendation_playlists']),
                      'native_genius_generation_verified': False}))


if __name__ == '__main__':
    main()
