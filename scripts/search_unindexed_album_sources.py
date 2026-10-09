"""Plan and validate YTMusic album searches for unindexed Library groups.

Search results remain unverified album metadata candidates. This module never
turns album discovery into Genius IDs, recording identity, or recommendation
edges.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from music_identity_map import IdentityMap, normalize, version_facets

PRODUCER = 'opengenius-unindexed-album-search-evidence-v1'
PLAN_PRODUCER = 'opengenius-unindexed-album-search-plan-v1'
ALBUM_RE = re.compile(r'MPRE[A-Za-z0-9_-]+\Z')
EXCLUDED_VERSION_KINDS = {'off_vocal', 'karaoke', 'instrumental'}
SAFE_RESULT_KEYS = {'browseId', 'title', 'artists', 'year'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _credit_json(credits):
    return sorted([list(credit) for credit in credits], key=lambda item: _canonical_json(item))


def _safe_artist(row):
    if not isinstance(row, dict):
        return None
    name = row.get('name')
    if not isinstance(name, str) or not name.strip():
        return None
    result = {'name': name}
    # Public artist IDs are retained only in their expected string shape.
    artist_id = row.get('id')
    if isinstance(artist_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', artist_id):
        result['id'] = artist_id
    return result


def sanitize_album_search_response(response):
    """Retain only public album fields; explicitly preserve a valid MPRE browseId."""
    if not isinstance(response, list):
        raise ValueError('Album search response must be a list')
    safe = []
    for row in response:
        if not isinstance(row, dict):
            continue
        browse_id, title = row.get('browseId'), row.get('title')
        artists = row.get('artists')
        if (not isinstance(browse_id, str) or not ALBUM_RE.fullmatch(browse_id)
                or not isinstance(title, str) or not title.strip()
                or not isinstance(artists, list)):
            continue
        safe_artists = [_safe_artist(artist) for artist in artists]
        safe_artists = [artist for artist in safe_artists if artist]
        if not safe_artists:
            continue
        clean = {'browseId': browse_id, 'title': title, 'artists': safe_artists}
        year = row.get('year')
        if isinstance(year, str) and len(year) <= 32:
            clean['year'] = year
        elif type(year) is int and 1000 <= year <= 9999:
            clean['year'] = year
        safe.append(clean)
    return safe


def _eligible(track):
    if not all(isinstance(track.get(key), str) and track[key].strip()
               for key in ('title', 'artist', 'album')):
        return False
    if type(track.get('duration_ms')) is not int or track['duration_ms'] <= 0:
        return False
    return not (set(version_facets(track['title'])['kinds']) & EXCLUDED_VERSION_KINDS)


def _indexed_pids(state):
    if not isinstance(state, dict) or not isinstance(state.get('rows'), list):
        raise ValueError('Index state must contain rows')
    result = set()
    for row in state['rows']:
        pid = row.get('persistent_id') if isinstance(row, dict) else None
        if not isinstance(pid, str) or not pid or pid in result:
            raise ValueError('Index state has invalid or duplicate persistent IDs')
        result.add(pid)
    return result


def build_album_search_plan(tracks, library_hash, identity_map_hash, matcher,
                            indexed_pids, *, state_hash, track_snapshot_hash,
                            limit=20):
    """Select high-yield, artist-diverse album groups from eligible unindexed tracks."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be from 1 to 100')
    if not isinstance(tracks, list) or not isinstance(indexed_pids, (set, frozenset, list, tuple)):
        raise ValueError('Invalid tracks or indexed PID set')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks) or None in by_pid:
        raise ValueError('Library has missing or duplicate persistent IDs')
    indexed = set(indexed_pids)
    if not indexed <= set(by_pid):
        raise ValueError('Index state contains a PID outside the Library')
    groups = {}
    for pid, track in by_pid.items():
        if pid in indexed or not _eligible(track):
            continue
        credits = matcher.artist_credits(track['artist'])
        if not credits:
            continue
        canonical_artists = _credit_json(credits)
        album_key = (normalize(track['album']), tuple(tuple(item) for item in canonical_artists))
        group = groups.setdefault(album_key, {
            'album_key': _sha(_canonical_json(album_key)),
            'album_title': track['album'], 'normalized_album_title': album_key[0],
            'canonical_artists': canonical_artists, 'artist_counts': Counter(),
            'tracks': []})
        group['artist_counts'][track['artist']] += 1
        group['tracks'].append({'persistent_id': pid, 'title': track['title'],
                                'artist': track['artist'], 'album': track['album'],
                                'duration_ms': track['duration_ms'],
                                'version_facets': version_facets(track['title'])})
    rows = []
    for group in groups.values():
        artist, _count = sorted(group['artist_counts'].items(),
                                key=lambda item: (-item[1], normalize(item[0]), item[0]))[0]
        row = {key: value for key, value in group.items() if key != 'artist_counts'}
        row['tracks'].sort(key=lambda item: item['persistent_id'])
        row.update(dominant_artist=artist,
                   query=(group['album_title'] + ' ' + artist).strip(),
                   candidate_pids=sorted(item['persistent_id'] for item in group['tracks']),
                   estimated_unindexed_local_tracks=len(group['tracks']))
        rows.append(row)
    # First choose the best album per canonical artist group, then fill remaining
    # slots by coverage. This prevents one prolific artist dominating the run.
    rows.sort(key=lambda row: (-row['estimated_unindexed_local_tracks'],
                               row['normalized_album_title'], row['album_key']))
    chosen, seen_artists = [], set()
    for row in rows:
        artist_key = _canonical_json(row['canonical_artists'])
        if artist_key not in seen_artists:
            chosen.append(row)
            seen_artists.add(artist_key)
            if len(chosen) == limit:
                break
    if len(chosen) < limit:
        chosen_keys = {row['album_key'] for row in chosen}
        chosen.extend(row for row in rows if row['album_key'] not in chosen_keys
                      and len(chosen) < limit)
    for ordinal, row in enumerate(chosen):
        row['query_ordinal'] = ordinal
    return {'schema_version': 1, 'producer': PLAN_PRODUCER,
            'library_sha256': library_hash, 'identity_map_sha256': identity_map_hash,
            'state_sha256': state_hash, 'track_snapshot_sha256': track_snapshot_hash,
            'eligible_unindexed_track_count': sum(len(row['tracks']) for row in groups.values()),
            'album_group_count': len(groups), 'query_limit': limit,
            'albums': chosen, 'network_requests': 0,
            'recommendation_edges_created': 0}


def validate_album_search_plan(plan, library_hash, identity_map_hash):
    if (not isinstance(plan, dict) or plan.get('schema_version') != 1
            or plan.get('producer') != PLAN_PRODUCER
            or plan.get('library_sha256') != library_hash
            or plan.get('identity_map_sha256') != identity_map_hash
            or not isinstance(plan.get('albums'), list)):
        raise ValueError('Album search plan schema or source hashes differ')
    seen = set()
    for ordinal, row in enumerate(plan['albums']):
        if (not isinstance(row, dict) or row.get('query_ordinal') != ordinal
                or not isinstance(row.get('query'), str) or not row['query'].strip()
                or not isinstance(row.get('candidate_pids'), list)
                or len(set(row['candidate_pids'])) != len(row['candidate_pids'])
                or row.get('album_key') in seen):
            raise ValueError('Album search plan row is malformed')
        seen.add(row['album_key'])
    return plan


def build_album_search_evidence(plan, saved_responses, tracks, matcher,
                                library_hash, identity_map_hash):
    """Bind exact saved search result positions to fully matching local albums."""
    validate_album_search_plan(plan, library_hash, identity_map_hash)
    response_by_query = {}
    for source in saved_responses:
        query = source.get('query') if isinstance(source, dict) else None
        if not isinstance(query, str) or query in response_by_query:
            raise ValueError('Saved album search queries must be unique')
        path = Path(source.get('path', ''))
        raw = path.read_bytes()
        digest = _sha(raw)
        if digest != source.get('sha256'):
            raise ValueError('Saved album search response hash changed')
        document = json.loads(raw)
        expected_args = {'query': query, 'filter': 'albums', 'limit': 5}
        if (not isinstance(document, dict) or document.get('endpoint') != 'search'
                or document.get('arguments') != expected_args
                or not isinstance(document.get('observed_at'), str)
                or document.get('response') != source.get('response')):
            raise ValueError('Saved album search provenance does not match exact query')
        response_by_query[query] = {'path': str(path), 'sha256': digest,
                                    'observed_at': document['observed_at'],
                                    'response': sanitize_album_search_response(document['response'])}
    candidates, observations, seen_ids = [], [], set()
    for query, saved in response_by_query.items():
        matching_plan = [row for row in plan['albums'] if row['query'] == query]
        if len(matching_plan) != 1:
            continue
        planned = matching_plan[0]
        for position, result in enumerate(saved['response']):
            remote_credits = frozenset(
                credit for artist in result['artists']
                for credit in matcher.artist_credits(artist['name']))
            if (normalize(result['title']) != planned['normalized_album_title']
                    or not remote_credits
                    or _credit_json(remote_credits) != planned['canonical_artists']):
                continue
            browse_id = result['browseId']
            # A browse ID may be seen for the same exact local group only once.
            if browse_id in seen_ids:
                continue
            seen_ids.add(browse_id)
            provenance = {'response_path': saved['path'],
                'response_sha256': saved['sha256'], 'observed_at': saved['observed_at'],
                'endpoint': 'search', 'arguments': {'query': query, 'filter': 'albums', 'limit': 5},
                'result_position': position, 'browse_id_key': 'browseId',
                'source_key': 'response[%d].browseId' % position}
            candidate = {'source_kind': 'search_album', 'browse_id': browse_id,
                'album_key': planned['album_key'], 'album_title': result['title'],
                'canonical_artists': planned['canonical_artists'],
                'candidate_pids': planned['candidate_pids'], 'query': query,
                'identity_status': 'unverified_metadata_candidate',
                'metadata_match': {'normalized_title_exact': True,
                    'complete_canonical_artist_set_exact': True},
                'source': provenance}
            candidates.append(candidate)
            observations.append({'source': 'ytmusic', 'relation': 'album_search_candidate',
                'identity_status': 'unverified_metadata_candidate',
                'browse_id': browse_id, 'album_title': result['title'],
                'artists': result['artists'], 'candidate_pids': planned['candidate_pids'],
                'source': provenance})
    return {'schema_version': 1, 'producer': PRODUCER,
        'library_sha256': library_hash, 'identity_map_sha256': identity_map_hash,
        'state_sha256': plan['state_sha256'],
        'track_snapshot_sha256': plan['track_snapshot_sha256'],
        'search_album_candidates': candidates, 'observations': observations,
        'audit': {'saved_search_count': len(response_by_query),
            'candidate_count': len(candidates), 'recommendation_edges_created': 0,
            'recording_identity_claims_created': 0}}


def _read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), _sha(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', required=True)
    parser.add_argument('--identity-map', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)
    tracks_doc, track_sha = _read_json(args.track_snapshot)
    tracks = tracks_doc.get('tracks') if isinstance(tracks_doc, dict) else tracks_doc
    if not isinstance(tracks, list):
        parser.error('track snapshot must be a list or contain a tracks list')
    map_doc, map_sha = _read_json(args.identity_map)
    state_doc, state_sha = _read_json(args.state)
    library_hash = map_doc.get('library_sha256')
    if not isinstance(library_hash, str):
        parser.error('track snapshot or identity map must carry library_sha256')
    matcher = IdentityMap(map_doc, tracks, library_hash)
    plan = build_album_search_plan(tracks, library_hash, map_sha, matcher,
        _indexed_pids(state_doc), state_hash=state_doc.get('state_sha256', state_sha),
        track_snapshot_hash=track_sha, limit=args.limit)
    Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    print(json.dumps({'album_groups': plan['album_group_count'],
        'eligible_unindexed_tracks': plan['eligible_unindexed_track_count'],
        'queries': [{'query': row['query'], 'estimated_tracks': row['estimated_unindexed_local_tracks'],
                     'candidate_pid_count': len(row['candidate_pids'])} for row in plan['albums']],
        'network_requests': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
