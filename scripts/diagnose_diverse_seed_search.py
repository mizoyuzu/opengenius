"""Audit cached seed-search hits against strict local identity matching.

This is a read-only, offline diagnostic. A close title/album/duration result is
reported as metadata evidence only; it does not assert the recording identity.
"""
import argparse
import hashlib
import json
from pathlib import Path

from music_identity_map import (IdentityMap, normalize, proposed_title_alias,
                                version_signature)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _duration_seconds(remote):
    value = remote.get('duration_seconds')
    if isinstance(value, (int, float)):
        return float(value)
    length = remote.get('length')
    if isinstance(length, str):
        try:
            seconds = 0
            for part in length.split(':'):
                seconds = seconds * 60 + int(part)
            return float(seconds)
        except ValueError:
            return None
    return None


def _match_result(track, remote, matcher):
    pid = track['persistent_id']
    local_title = normalize(track.get('title') or '')
    remote_title = normalize(remote.get('title') or '')
    active_title = remote_title == local_title or remote_title in matcher.titles.get(pid, [])
    proposal = proposed_title_alias(remote.get('title') or '')
    proposed_title = bool(proposal and normalize(proposal['value']) == local_title)
    facet_match = version_signature(track.get('title') or '') == version_signature(remote.get('title') or '')
    remote_duration = _duration_seconds(remote)
    duration_delta = (round(abs(track['duration_ms'] / 1000 - remote_duration), 3)
                      if remote_duration is not None and isinstance(track.get('duration_ms'), int)
                      else None)
    remote_album = (remote.get('album') or {}).get('name')
    album_match = (normalize(remote_album) == normalize(track.get('album') or '')
                   if isinstance(remote_album, str) and remote_album else None)
    local_credit_set = set(matcher.artist_credits(track.get('artist') or ''))
    remote_credit_set = {credit for artist in (remote.get('artists') or [])
                         if isinstance(artist, dict) and artist.get('name')
                         for credit in matcher.artist_credits(artist['name'])}
    strict = matcher.match(remote)
    proposed = matcher.match(remote, include_proposed=True)
    seed_matches = [row for row in strict if row['persistent_id'] == pid]
    proposed_matches = [row for row in proposed if row['persistent_id'] == pid]
    return {
        'video_id': remote.get('videoId'),
        'remote_title': remote.get('title'),
        'remote_artists': [{'name': row.get('name'), 'id': row.get('id')}
                           for row in remote.get('artists') or []],
        'remote_album': remote_album,
        'remote_duration_seconds': remote_duration,
        'title_exact_or_active_alias': active_title,
        'pending_title_alias_proposal': proposed_title,
        'version_facets_match': facet_match,
        'duration_difference_seconds': duration_delta,
        'album_exact_after_normalization': album_match,
        'artist_credit_sets_match': local_credit_set == remote_credit_set,
        'local_canonical_credits': sorted(local_credit_set),
        'remote_canonical_credits': sorted(remote_credit_set),
        'remote_credit_extras': sorted(remote_credit_set - local_credit_set),
        'local_credit_missing_remotely': sorted(local_credit_set - remote_credit_set),
        'current_matcher_seed_pid_candidate': bool(seed_matches),
        'current_matcher_artist_credit_status': (seed_matches[0]['artist_credit_match']
                                                  if seed_matches else None),
        'pending_alias_seed_pid_candidate': bool(proposed_matches),
        'pending_alias_artist_credit_status': (proposed_matches[0]['artist_credit_match']
                                                if proposed_matches else None),
        'metadata_close_candidate': bool(
            (active_title or proposed_title) and facet_match
            and duration_delta is not None and duration_delta <= 3),
        'same_album_title_version_and_duration_candidate': bool(
            (active_title or proposed_title) and facet_match and album_match is True
            and duration_delta is not None and duration_delta <= 3),
    }


def diagnose(run_dir, track_snapshot_path, identity_map_path):
    run_dir = Path(run_dir)
    seeds_doc = _read(run_dir / 'seeds.json')
    summary_files = sorted(run_dir.glob('summary-*.json'))
    if len(summary_files) != 1:
        raise ValueError('Expected one collector summary in the run directory')
    summary_path = summary_files[0]
    summary = _read(summary_path)
    snapshot_doc = _read(track_snapshot_path)
    tracks = snapshot_doc.get('tracks') or []
    by_pid = {row.get('persistent_id'): row for row in tracks}
    identity_bytes = Path(identity_map_path).read_bytes()
    identity_sha = _sha(identity_bytes)
    if (len(by_pid) != len(tracks)
            or seeds_doc.get('library_sha256') != snapshot_doc.get('source_sha256')
            or summary.get('library_sha256') != snapshot_doc.get('source_sha256')
            or seeds_doc.get('identity_map_sha256') != identity_sha):
        raise ValueError('Seed plan, collector summary, Library snapshot, and map hashes differ')
    matcher = IdentityMap(json.loads(identity_bytes), tracks, snapshot_doc['source_sha256'])
    result_by_pid = {row['persistent_id']: row for row in summary.get('results', [])
                     if isinstance(row, dict) and isinstance(row.get('persistent_id'), str)}

    requests = []
    request_hashes = {}
    for path in sorted((run_dir / 'requests').glob('*.json')):
        raw = path.read_bytes()
        doc = json.loads(raw)
        if (not isinstance(doc, dict) or set(doc) != {'endpoint', 'arguments', 'observed_at', 'response'}
                or doc.get('endpoint') != 'search' or not isinstance(doc.get('response'), list)):
            continue
        query = (doc.get('arguments') or {}).get('query')
        if not isinstance(query, str):
            continue
        record = {'document': doc, 'path': str(path), 'sha256': _sha(raw)}
        requests.append(record)
        request_hashes[str(path)] = record['sha256']

    records = []
    used_requests = set()
    for seed in seeds_doc.get('seeds', []):
        pid = seed.get('persistent_id')
        track = by_pid.get(pid)
        if track is None:
            raise ValueError('Seed PID is missing from the Library snapshot')
        query_matches = [request for request in requests
                         if normalize(track.get('title') or '') in
                         normalize(request['document']['arguments']['query'])]
        if len(query_matches) != 1:
            raise ValueError('Could not uniquely link cached title search to seed ' + pid)
        request = query_matches[0]
        used_requests.add(request['path'])
        response = request['document']['response']
        candidates = [_match_result(track, remote, matcher) for remote in response
                      if isinstance(remote, dict) and isinstance(remote.get('title'), str)]
        candidates.sort(key=lambda row: (
            not row['same_album_title_version_and_duration_candidate'],
            not row['metadata_close_candidate'],
            row['duration_difference_seconds'] is None,
            row['duration_difference_seconds'] if row['duration_difference_seconds'] is not None else 999999,
            row['video_id'] or ''))
        exact = [row for row in candidates if row['title_exact_or_active_alias']]
        pending = [row for row in candidates if row['pending_title_alias_proposal']]
        close = [row for row in candidates if row['metadata_close_candidate']]
        high_signal = [row for row in candidates
                       if row['same_album_title_version_and_duration_candidate']]
        collector_result = result_by_pid.get(pid, {'status': 'missing_result'})
        records.append({
            'persistent_id': pid,
            'local_title': track.get('title'),
            'local_artist_credit': track.get('artist'),
            'local_album': track.get('album'),
            'local_duration_ms': track.get('duration_ms'),
            'seed_series': seed.get('seed_series'),
            'collector_status': collector_result.get('status'),
            'collector_candidate_video_ids': collector_result.get('candidate_video_ids'),
            'search_query': request['document']['arguments']['query'],
            'search_result_count': len(response),
            'search_request_sha256': request['sha256'],
            'exact_or_active_title_result_count': len(exact),
            'pending_title_alias_result_count': len(pending),
            'metadata_close_candidate_count': len(close),
            'same_album_title_version_and_duration_candidate_count': len(high_signal),
            'current_matcher_seed_pid_candidate_count': sum(
                row['current_matcher_seed_pid_candidate'] for row in candidates),
            'complete_artist_credit_candidate_count': sum(
                row['current_matcher_artist_credit_status'] == 'complete' for row in candidates),
            'partial_artist_credit_candidate_count': sum(
                row['current_matcher_artist_credit_status'] == 'partial' for row in candidates),
            'pending_alias_seed_pid_candidate_count': sum(
                row['pending_alias_seed_pid_candidate'] for row in candidates),
            'diagnostic_class': _classify(collector_result.get('status'), exact=exact, pending=pending,
                                          close=close, high_signal=high_signal),
            'best_metadata_candidates': (high_signal or close or pending or exact)[:3],
        })
    if len(records) != len(seeds_doc.get('seeds', [])) or used_requests != {row['path'] for row in requests}:
        raise ValueError('Not all seed records and cached title searches were accounted for')

    classes = {}
    for row in records:
        classes[row['diagnostic_class']] = classes.get(row['diagnostic_class'], 0) + 1
    return {
        'schema_version': 1,
        'run_dir': str(run_dir),
        'collector_summary_sha256': _sha(summary_path.read_bytes()),
        'library_sha256': snapshot_doc['source_sha256'],
        'track_snapshot_sha256': _sha(Path(track_snapshot_path).read_bytes()),
        'identity_map_sha256': identity_sha,
        'request_count': len(requests),
        'request_hashes_validated': len(request_hashes),
        'seed_count': len(records),
        'collector_summary_status_counts': _status_counts(summary.get('results', [])),
        'search_result_count_total': sum(row['search_result_count'] for row in records),
        'search_queries_returning_zero_results': sum(row['search_result_count'] == 0 for row in records),
        'diagnostic_class_counts': classes,
        'current_matcher_seed_pid_candidate_count': sum(
            row['current_matcher_seed_pid_candidate_count'] > 0 for row in records),
        'unresolved_seeds_with_same_album_title_version_duration_candidates': sum(
            row['collector_status'] == 'search_unresolved'
            and row['same_album_title_version_and_duration_candidate_count'] > 0 for row in records),
        'same_album_title_version_duration_metadata_candidates': sum(
            row['same_album_title_version_and_duration_candidate_count'] > 0 for row in records),
        'safety_notes': [
            'Search result presence and close metadata are not recording identity proof.',
            'No Japanese-to-Latin artist equivalence was inferred.',
            'CV text, performer order, extra credits, and version facets remain intact.',
            'Pending title alias proposals remain review-only and never enable strict matching.',
        ],
        'seeds': records,
    }


def _classify(collector_status, exact, pending, close, high_signal):
    if collector_status == 'collected' and any(row['current_matcher_seed_pid_candidate'] for row in high_signal + close + exact + pending):
        return 'collector_success_current_matcher_candidate'
    if high_signal:
        return 'strong_metadata_match_artist_credit_mapping_needed'
    if close:
        if any(not row['version_facets_match'] for row in close):
            return 'title_candidate_version_or_duration_conflict'
        if any(row['duration_difference_seconds'] is None or row['duration_difference_seconds'] > 3
               for row in close):
            return 'title_candidate_duration_conflict'
        return 'title_candidate_album_or_artist_conflict'
    if pending:
        return 'pending_title_alias_review_without_strong_recording_match'
    if exact:
        return 'exact_title_only_search_hits'
    return 'no_exact_or_reviewable_title_hit'


def _status_counts(results):
    counts = {}
    for row in results:
        status = row.get('status', 'missing') if isinstance(row, dict) else 'invalid'
        counts[status] = counts.get(status, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--identity-map', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = diagnose(args.run_dir, args.track_snapshot, args.identity_map)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')


if __name__ == '__main__':
    main()
