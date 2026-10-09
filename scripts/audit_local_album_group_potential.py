"""Audit same-album, same-credit, same-version local index growth potential."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from music_identity_map import IdentityMap, load_library, normalize, version_facets

EXCLUDED_KINDS = {'off_vocal', 'karaoke', 'instrumental'}
GENERIC_ARTIST = re.compile(
    r'^(?:v\.?a\.?|va|various|various artists?|soundtrack|original soundtrack|unknown|'
    r'サウンドトラック|アーティスト)$', re.I)
BGM_MARKER = re.compile(r'\bbgm\b|background music|instrument(?:al)?|インスト', re.I)
VOCAL_MARKER = re.compile(r'(?<!off )\bvocal\b|歌唱|ボーカル|ヴォーカル', re.I)
OFF_VOCAL_MARKER = re.compile(r'off\s*vocal|オフボーカル|オフ・ボーカル', re.I)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canon(credits):
    return sorted([list(value) for value in credits], key=lambda x: json.dumps(
        x, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def audit(tracks, library_hash, map_hash, matcher, state, track_sha):
    if state.get('source', {}).get('library_sha256') != library_hash:
        raise ValueError('State and Library hashes differ')
    if state.get('source', {}).get('identity_map_sha256') != map_hash:
        raise ValueError('State and identity-map hashes differ')
    indexed = {row['persistent_id'] for row in state.get('rows', [])}
    by_pid = {row['persistent_id']: row for row in tracks}
    if len(by_pid) != len(tracks) or not indexed <= by_pid.keys():
        raise ValueError('State PIDs do not match the Library')
    groups, albums = defaultdict(list), defaultdict(list)
    missing_labels = {'title': 0, 'artist': 0, 'album': 0, 'duration': 0}
    eligible_pids = set()
    for track in tracks:
        pid = track['persistent_id']
        labels_ok = True
        for key in ('title', 'artist', 'album'):
            if not isinstance(track.get(key), str) or not track[key].strip():
                missing_labels[key] += 1
                labels_ok = False
        if type(track.get('duration_ms')) is not int or track['duration_ms'] <= 0:
            missing_labels['duration'] += 1
            labels_ok = False
        if not labels_ok:
            continue
        credits = _canon(matcher.artist_credits(track['artist']))
        facets = version_facets(track['title'])
        facet_key = {key: facets[key] for key in ('markers', 'years', 'unknown_qualifiers')}
        key = (normalize(track['album']), json.dumps(credits, ensure_ascii=False,
                sort_keys=True, separators=(',', ':')),
               json.dumps(facet_key, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        groups[key].append(track)
        albums[key[0]].append((track, credits, facets))
        if (pid not in indexed
                and not (set(facets['kinds']) & EXCLUDED_KINDS)):
            eligible_pids.add(pid)

    qualifying = []
    qualified_pids = set()
    excluded_version_pids = set()
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        candidates = {row['persistent_id'] for row in rows if row['persistent_id'] not in indexed}
        if not candidates:
            continue
        qualified_pids.update(candidates)
        facets = version_facets(rows[0]['title'])
        unsafe = bool(set(facets['kinds']) & EXCLUDED_KINDS)
        if unsafe:
            excluded_version_pids.update(candidates)
        qualifying.append({'normalized_album': key[0], 'canonical_artists': json.loads(key[1]),
            'version_facets': json.loads(key[2]), 'track_count': len(rows),
            'unindexed_track_count': len(candidates),
            'indexed_bridge': any(row['persistent_id'] in indexed for row in rows),
            'explicit_version_excluded_from_conservative_projection': unsafe,
            'persistent_ids': sorted(candidates),
            'example_titles': sorted({row['title'] for row in rows})[:8],
            'example_artists': sorted({row['artist'] for row in rows})[:5]})
    conservative_pids = qualified_pids - excluded_version_pids

    risk_albums = []
    bgm_mixed_pids = set()
    generic_va_pids = set()
    for album_key, values in albums.items():
        source_album = values[0][0]['album']
        all_tracks = [row[0] for row in values]
        artist_keys = {json.dumps(credits, ensure_ascii=False, sort_keys=True) for _, credits, _ in values}
        names = {credit[1] for _, credits, _ in values for credit in credits
                 if len(credit) > 1 and credit[0] == 'literal'}
        is_generic = any(GENERIC_ARTIST.fullmatch(name.strip()) for name in names)
        marked_bgm = [row for row in all_tracks if BGM_MARKER.search(row['title'])]
        marked_vocal = [row for row in all_tracks if VOCAL_MARKER.search(row['title'])]
        marked_off_vocal = [row for row in all_tracks if OFF_VOCAL_MARKER.search(row['title'])]
        mixed_bgm = bool(marked_bgm and len(marked_bgm) < len(all_tracks))
        mixed_vocal = bool(marked_vocal and len(marked_vocal) < len(all_tracks))
        mixed_off_vocal = bool(marked_off_vocal and len(marked_off_vocal) < len(all_tracks))
        mixed_bgm_vocal = bool(marked_bgm and marked_vocal)
        if is_generic:
            generic_va_pids.update(row['persistent_id'] for row in all_tracks
                                   if row['persistent_id'] not in indexed)
        if mixed_bgm:
            bgm_mixed_pids.update(row['persistent_id'] for row in all_tracks
                                  if row['persistent_id'] not in indexed)
        if is_generic or mixed_bgm or mixed_vocal or mixed_off_vocal or mixed_bgm_vocal:
            risk_albums.append({'album': source_album, 'normalized_album': album_key,
                'track_count': len(all_tracks),
                'unindexed_track_count': sum(row['persistent_id'] not in indexed for row in all_tracks),
                'generic_artist_credit': is_generic,
                'canonical_credit_group_count': len(artist_keys),
                'bgm_or_instrumental_marker_mixed_with_unmarked_tracks': mixed_bgm,
                'vocal_marker_mixed_with_unmarked_tracks': mixed_vocal,
                'off_vocal_marker_mixed_with_unmarked_tracks': mixed_off_vocal,
                'bgm_instrumental_and_vocal_markers_both_present': mixed_bgm_vocal,
                'marked_title_count': len(marked_bgm),
                'marked_title_examples': [row['title'] for row in marked_bgm[:8]],
                'unmarked_title_examples': [row['title'] for row in all_tracks
                                            if not BGM_MARKER.search(row['title'])][:8],
                'artist_examples': sorted({row['artist'] for row in all_tracks})[:8]})
    qualifying.sort(key=lambda row: (-row['unindexed_track_count'], row['normalized_album'],
                                     json.dumps(row['canonical_artists'], ensure_ascii=False)))
    risk_albums.sort(key=lambda row: (-row['unindexed_track_count'], row['normalized_album']))
    projected = len(indexed) + len(conservative_pids)
    return {'schema_version': 1, 'producer': 'opengenius-local-album-potential-audit-v1',
        'library_sha256': library_hash, 'identity_map_sha256': map_hash,
        'track_snapshot_sha256': track_sha, 'state_sha256': state.get('state_sha256'),
        'track_count': len(tracks), 'indexed_track_count': len(indexed),
        'half_library_target': (len(tracks) + 1) // 2,
        'eligible_unindexed_track_count': len(eligible_pids),
        'missing_or_invalid_labels': missing_labels,
        'group_definition': 'exact normalized album + complete canonical artist-credit set + exact version_facets markers/years/unknown_qualifiers; at least two Library tracks',
        'qualifying_group_count': len(qualifying),
        'qualifying_group_unindexed_track_count_including_explicit_versions': len(qualified_pids),
        'explicit_version_tracks_removed_from_conservative_projection': len(excluded_version_pids),
        'conservative_new_indexable_pids': len(conservative_pids),
        'conservative_projected_indexed_track_count': projected,
        'conservative_projection_reaches_half': projected >= (len(tracks) + 1) // 2,
        'indexed_pids_available_as_same_group_bridges': sum(row['indexed_bridge'] for row in qualifying),
        'generic_artist_unindexed_pids': len(generic_va_pids),
        'bgm_or_instrumental_mixed_album_unindexed_pids': len(bgm_mixed_pids),
        'bgm_or_instrumental_mixed_album_count': sum(row['bgm_or_instrumental_marker_mixed_with_unmarked_tracks'] for row in risk_albums),
        'vocal_mixed_album_count': sum(row['vocal_marker_mixed_with_unmarked_tracks'] for row in risk_albums),
        'off_vocal_mixed_album_count': sum(row['off_vocal_marker_mixed_with_unmarked_tracks'] for row in risk_albums),
        'bgm_vocal_mixed_album_count': sum(row['bgm_instrumental_and_vocal_markers_both_present'] for row in risk_albums),
        'risk_albums': risk_albums,
        'qualifying_groups': qualifying,
        'network_requests': 0, 'recording_identity_claims_created': 0,
        'recommendation_edges_created': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    tracks, library_hash, provenance = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    state = json.loads(args.state.read_text(encoding='utf-8'))
    result = audit(tracks, library_hash, map_hash, matcher, state,
                   provenance['snapshot_sha256'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in (
        'track_count', 'indexed_track_count', 'half_library_target',
        'eligible_unindexed_track_count', 'qualifying_group_count',
        'conservative_new_indexable_pids', 'conservative_projected_indexed_track_count',
        'conservative_projection_reaches_half', 'generic_artist_unindexed_pids',
        'bgm_or_instrumental_mixed_album_count', 'bgm_or_instrumental_mixed_album_unindexed_pids',
        'vocal_mixed_album_count', 'off_vocal_mixed_album_count',
        'bgm_vocal_mixed_album_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
