"""Find strict Library seed-video candidates from saved radio album IDs.

Album catalog rows are candidate observations only. They do not create radio
observations or recommendation edges, and metadata matches do not verify
recording identity.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from music_identity_map import (IdentityMap, load_library, normalize, version_facets,
                                version_signature)
from probe_ytmusic_batch import create_paced_session, verify_account
from update_genius_bundle import state_rows

PRODUCER = 'opengenius-album-catalog-seed-evidence-v1'
PLAN_PRODUCER = 'opengenius-saved-radio-album-plan-v1'
VIDEO_RE = re.compile(r'[A-Za-z0-9_-]{11}\Z')
ALBUM_RE = re.compile(r'MPRE[A-Za-z0-9_-]+\Z')
SAFE_TRACK_KEYS = {'videoId', 'title', 'artists', 'name', 'id', 'album', 'length',
                   'duration_seconds', 'videoType'}
EXCLUDED_VERSION_KINDS = {'off_vocal', 'karaoke', 'instrumental'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _norm(text):
    return normalize(text or '')


def _write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _safe_catalog(value):
    """Keep public album metadata and drop feedback, account, and token fields."""
    if isinstance(value, dict):
        return {key: _safe_catalog(item) for key, item in value.items()
                if key in SAFE_TRACK_KEYS or key == 'tracks'}
    if isinstance(value, list):
        return [_safe_catalog(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _remote_credits(track, matcher):
    artists = track.get('artists')
    if not isinstance(artists, list) or not artists:
        return frozenset()
    names = [row.get('name') for row in artists if isinstance(row, dict)]
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        return frozenset()
    return frozenset(credit for name in names for credit in matcher.artist_credits(name))


def _credit_json(credits):
    return sorted([list(credit) for credit in credits])


def _eligible_library_track(track):
    if not all(isinstance(track.get(key), str) and track[key].strip()
               for key in ('title', 'artist', 'album')):
        return False
    if type(track.get('duration_ms')) is not int or track['duration_ms'] <= 0:
        return False
    return not (set(version_facets(track['title'])['kinds']) & EXCLUDED_VERSION_KINDS)


def build_album_plan(tracks, library_hash, identity_map_hash, indexed_pids,
                     snapshot_paths, matcher, search_evidence_paths=()):
    """Plan MPRE IDs from saved radio fields or validated album-search results."""
    if not isinstance(snapshot_paths, (list, tuple)) or not isinstance(search_evidence_paths, (list, tuple)):
        raise ValueError('Snapshot and search evidence paths must be lists')
    if not snapshot_paths and not search_evidence_paths:
        raise ValueError('At least one saved radio snapshot or album-search evidence file is required')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks) or not isinstance(indexed_pids, (set, frozenset, list, tuple)):
        raise ValueError('Invalid Library or indexed PID input')
    indexed = set(indexed_pids)
    if not indexed <= set(by_pid):
        raise ValueError('Indexed state contains a PID outside the Library')
    unindexed = {pid for pid, track in by_pid.items()
                 if pid not in indexed and _eligible_library_track(track)}
    albums = {}
    skipped = []
    for raw_path in snapshot_paths:
        path = Path(raw_path).resolve()
        try:
            data = path.read_bytes()
            snapshot = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'path': str(path), 'reason': 'unreadable_or_invalid_snapshot'})
            continue
        if (not isinstance(snapshot, dict)
                or snapshot.get('library_sha256') != library_hash
                or snapshot.get('identity_map_sha256') != identity_map_hash
                or not isinstance(snapshot.get('observations'), list)):
            skipped.append({'path': str(path), 'reason': 'source_hash_or_schema_mismatch'})
            continue
        snapshot_sha = _sha(data)
        for index, observation in enumerate(snapshot['observations']):
            if (not isinstance(observation, dict) or observation.get('source') != 'ytmusic'
                    or observation.get('relation') != 'radio'):
                continue
            row = observation.get('track')
            album = row.get('album') if isinstance(row, dict) else None
            album_id = album.get('id') if isinstance(album, dict) else None
            album_name = album.get('name') if isinstance(album, dict) else None
            if not isinstance(album_id, str) or not ALBUM_RE.fullmatch(album_id):
                continue
            if not isinstance(album_name, str) or not album_name.strip():
                continue
            credits = _remote_credits(row, matcher)
            source = {'snapshot_path': str(path), 'snapshot_sha256': snapshot_sha,
                      'observation_index': index, 'source_key': 'observations[%d].track.album.id' % index,
                      'source_video_id': row.get('video_id')}
            entry = albums.setdefault(album_id, {'album_id': album_id,
                'album_name': album_name, 'source_inputs': [], 'candidate_pids': set()})
            entry['source_inputs'].append(source)
            # Rank albums by exact saved album label and complete mapped artist credits.
            # This is only a query-priority estimate; get_album rows are re-matched below.
            if credits:
                for pid in unindexed:
                    track = by_pid[pid]
                    if (_norm(track.get('album')) == _norm(album_name)
                            and matcher.artist_credits(track.get('artist') or '') == credits):
                        entry['candidate_pids'].add(pid)
    search_evidence_count = 0
    for raw_path in search_evidence_paths:
        path = Path(raw_path).resolve()
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'path': str(path), 'reason': 'unreadable_or_invalid_search_evidence'})
            continue
        if (not isinstance(document, dict)
                or document.get('producer') != 'opengenius-unindexed-album-search-evidence-v1'
                or document.get('schema_version') != 1
                or document.get('library_sha256') != library_hash
                or document.get('identity_map_sha256') != identity_map_hash
                or not isinstance(document.get('search_album_candidates'), list)):
            skipped.append({'path': str(path), 'reason': 'search_evidence_source_hash_or_schema_mismatch'})
            continue
        evidence_sha = _sha(raw)
        search_evidence_count += 1
        for candidate in document['search_album_candidates']:
            if (not isinstance(candidate, dict) or candidate.get('source_kind') != 'search_album'
                    or candidate.get('identity_status') != 'unverified_metadata_candidate'):
                continue
            album_id = candidate.get('browse_id')
            album_name = candidate.get('album_title')
            source = candidate.get('source')
            if (not isinstance(album_id, str) or not ALBUM_RE.fullmatch(album_id)
                    or not isinstance(album_name, str) or not album_name.strip()
                    or not isinstance(source, dict)):
                continue
            # Verify the exact result slot and hash here, rather than trusting
            # candidate metadata copied into the evidence wrapper.
            try:
                response_path = Path(source['response_path']).resolve()
                response_bytes = response_path.read_bytes()
                response_doc = json.loads(response_bytes)
                position = source['result_position']
                result = response_doc['response'][position]
                arguments = source['arguments']
            except (KeyError, IndexError, TypeError, OSError, UnicodeDecodeError,
                    json.JSONDecodeError):
                continue
            if (source.get('endpoint') != 'search' or source.get('browse_id_key') != 'browseId'
                    or source.get('response_sha256') != _sha(response_bytes)
                    or type(position) is not int or position < 0
                    or not isinstance(arguments, dict)
                    or arguments != {'query': candidate.get('query'), 'filter': 'albums', 'limit': 5}
                    or response_doc.get('endpoint') != 'search'
                    or response_doc.get('arguments') != arguments
                    or not isinstance(response_doc.get('observed_at'), str)
                    or not isinstance(result, dict) or result.get('browseId') != album_id
                    or result.get('title') != album_name):
                continue
            credits = _remote_credits(result, matcher)
            declared = candidate.get('canonical_artists')
            if not credits or _credit_json(credits) != declared:
                continue
            source_input = {'source_kind': 'search_album',
                'evidence_path': str(path), 'evidence_sha256': evidence_sha,
                'response_path': str(response_path), 'response_sha256': source['response_sha256'],
                'source_key': 'response[%d].browseId' % position,
                'arguments': arguments, 'result_position': position}
            entry = albums.setdefault(album_id, {'album_id': album_id,
                'album_name': album_name, 'source_inputs': [], 'candidate_pids': set()})
            entry['source_inputs'].append(source_input)
            declared_pids = candidate.get('candidate_pids')
            if not isinstance(declared_pids, list):
                continue
            for pid in declared_pids:
                track = by_pid.get(pid)
                if (pid in unindexed and track is not None
                        and _norm(track.get('album')) == _norm(album_name)
                        and matcher.artist_credits(track.get('artist') or '') == credits):
                    entry['candidate_pids'].add(pid)
    planned = []
    for entry in albums.values():
        planned.append({**entry, 'source_inputs': sorted(
            entry['source_inputs'], key=lambda item: (
                item.get('source_kind', 'radio'), item.get('snapshot_sha256', ''),
                item.get('observation_index', -1), item.get('response_sha256', ''),
                item.get('evidence_sha256', ''), item.get('snapshot_path', ''))),
            'candidate_pids': sorted(entry['candidate_pids']),
            'estimated_unindexed_local_tracks': len(entry['candidate_pids'])})
    planned.sort(key=lambda item: (-item['estimated_unindexed_local_tracks'], item['album_id']))
    return {'schema_version': 1, 'producer': PLAN_PRODUCER,
            'library_sha256': library_hash, 'identity_map_sha256': identity_map_hash,
            'source_snapshot_count': len(snapshot_paths),
            'source_search_evidence_count': search_evidence_count, 'skipped_sources': skipped,
            'albums': planned,
            'network_requests': 0, 'recommendation_edges_created': 0}


def validate_album_plan(plan, library_hash, identity_map_hash):
    if (not isinstance(plan, dict) or type(plan.get('schema_version')) is not int
            or plan.get('schema_version') != 1 or plan.get('producer') != PLAN_PRODUCER
            or plan.get('library_sha256') != library_hash
            or plan.get('identity_map_sha256') != identity_map_hash
            or not isinstance(plan.get('albums'), list)):
        raise ValueError('Album plan schema or source hashes differ')
    seen = set()
    for row in plan['albums']:
        album_id = row.get('album_id') if isinstance(row, dict) else None
        if (not isinstance(album_id, str) or not ALBUM_RE.fullmatch(album_id)
                or album_id in seen or not isinstance(row.get('source_inputs'), list)
                or not row['source_inputs']):
            raise ValueError('Album plan includes an invalid or unproven browse ID')
        seen.add(album_id)
        for source in row['source_inputs']:
            if not isinstance(source, dict):
                raise ValueError('Album plan source provenance is malformed')
            if source.get('source_kind') == 'search_album':
                required = {'source_kind', 'evidence_path', 'evidence_sha256',
                            'response_path', 'response_sha256', 'source_key',
                            'arguments', 'result_position'}
                if (set(source) != required or not isinstance(source.get('evidence_path'), str)
                        or not isinstance(source.get('response_path'), str)
                        or not all(isinstance(source.get(key), str)
                                   and re.fullmatch('[a-f0-9]{64}', source[key])
                                   for key in ('evidence_sha256', 'response_sha256'))
                        or type(source.get('result_position')) is not int
                        or source['result_position'] < 0
                        or source.get('source_key') !=
                           'response[%d].browseId' % source['result_position']
                        or source.get('arguments') != {
                            'query': source.get('arguments', {}).get('query')
                            if isinstance(source.get('arguments'), dict) else None,
                            'filter': 'albums', 'limit': 5}):
                    raise ValueError('Album plan search source provenance is malformed')
                try:
                    evidence_bytes = Path(source['evidence_path']).read_bytes()
                    response_bytes = Path(source['response_path']).read_bytes()
                    evidence_doc, response_doc = json.loads(evidence_bytes), json.loads(response_bytes)
                    response_row = response_doc['response'][source['result_position']]
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
                    raise ValueError('Album plan search source is unreadable') from None
                if (_sha(evidence_bytes) != source['evidence_sha256']
                        or _sha(response_bytes) != source['response_sha256']
                        or evidence_doc.get('producer') != 'opengenius-unindexed-album-search-evidence-v1'
                        or evidence_doc.get('library_sha256') != library_hash
                        or evidence_doc.get('identity_map_sha256') != identity_map_hash
                        or response_doc.get('endpoint') != 'search'
                        or response_doc.get('arguments') != source['arguments']
                        or not isinstance(response_row, dict)
                        or response_row.get('browseId') != album_id
                        or response_row.get('title') != row.get('album_name')):
                    raise ValueError('Album plan search browse ID or source hash changed')
                candidates = evidence_doc.get('search_album_candidates')
                if (not isinstance(candidates, list) or not any(
                        isinstance(candidate, dict)
                        and candidate.get('source_kind') == 'search_album'
                        and candidate.get('browse_id') == album_id
                        and candidate.get('album_title') == row.get('album_name')
                        and candidate.get('source', {}).get('response_sha256') == source['response_sha256']
                        and candidate.get('source', {}).get('result_position') == source['result_position']
                        and candidate.get('source', {}).get('arguments') == source['arguments']
                        for candidate in candidates)):
                    raise ValueError('Album plan source is absent from the bound search evidence')
                continue
            if (set(source) != {'snapshot_path', 'snapshot_sha256', 'observation_index',
                                'source_key', 'source_video_id'}
                    or not isinstance(source.get('snapshot_path'), str)
                    or not isinstance(source.get('snapshot_sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', source['snapshot_sha256'])
                    or source.get('source_key') != 'observations[%d].track.album.id' % source.get('observation_index', -1)):
                raise ValueError('Album plan source provenance is malformed')
            path = Path(source['snapshot_path'])
            if _sha(path.read_bytes()) != source['snapshot_sha256']:
                raise ValueError('Album plan source snapshot hash changed')
            document = json.loads(path.read_text(encoding='utf-8'))
            if (document.get('library_sha256') != library_hash
                    or document.get('identity_map_sha256') != identity_map_hash):
                raise ValueError('Album plan source snapshot has foreign source hashes')
            index = source['observation_index']
            try:
                observation = document['observations'][index]
                actual = observation['track']['album']['id']
            except (KeyError, IndexError, TypeError):
                raise ValueError('Album plan source key no longer exists') from None
            if (actual != album_id or observation.get('source') != 'ytmusic'
                    or observation.get('relation') != 'radio'):
                raise ValueError('Album plan browse ID was not saved from a radio album field')
    return plan


def catalog_album(client, album_id):
    """Call one real album endpoint, retaining only safe metadata fields."""
    return _safe_catalog(client.get_album(browseId=album_id))


def build_album_catalog_evidence(plan, responses, tracks, matcher,
                                 library_hash, identity_map_hash):
    """Make typed catalog candidate observations; no recommendation edges."""
    validate_album_plan(plan, library_hash, identity_map_hash)
    by_pid = {track['persistent_id']: track for track in tracks}
    allowed_unindexed = {pid for row in plan['albums'] for pid in row.get('candidate_pids', [])}
    response_by_id = {row['album_id']: row for row in responses}
    if len(response_by_id) != len(responses):
        raise ValueError('Duplicate album response provenance')
    catalog_rows, hint_candidates, skipped = [], [], []
    for planned in plan['albums']:
        result = response_by_id.get(planned['album_id'])
        if result is None:
            continue
        try:
            response_bytes = Path(result['path']).read_bytes()
            if _sha(response_bytes) != result.get('sha256'):
                raise ValueError('Album response source hash changed')
            response_document = json.loads(response_bytes)
        except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Album response source is unreadable') from None
        if (response_document.get('endpoint') != 'get_album'
                or response_document.get('arguments') != {'browseId': planned['album_id']}
                or response_document.get('response') != result.get('response')):
            raise ValueError('Album response provenance does not match its browse ID')
        response = response_document.get('response')
        if not isinstance(response, dict) or not isinstance(response.get('tracks'), list):
            skipped.append({'album_id': planned['album_id'], 'reason': 'invalid_album_response_shape'})
            continue
        album_name = response.get('title') or planned.get('album_name')
        for position, remote in enumerate(response['tracks'], 1):
            if not isinstance(remote, dict):
                continue
            video_id = remote.get('videoId')
            if not isinstance(video_id, str) or not VIDEO_RE.fullmatch(video_id):
                continue
            match_row = dict(remote)
            if isinstance(match_row.get('album'), str):
                match_row['album'] = {'name': match_row['album']}
            candidates = matcher.match(match_row)
            candidates = [candidate for candidate in candidates
                          if candidate.get('persistent_id') in allowed_unindexed
                          and candidate.get('artist_credit_match') == 'complete'
                          and candidate.get('persistent_id') in by_pid
                          and version_signature(candidate.get('local_metadata', {}).get('title') or '')
                          == version_signature(remote.get('title') or '')]
            candidates = [candidate for candidate in candidates
                          if _norm(candidate['local_metadata'].get('album')) == _norm(album_name)]
            pids = sorted({candidate['persistent_id'] for candidate in candidates})
            catalog_rows.append({'source': 'ytmusic', 'relation': 'album_catalog_candidate',
                'album_id': planned['album_id'], 'album_name': album_name,
                'position': position, 'video_id': video_id,
                'track': {key: remote.get(key) for key in
                          ('title', 'artists', 'album', 'length', 'duration_seconds')},
                'candidate_pids': pids, 'identity_status': 'unverified_metadata_candidate',
                'source_request': {'endpoint': 'get_album', 'argument_key': 'browseId',
                                   'argument_value': planned['album_id'],
                                   'path': result['path'], 'sha256': result['sha256']},
                'source_plan_inputs': planned['source_inputs']})
            for pid in pids:
                hint_candidates.append({'persistent_id': pid, 'video_id': video_id,
                    'album_id': planned['album_id'], 'input_path': result['path'],
                    'input_sha256': result['sha256'],
                    'source_key': {'endpoint': 'get_album', 'argument_key': 'browseId',
                                   'argument_value': planned['album_id'], 'track_position': position},
                    'source_album_id_inputs': planned['source_inputs']})
    video_pids = {}
    for hint in hint_candidates:
        video_pids.setdefault(hint['video_id'], set()).add(hint['persistent_id'])
    ambiguous_videos = {video for video, pids in video_pids.items() if len(pids) != 1}
    by_pid_hints = {}
    for hint in hint_candidates:
        if hint['video_id'] in ambiguous_videos:
            continue
        by_pid_hints.setdefault(hint['persistent_id'], []).append(hint)
    evidence_rows = []
    for pid, rows in sorted(by_pid_hints.items()):
        hint = min(rows, key=lambda row: (row['album_id'], row['video_id']))
        evidence_rows.append({**hint, 'source_relation': 'album_catalog_candidate'})
    return {'schema_version': 1, 'producer': PRODUCER,
            'library_sha256': library_hash, 'identity_map_sha256': identity_map_hash,
            'source_plan_producer': PLAN_PRODUCER,
            'rows': evidence_rows, 'catalog_observations': catalog_rows,
            'audit': {'catalog_observation_count': len(catalog_rows),
                      'seed_video_candidate_count': len(evidence_rows),
                      'ambiguous_video_id_count': len(ambiguous_videos),
                      'recommendation_edges_created': 0,
                      'recommendation_observations_created': 0,
                      'identity_status': 'unverified_metadata_candidate',
                      'skipped_album_responses': skipped}}


def _cache_call(client, cache_dir, album_id):
    request = {'endpoint': 'get_album', 'arguments': {'browseId': album_id}}
    key = _sha(json.dumps(request, sort_keys=True).encode('utf-8'))
    path = cache_dir / (key + '.json')
    if path.exists():
        stored = json.loads(path.read_text(encoding='utf-8'))
        if stored.get('endpoint') != 'get_album' or stored.get('arguments') != {'browseId': album_id}:
            raise ValueError('Album cache key does not match its source key')
        return {'album_id': album_id, 'response': stored.get('response'),
                'path': str(path.resolve()), 'sha256': _sha(path.read_bytes()), 'cache_hit': True}
    response = catalog_album(client, album_id)
    stored = {**request, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'response': response}
    _write_json(path, stored)
    return {'album_id': album_id, 'response': response, 'path': str(path.resolve()),
            'sha256': _sha(path.read_bytes()), 'cache_hit': False}


def run_discovery(*, album_plan_path, track_snapshot, identity_map_path,
                  output_dir, auth_path, state_path, interval=5, request_budget=45,
                  snapshot_paths=(), search_evidence_paths=(),
                  client_factory=None, session_factory=None):
    if (type(interval) not in (int, float) or not math.isfinite(interval)
            or not 2 <= interval <= 60):
        raise ValueError('interval must be finite and between 2 and 60 seconds')
    if type(request_budget) is not int or not 1 <= request_budget <= 100:
        raise ValueError('request_budget must be between 1 and 100')
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    tracks, library_hash, _ = load_library(track_snapshot=track_snapshot)
    map_bytes = Path(identity_map_path).read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    indexed_pids = set(state_rows(state)[0])
    if Path(album_plan_path).is_file():
        plan = json.loads(Path(album_plan_path).read_text(encoding='utf-8'))
        validate_album_plan(plan, library_hash, map_hash)
    else:
        if not snapshot_paths and not search_evidence_paths:
            raise ValueError('New album plan needs saved radio snapshots or search evidence')
        plan = build_album_plan(tracks, library_hash, map_hash, indexed_pids,
                                snapshot_paths, matcher, search_evidence_paths)
        _write_json(album_plan_path, plan)
    # Recompute exact current unindexed status from the state and reject stale candidate PIDs.
    eligible_unindexed = {track['persistent_id'] for track in tracks
                          if track['persistent_id'] not in indexed_pids and _eligible_library_track(track)}
    for album in plan['albums']:
        album['candidate_pids'] = sorted(set(album.get('candidate_pids', [])) & eligible_unindexed)
    output_dir.mkdir(parents=True)
    cache_dir = output_dir / 'album-responses'
    cache_dir.mkdir()
    if session_factory is None:
        session_factory = create_paced_session
    if client_factory is None:
        from ytmusicapi import YTMusic
        client_factory = YTMusic
    session = session_factory(interval, request_budget)
    report = {'schema_version': 1, 'status': 'running',
              'account_authentication_verified': False, 'library_sha256': library_hash,
              'identity_map_sha256': map_hash, 'album_plan_sha256': _sha(Path(album_plan_path).read_bytes()),
              'minimum_request_interval_seconds': interval, 'request_budget': request_budget,
              'http_requests': 0, 'results': [], 'recommendation_edges_created': 0}
    try:
        # SDK and transport diagnostics can contain private account or request data.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            client = client_factory(str(auth_path), requests_session=session,
                                    language='en', location='JP')
            report['account_authentication_verified'] = verify_account(client)
        responses = []
        for album in plan['albums']:
            if getattr(session, 'count', 0) >= request_budget:
                break
            try:
                result = _cache_call(client, cache_dir, album['album_id'])
            except Exception as error:
                report['status'] = 'stopped'
                report['error_type'] = type(error).__name__
                break
            responses.append(result)
            report['results'].append({'album_id': album['album_id'],
                                      'cache_hit': result['cache_hit'],
                                      'track_count': len((result['response'] or {}).get('tracks', []))})
        report['http_requests'] = getattr(session, 'count', None)
        evidence = build_album_catalog_evidence(plan, responses, tracks, matcher,
                                                library_hash, map_hash)
        evidence['album_plan_sha256'] = _sha(Path(album_plan_path).read_bytes())
        report['status'] = report.get('status') if report.get('status') != 'running' else 'completed'
        report['candidate_seed_count'] = len(evidence['rows'])
        report['catalog_observation_count'] = len(evidence['catalog_observations'])
        _write_json(output_dir / 'album-catalog-evidence.json', evidence)
        _write_json(output_dir / 'summary.json', report)
        return report
    except Exception as error:
        report['status'] = 'stopped'
        report['error_type'] = type(error).__name__
        report['http_requests'] = getattr(session, 'count', None)
        _write_json(output_dir / 'summary.json', report)
        return report
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--album-plan', type=Path, required=True,
                        help='Existing saved-radio album plan, or new output path to build from --snapshot')
    parser.add_argument('--snapshot', type=Path, action='append', default=[],
                        help='Saved observation snapshot; repeat for several snapshots')
    parser.add_argument('--search-evidence', type=Path, action='append', default=[],
                        help='Validated saved album-search evidence; repeat as needed')
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True,
                        help='Synthetic state whose indexed PIDs are excluded from album seed hints')
    parser.add_argument('--auth', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--budget', type=int, default=45,
                        help='Total paced requests including account verification (1..100)')
    args = parser.parse_args(argv)
    try:
        report = run_discovery(album_plan_path=args.album_plan,
            snapshot_paths=args.snapshot, search_evidence_paths=args.search_evidence,
            track_snapshot=args.track_snapshot,
            identity_map_path=args.identity_map, state_path=args.state,
            auth_path=args.auth, output_dir=args.output,
            interval=args.interval, request_budget=args.budget)
    except Exception as error:
        parser.exit(1, 'Catalog discovery stopped (' + type(error).__name__ + '). No retry.\n')
    print(json.dumps({key: report.get(key) for key in
                       ('status', 'http_requests', 'candidate_seed_count',
                        'catalog_observation_count', 'recommendation_edges_created')}))


if __name__ == '__main__':
    main()
