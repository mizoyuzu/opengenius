"""Build and validate strict video-ID evidence from saved radio observations.

The sidecar supports seed selection only. It records no recommendation edges
and never upgrades a metadata candidate to a verified recording identity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from evaluate_ytmusic_batch import validate_observations
from music_identity_map import IdentityMap, load_library, normalize, version_signature


PRODUCER = 'opengenius-seed-video-evidence-v1'
VIDEO_RE = re.compile(r'[A-Za-z0-9_-]{11}\Z')
SHA_RE = re.compile(r'[a-f0-9]{64}\Z')
ROW_KEYS = {'persistent_id', 'video_id', 'input_path', 'input_sha256', 'source_relation'}
CATALOG_PRODUCER = 'opengenius-album-catalog-seed-evidence-v1'
CATALOG_ROW_KEYS = ROW_KEYS | {
    'album_id', 'source_key', 'source_album_id_inputs',
}
ALBUM_ID_RE = re.compile(r'MPRE[A-Za-z0-9_-]+\Z')
CATALOG_SAFE_KEYS = {'videoId', 'title', 'artists', 'name', 'id', 'album', 'length',
                     'duration_seconds', 'videoType', 'related', 'tracks', 'contents'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _candidate_for_video(snapshot, video_id, persistent_id, matcher):
    """Return true only when every saved radio occurrence has one strict match."""
    rows = [row for row in snapshot['observations']
            if row['source'] == 'ytmusic' and row['relation'] == 'radio'
            and row['track']['video_id'] == video_id]
    if not rows:
        return False
    for row in rows:
        candidates = matcher.match(row['track'])
        if (len(candidates) != 1 or candidates[0].get('persistent_id') != persistent_id
                or candidates[0].get('artist_credit_match') != 'complete'):
            return False
        local = candidates[0].get('local_metadata') or {}
        if version_signature(local.get('title') or '') != version_signature(row['track'].get('title') or ''):
            return False
    return True


def build_seed_video_evidence(graph_paths, matcher, library_hash, identity_map_sha256):
    """Create deterministic PID→radio-video hints from raw snapshot paths.

    Invalid or unbound input files are skipped and counted. Candidate records
    contain their actual source path and content hash for later revalidation.
    """
    if not SHA_RE.fullmatch(library_hash or '') or not SHA_RE.fullmatch(identity_map_sha256 or ''):
        raise ValueError('Library and identity-map hashes must be SHA-256')
    if not isinstance(graph_paths, (list, tuple, set)):
        raise ValueError('Snapshot paths must be a list, tuple, or set')
    candidates = {}
    skipped = []
    radio_count = 0
    for raw_path in graph_paths:
        expected_sha = None
        if isinstance(raw_path, dict):
            source = raw_path.get('input')
            if isinstance(source, dict):
                raw_path, expected_sha = source.get('path'), source.get('sha256')
            else:
                raw_path = None
        if not isinstance(raw_path, (str, Path)):
            skipped.append({'path': None, 'reason': 'invalid_snapshot_path'})
            continue
        path = Path(raw_path).resolve()
        try:
            data = path.read_bytes()
            snapshot = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'path': str(path), 'reason': 'unreadable_or_invalid_snapshot'})
            continue
        try:
            validate_observations(snapshot, library_hash)
        except ValueError as error:
            skipped.append({'path': str(path), 'reason': str(error)})
            continue
        if (snapshot.get('library_sha256') != library_hash
                or snapshot.get('identity_map_sha256') != identity_map_sha256):
            skipped.append({'path': str(path), 'reason': 'source_hash_mismatch'})
            continue
        raw_sha = _sha(data)
        if expected_sha is not None and expected_sha != raw_sha:
            skipped.append({'path': str(path), 'reason': 'graph_input_sha256_mismatch'})
            continue
        by_video = {}
        for observation in snapshot['observations']:
            if observation.get('relation') != 'radio' or observation.get('source') != 'ytmusic':
                continue
            radio_count += 1
            by_video.setdefault(observation['track']['video_id'], []).append(observation)
        for video_id, observations in by_video.items():
            # Match every repeated observation for the video; one partial or
            # conflicting row makes that video unsuitable as a seed hint.
            matching = []
            valid = True
            for observation in observations:
                found = matcher.match(observation['track'])
                if len(found) != 1 or found[0].get('artist_credit_match') != 'complete':
                    valid = False
                    break
                matching.append(found[0]['persistent_id'])
            if not valid or len(set(matching)) != 1:
                continue
            pid = matching[0]
            if not _candidate_for_video(snapshot, video_id, pid, matcher):
                continue
            candidates.setdefault(pid, []).append({
                'persistent_id': pid, 'video_id': video_id,
                'input_path': str(path), 'input_sha256': raw_sha,
                'source_relation': 'radio',
            })
    # One stable video per PID. Repeated saved evidence for the same video is
    # collapsed; ties are resolved by video ID and then source path.
    video_pids = {}
    for pid, hints in candidates.items():
        for hint in hints:
            video_pids.setdefault(hint['video_id'], set()).add(pid)
    ambiguous_videos = {video for video, pids in video_pids.items() if len(pids) != 1}
    rows = []
    for pid in sorted(candidates):
        eligible = [row for row in candidates[pid] if row['video_id'] not in ambiguous_videos]
        if eligible:
            rows.append(min(eligible, key=lambda row: (row['video_id'], row['input_path'])))
    rows.sort(key=lambda row: row['persistent_id'])
    return {
        'schema_version': 1, 'producer': PRODUCER,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_map_sha256,
        'rows': rows,
        'audit': {
            'input_snapshot_count': len(graph_paths),
            'radio_observation_count': radio_count,
            'accepted_seed_video_count': len(rows),
            'ambiguous_video_mapping_count': len(ambiguous_videos),
            'skipped_snapshot_count': len(skipped),
            'skipped_snapshots': skipped,
            'identity_status': 'unverified_metadata_candidate',
            'edges_created': 0,
        },
    }


def _resolve_source(path, sidecar_dir):
    source = Path(path)
    return source.resolve() if source.is_absolute() else (sidecar_dir / source).resolve()


def _is_sanitized_catalog(value):
    if isinstance(value, dict):
        return (set(value) <= CATALOG_SAFE_KEYS
                and all(_is_sanitized_catalog(item) for item in value.values()))
    if isinstance(value, list):
        return all(_is_sanitized_catalog(item) for item in value)
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_catalog_seed_row(row, sidecar_dir, tracks, matcher,
                               library_hash, identity_map_sha256):
    if (not isinstance(row, dict) or set(row) != CATALOG_ROW_KEYS
            or row.get('source_relation') != 'album_catalog_candidate'
            or not isinstance(row.get('persistent_id'), str)
            or not re.fullmatch('[0-9A-F]{16}', row['persistent_id'])
            or row['persistent_id'] not in {track.get('persistent_id') for track in tracks}
            or not isinstance(row.get('video_id'), str) or not VIDEO_RE.fullmatch(row['video_id'])
            or not isinstance(row.get('album_id'), str) or not ALBUM_ID_RE.fullmatch(row['album_id'])
            or not isinstance(row.get('input_path'), str)
            or not isinstance(row.get('input_sha256'), str)
            or not SHA_RE.fullmatch(row['input_sha256'])):
        raise ValueError('Invalid or duplicate album-catalog seed evidence row')
    source_key = row.get('source_key')
    if (not isinstance(source_key, dict)
            or set(source_key) != {'endpoint', 'argument_key', 'argument_value', 'track_position'}
            or source_key.get('endpoint') != 'get_album'
            or source_key.get('argument_key') != 'browseId'
            or source_key.get('argument_value') != row['album_id']
            or type(source_key.get('track_position')) is not int
            or source_key['track_position'] < 1):
        raise ValueError('Invalid album-catalog request key provenance')
    response_path = _resolve_source(row['input_path'], sidecar_dir)
    try:
        response_bytes = response_path.read_bytes()
        if _sha(response_bytes) != row['input_sha256']:
            raise ValueError('Album-catalog response hash changed')
        cached = json.loads(response_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Album-catalog response is unreadable') from None
    if (not isinstance(cached, dict) or cached.get('endpoint') != 'get_album'
            or cached.get('arguments') != {'browseId': row['album_id']}
            or not isinstance(cached.get('response'), dict)
            or set(cached) != {'endpoint', 'arguments', 'observed_at', 'response'}):
        raise ValueError('Album-catalog response does not match its saved browse ID')
    album_response = cached['response']
    if not _is_sanitized_catalog(album_response):
        raise ValueError('Album-catalog response contains fields outside its safe metadata schema')
    response_rows = album_response.get('tracks')
    position = source_key['track_position']
    if (not isinstance(response_rows, list) or position > len(response_rows)
            or not isinstance(album_response.get('title'), str)):
        raise ValueError('Album-catalog track position is outside the response')
    remote = response_rows[position - 1]
    if (not isinstance(remote, dict) or remote.get('videoId') != row['video_id']
            or not isinstance(remote.get('title'), str)
            or not isinstance(remote.get('artists'), list)):
        raise ValueError('Album-catalog video ID differs from the saved response')
    match_row = dict(remote)
    if isinstance(match_row.get('album'), str):
        match_row['album'] = {'name': match_row['album']}
    candidates = matcher.match(match_row)
    if (len(candidates) != 1 or candidates[0].get('persistent_id') != row['persistent_id']
            or candidates[0].get('artist_credit_match') != 'complete'):
        raise ValueError('Album-catalog row no longer has one complete-credit duration match')
    local = candidates[0].get('local_metadata') or {}
    if (version_signature(local.get('title') or '') != version_signature(remote.get('title') or '')
            or normalize(local.get('album') or '') != normalize(album_response['title'])
            or normalize(local.get('album') or '') != normalize(remote.get('album') or '')):
        raise ValueError('Album-catalog row title version or exact album label changed')
    source_inputs = row.get('source_album_id_inputs')
    if not isinstance(source_inputs, list) or not source_inputs:
        raise ValueError('Album-catalog row lacks saved radio album-ID provenance')
    seen_sources = set()
    for source in source_inputs:
        if isinstance(source, dict) and source.get('source_kind') == 'search_album':
            required = {'source_kind', 'evidence_path', 'evidence_sha256',
                        'response_path', 'response_sha256', 'source_key',
                        'arguments', 'result_position'}
            if (set(source) != required
                    or not all(isinstance(source.get(key), str)
                               for key in ('evidence_path', 'response_path', 'evidence_sha256',
                                           'response_sha256'))
                    or not SHA_RE.fullmatch(source['evidence_sha256'])
                    or not SHA_RE.fullmatch(source['response_sha256'])
                    or type(source.get('result_position')) is not int
                    or source['result_position'] < 0
                    or source.get('source_key') !=
                       'response[%d].browseId' % source['result_position']
                    or not isinstance(source.get('arguments'), dict)
                    or source['arguments'].get('filter') != 'albums'
                    or source['arguments'].get('limit') != 5
                    or not isinstance(source['arguments'].get('query'), str)):
                raise ValueError('Malformed saved album-search browse-ID provenance')
            evidence_path = _resolve_source(source['evidence_path'], sidecar_dir)
            response_path = _resolve_source(source['response_path'], sidecar_dir)
            try:
                evidence_bytes = evidence_path.read_bytes()
                response_bytes = response_path.read_bytes()
                if (_sha(evidence_bytes) != source['evidence_sha256']
                        or _sha(response_bytes) != source['response_sha256']):
                    raise ValueError('Saved album-search source hash changed')
                evidence_doc = json.loads(evidence_bytes)
                response_doc = json.loads(response_bytes)
                result = response_doc['response'][source['result_position']]
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
                raise ValueError('Saved album-search source is unreadable') from None
            if (not isinstance(evidence_doc, dict)
                    or evidence_doc.get('schema_version') != 1
                    or evidence_doc.get('producer') != 'opengenius-unindexed-album-search-evidence-v1'
                    or evidence_doc.get('library_sha256') != library_hash
                    or evidence_doc.get('identity_map_sha256') != identity_map_sha256
                    or response_doc.get('endpoint') != 'search'
                    or response_doc.get('arguments') != source['arguments']
                    or not isinstance(response_doc.get('observed_at'), str)
                    or not isinstance(result, dict)
                    or set(result) - {'browseId', 'title', 'artists', 'year'}
                    or result.get('browseId') != row['album_id']
                    or result.get('title') != album_response['title']):
                raise ValueError('Album browse ID was not read from a bound saved album-search result')
            evidence_rows = evidence_doc.get('search_album_candidates')
            matching_search = [candidate for candidate in evidence_rows or []
                if isinstance(candidate, dict)
                and candidate.get('source_kind') == 'search_album'
                and candidate.get('browse_id') == row['album_id']
                and candidate.get('album_title') == result.get('title')
                and candidate.get('source', {}).get('response_sha256') == source['response_sha256']
                and candidate.get('source', {}).get('result_position') == source['result_position']
                and candidate.get('source', {}).get('arguments') == source['arguments']]
            if len(matching_search) != 1:
                raise ValueError('Album-search browse ID is absent from bound candidate evidence')
            search_candidate = matching_search[0]
            local = next(track for track in tracks if track['persistent_id'] == row['persistent_id'])
            remote_artists = result.get('artists')
            if (not isinstance(remote_artists, list) or not remote_artists
                    or not all(isinstance(artist, dict)
                               and isinstance(artist.get('name'), str)
                               and artist['name'].strip() for artist in remote_artists)):
                raise ValueError('Album-search result has incomplete album artist credits')
            remote_credits = {credit for artist in remote_artists
                              for credit in matcher.artist_credits(artist['name'])}
            local_credits = matcher.artist_credits(local.get('artist') or '')
            declared_credits = search_candidate.get('canonical_artists')
            if (normalize(local.get('album') or '') != normalize(result.get('title') or '')
                    or not local_credits or remote_credits != local_credits
                    or sorted([list(credit) for credit in remote_credits]) != declared_credits
                    or row['persistent_id'] not in search_candidate.get('candidate_pids', [])):
                raise ValueError('Album-search artist/title evidence no longer uniquely matches the local track')
            source_identity = (str(response_path), source['response_sha256'], source['result_position'])
            if source_identity in seen_sources:
                raise ValueError('Duplicate saved album-search provenance')
            seen_sources.add(source_identity)
            continue
        if (not isinstance(source, dict)
                or set(source) != {'snapshot_path', 'snapshot_sha256', 'observation_index',
                                   'source_key', 'source_video_id'}
                or not isinstance(source.get('snapshot_path'), str)
                or not isinstance(source.get('snapshot_sha256'), str)
                or not SHA_RE.fullmatch(source['snapshot_sha256'])
                or type(source.get('observation_index')) is not int
                or source['observation_index'] < 0
                or source.get('source_key') !=
                   'observations[%d].track.album.id' % source.get('observation_index')):
            raise ValueError('Malformed saved radio album-ID provenance')
        source_path = _resolve_source(source['snapshot_path'], sidecar_dir)
        source_identity = (str(source_path), source['snapshot_sha256'], source['observation_index'])
        if source_identity in seen_sources:
            raise ValueError('Duplicate saved radio album-ID provenance')
        seen_sources.add(source_identity)
        try:
            source_bytes = source_path.read_bytes()
            if _sha(source_bytes) != source['snapshot_sha256']:
                raise ValueError('Saved radio album-ID source hash changed')
            source_snapshot = json.loads(source_bytes)
            validate_observations(source_snapshot, library_hash)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Saved radio album-ID source is unreadable') from None
        if (source_snapshot.get('library_sha256') != library_hash
                or source_snapshot.get('identity_map_sha256') != identity_map_sha256):
            raise ValueError('Saved radio album-ID source has foreign source hashes')
        try:
            observation = source_snapshot['observations'][source['observation_index']]
            source_album = observation['track']['album']
        except (KeyError, IndexError, TypeError):
            raise ValueError('Saved radio album-ID source key no longer exists') from None
        if (observation.get('source') != 'ytmusic' or observation.get('relation') != 'radio'
                or source_album.get('id') != row['album_id']
                or observation['track'].get('video_id') != source.get('source_video_id')):
            raise ValueError('Album browse ID was not read from the saved radio album field')
    return {'persistent_id': row['persistent_id'], 'video_id': row['video_id'],
            'input_path': str(response_path), 'input_sha256': row['input_sha256'],
            'source_relation': 'album_catalog_candidate', 'album_id': row['album_id']}


def validate_album_catalog_evidence(document, evidence_path, tracks, matcher,
                                    library_hash, identity_map_sha256):
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document.get('schema_version') != 1
            or document.get('producer') != CATALOG_PRODUCER
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256
            or document.get('source_plan_producer') != 'opengenius-saved-radio-album-plan-v1'
            or not SHA_RE.fullmatch(document.get('album_plan_sha256') or '')
            or not isinstance(document.get('rows'), list)
            or not isinstance(document.get('catalog_observations'), list)):
        raise ValueError('Album-catalog evidence schema or source hashes differ')
    audit = document.get('audit')
    if (not isinstance(audit, dict) or audit.get('recommendation_edges_created') != 0
            or audit.get('recommendation_observations_created') != 0):
        raise ValueError('Album-catalog evidence may not assert recommendation edges')
    sidecar_dir = Path(evidence_path).resolve().parent
    validated, pids, videos = [], set(), set()
    for row in document['rows']:
        output = _validate_catalog_seed_row(row, sidecar_dir, tracks, matcher,
                                            library_hash, identity_map_sha256)
        if output['persistent_id'] in pids or output['video_id'] in videos:
            raise ValueError('Duplicate PID or video ID in album-catalog evidence')
        pids.add(output['persistent_id'])
        videos.add(output['video_id'])
        validated.append(output)
    return validated


def combine_seed_video_evidence(radio_document, radio_evidence_path,
                                catalog_document, catalog_evidence_path,
                                tracks, matcher, library_hash, identity_map_sha256):
    """Combine strict radio and album catalog hints, rejecting PID/video conflicts."""
    radio = select_seed_video_evidence(
        radio_document, radio_evidence_path,
        [{'persistent_id': track['persistent_id']} for track in tracks],
        tracks, matcher, library_hash, identity_map_sha256)
    radio_rows = [row for row in radio_document['rows']]
    catalog_rows = validate_album_catalog_evidence(
        catalog_document, catalog_evidence_path, tracks, matcher,
        library_hash, identity_map_sha256)
    original_catalog_rows = {row['persistent_id']: row for row in catalog_document['rows']}
    by_pid = {row['persistent_id']: row for row in radio_rows}
    by_video = {row['video_id']: row['persistent_id'] for row in radio_rows}
    for row in catalog_rows:
        old = by_pid.get(row['persistent_id'])
        other_pid = by_video.get(row['video_id'])
        if old is not None and old['video_id'] != row['video_id']:
            raise ValueError('Radio and album catalog evidence conflict for one PID')
        if other_pid is not None and other_pid != row['persistent_id']:
            raise ValueError('Radio and album catalog evidence conflict for one video ID')
        if old is None:
            by_pid[row['persistent_id']] = original_catalog_rows[row['persistent_id']]
            by_video[row['video_id']] = row['persistent_id']
    combined = dict(radio_document)
    combined['rows'] = [by_pid[pid] for pid in sorted(by_pid)]
    combined['catalog_evidence'] = {
        'path': str(Path(catalog_evidence_path).resolve()),
        'sha256': _sha(Path(catalog_evidence_path).read_bytes()),
        'producer': CATALOG_PRODUCER,
    }
    combined['audit'] = dict(radio_document.get('audit') or {})
    combined['audit']['radio_seed_video_count'] = len(radio_rows)
    combined['audit']['album_catalog_seed_video_count'] = len(catalog_rows)
    combined['audit']['accepted_seed_video_count'] = len(combined['rows'])
    combined['audit']['edges_created'] = 0
    return combined


def select_seed_video_evidence(document, evidence_path, seeds, tracks, matcher,
                               library_hash, identity_map_sha256):
    """Validate an evidence sidecar and return exact requested seed hints."""
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or document.get('producer') != PRODUCER
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256
            or not SHA_RE.fullmatch(library_hash or '')
            or not SHA_RE.fullmatch(identity_map_sha256 or '')):
        raise ValueError('Seed-video evidence schema or source hashes differ')
    rows = document.get('rows')
    if not isinstance(rows, list):
        raise ValueError('Seed-video evidence requires rows')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Library persistent IDs')
    requested = {seed.get('persistent_id') for seed in seeds}
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    selected, seen_videos = {}, set()
    sidecar_dir = Path(evidence_path).resolve().parent
    catalog_by_pid = {}
    catalog_rows_in_doc = [row for row in rows if isinstance(row, dict)
                           and row.get('source_relation') == 'album_catalog_candidate']
    if catalog_rows_in_doc:
        source = document.get('catalog_evidence')
        if (not isinstance(source, dict)
                or source.get('producer') != CATALOG_PRODUCER
                or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)
                or not SHA_RE.fullmatch(source['sha256'])):
            raise ValueError('Combined evidence lacks album-catalog source provenance')
        catalog_path = _resolve_source(source['path'], sidecar_dir)
        try:
            catalog_bytes = catalog_path.read_bytes()
            if _sha(catalog_bytes) != source['sha256']:
                raise ValueError('Album-catalog evidence source hash changed')
            catalog_doc = json.loads(catalog_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Album-catalog evidence source is unreadable') from None
        validated = validate_album_catalog_evidence(
            catalog_doc, catalog_path, tracks, matcher, library_hash, identity_map_sha256)
        original = {row['persistent_id']: row for row in catalog_doc['rows']}
        catalog_by_pid = {row['persistent_id']: row for row in validated}
        for row in catalog_rows_in_doc:
            if original.get(row.get('persistent_id')) != row:
                raise ValueError('Combined album-catalog row differs from its source evidence')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed seed-video evidence row')
        relation = row.get('source_relation')
        if relation == 'radio':
            if set(row) != ROW_KEYS:
                raise ValueError('Malformed radio seed-video evidence row')
        elif relation == 'album_catalog_candidate':
            if (set(row) != CATALOG_ROW_KEYS
                    or catalog_by_pid.get(row.get('persistent_id')) is None
                    or catalog_by_pid[row['persistent_id']]['video_id'] != row.get('video_id')):
                raise ValueError('Malformed album-catalog seed-video evidence row')
        else:
            raise ValueError('Unsupported seed-video evidence source relation')
        pid, video_id = row['persistent_id'], row['video_id']
        if (not isinstance(pid, str) or not re.fullmatch('[0-9A-F]{16}', pid)
                or pid not in by_pid or pid in selected
                or not isinstance(video_id, str) or not VIDEO_RE.fullmatch(video_id)
                or video_id in seen_videos
                or not isinstance(row.get('input_path'), str)
                or not isinstance(row.get('input_sha256'), str)
                or not SHA_RE.fullmatch(row['input_sha256'])):
            raise ValueError('Invalid or duplicate seed-video evidence row')
        source_path = Path(row['input_path'])
        if not source_path.is_absolute():
            source_path = sidecar_dir / source_path
        try:
            source_data = source_path.read_bytes()
            if _sha(source_data) != row['input_sha256']:
                raise ValueError('Seed-video source snapshot hash changed')
            snapshot = json.loads(source_data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Seed-video source snapshot is unreadable') from None
        if relation == 'radio':
            validate_observations(snapshot, library_hash)
            if (snapshot.get('library_sha256') != library_hash
                    or snapshot.get('identity_map_sha256') != identity_map_sha256):
                raise ValueError('Seed-video source snapshot has foreign source hashes')
            if not _candidate_for_video(snapshot, video_id, pid, matcher):
                raise ValueError('Seed-video evidence no longer has one complete-credit duration match')
            source_name = 'saved_recommendation_metadata_candidate_unverified'
        else:
            validate_source = catalog_by_pid.get(pid)
            if (validate_source is None or validate_source['video_id'] != video_id
                    or Path(validate_source['input_path']).resolve() != source_path.resolve()
                    or validate_source['input_sha256'] != row['input_sha256']):
                raise ValueError('Album-catalog seed evidence no longer validates')
            source_name = 'saved_album_catalog_metadata_candidate_unverified'
        selected[pid] = {
            'video_id': video_id, 'source': source_name,
            'source_relation': relation,
            'input_path': str(source_path.resolve()), 'input_sha256': row['input_sha256'],
        }
        seen_videos.add(video_id)
    return {pid: selected[pid] for pid in requested if pid in selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, action='append', default=[])
    parser.add_argument('--snapshot-directory', type=Path, action='append', default=[])
    parser.add_argument('--graph-provenance', type=Path, action='append', default=[],
                        help='Saved raw graph list; extracts each graph input.path')
    parser.add_argument('--catalog-evidence', type=Path,
                        help='Strict saved album-catalog candidate evidence to combine with radio hints')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Output exists; choose a new path')
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    paths = [path.resolve() for path in args.snapshot]
    for directory in args.snapshot_directory:
        paths.extend(sorted(path.resolve() for path in directory.glob('*.json')))
    for graph_file in args.graph_provenance:
        graphs = json.loads(graph_file.read_text(encoding='utf-8'))
        if not isinstance(graphs, list):
            parser.error('--graph-provenance must contain a list of raw graphs')
        for graph in graphs:
            source = graph.get('input') if isinstance(graph, dict) else None
            if not isinstance(source, dict) or not isinstance(source.get('path'), str):
                parser.error('Each raw graph needs input.path provenance')
            source_path = Path(source['path'])
            if not source_path.is_absolute():
                source_path = Path(__file__).resolve().parents[1] / source_path
            paths.append({'input': {**source, 'path': str(source_path.resolve())}})
    if not paths:
        parser.error('Supply at least one --snapshot or --snapshot-directory')
    document = build_seed_video_evidence(paths, matcher, library_hash, map_hash)
    document['identity_map_input_sha256'] = map_hash
    if args.catalog_evidence:
        catalog_bytes = args.catalog_evidence.read_bytes()
        catalog_document = json.loads(catalog_bytes)
        document = combine_seed_video_evidence(
            document, output, catalog_document, args.catalog_evidence,
            tracks, matcher, library_hash, map_hash)
        document['identity_map_input_sha256'] = map_hash
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output),
                      'accepted_seed_video_count': len(document['rows']),
                      'skipped_snapshot_count': document['audit']['skipped_snapshot_count'],
                      'edges_created': 0}))


if __name__ == '__main__':
    main()
