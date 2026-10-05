"""Measure concentration in saved observed paths, without rating music quality.

Paths are verified against the saved directed graph. Missing paths are reported
as unverified, never reconstructed as evidence of a particular gateway.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library


def audit_paths(evaluation, matcher):
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    edges = {}
    for row in evaluation['relation_rows']:
        root, targets = row['root_pid'], row['ordered_target_pids']
        if root not in tracks or root in edges or not isinstance(targets, list):
            raise ValueError('Invalid observed graph')
        if any(pid not in tracks for pid in targets) or len(set(targets)) != len(targets):
            raise ValueError('Invalid observed targets')
        edges[root] = set(targets)
    results, seen = [], set()
    for row in evaluation['root_results']:
        root = row['root_pid']
        if root not in edges or root in seen:
            raise ValueError('Invalid evaluated root')
        seen.add(root)
        playlist = row['playlist']
        pids = [entry['persistent_id'] for entry in playlist]
        if any(pid not in tracks for pid in pids) or len(set(pids)) != len(pids):
            raise ValueError('Invalid evaluated playlist')
        if row.get('playlist_pids', pids) != pids:
            raise ValueError('Playlist representations disagree')
        gateways, hops = Counter(), Counter()
        verified, unverified = 0, 0
        for entry in playlist:
            pid, path = entry['persistent_id'], entry.get('observed_relation_path')
            if path is None:
                unverified += 1
                continue
            if (not isinstance(path, list) or not path or path[0] != root or path[-1] != pid
                    or any(node not in tracks for node in path) or len(set(path)) != len(path)
                    or any(right not in edges.get(left, set()) for left, right in zip(path, path[1:]))
                    or type(entry.get('observed_relation_hops')) is not int
                    or entry['observed_relation_hops'] != len(path) - 1):
                raise ValueError('Saved path does not match the observed graph')
            verified += 1
            hops[len(path) - 1] += 1
            if len(path) > 1:
                gateways[path[1]] += 1
        nonroot = [pid for pid in pids if pid != root]
        known_artists = [pid for pid in nonroot if tracks[pid].get('artist')]
        credits = Counter(tuple(sorted(matcher.artist_credits(tracks[pid]['artist'])))
                          for pid in known_artists)
        verified_nonroot = sum(gateways.values())
        results.append(dict(
            root_pid=root, title=tracks[root].get('title'), artist=tracks[root].get('artist'),
            playlist_size=len(pids), nonroot_count=len(nonroot), verified_graph_path_count=verified,
            missing_path_count=unverified, hop_distribution=dict(sorted(hops.items())),
            first_hop_gateway_count=len(gateways),
            largest_gateway_verified_nonroot_share=(max(gateways.values()) / verified_nonroot
                                                     if verified_nonroot else None),
            gateways=[dict(persistent_id=pid, title=tracks[pid].get('title'),
                           artist=tracks[pid].get('artist'), nonroot_paths=count)
                      for pid, count in sorted(gateways.items(), key=lambda pair: (-pair[1], pair[0]))],
            nonroot_artist_credit_count=len(credits),
            missing_nonroot_artist_count=len(nonroot) - len(known_artists),
            largest_known_nonroot_artist_credit_share=(max(credits.values()) / len(known_artists)
                                                      if known_artists else None)))
    return dict(schema_version=1, root_results=results, network_requests=0,
                interpretation='Structural concentration of saved paths; not musical quality or a genre label. '
                               'Only graph consistency is verified, not recording identity. '
                               'Gateways use the saved observed path; alternative observed paths may exist.',
                identity_status='metadata_match_unverified_recording', verified_recordings=0,
                native_acceptance_verified=False, ipod_acceptance_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evaluation', 'track-snapshot', 'identity-map', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--side', choices=('baseline', 'expanded'),
                        help='Select a side of a saved common-space comparison')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new path')
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    matcher = IdentityMap(json.loads(args.identity_map.read_bytes()), tracks, library_hash)
    document = json.loads(args.evaluation.read_bytes())
    if document.get('library_sha256') != library_hash:
        parser.error('Evaluation belongs to a different Library')
    comparison = document.get('graph_comparison', document)
    if args.side and args.side not in comparison:
        parser.error('Requested side is absent from the comparison')
    evaluation = comparison[args.side] if args.side else document
    report = audit_paths(evaluation, matcher)
    report.update(library_sha256=library_hash, side=args.side,
                  inputs={name: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                          for name, path in vars(args).items()
                          if name in ('evaluation', 'track_snapshot', 'identity_map')},
                  auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps(dict(output=str(args.output), roots=len(report['root_results']), network_requests=0)))


if __name__ == '__main__':
    main()
