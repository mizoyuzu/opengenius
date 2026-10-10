"""Apply one root-reviewed whole-album metadata alias to an identity map.

This is a candidate metadata aid only. It does not alter Library fields or
establish recording identity.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from music_identity_map import IdentityMap, load_library, normalize, version_signature


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return path, data, json.loads(data)


def _resolve(path):
    value = Path(path)
    if value.is_absolute():
        return value.resolve()
    return (Path(__file__).resolve().parents[1] / value).resolve()


def _candidate_pid_set(matcher, remote, expected_alias):
    """Return PIDs only when the full pre-album candidate is unambiguous."""
    album = (remote.get('album') or {}).get('name')
    if album != expected_alias:
        return set()
    candidates = matcher.match(remote)
    if len(candidates) != 1:
        return set()
    candidate = candidates[0]
    if (candidate.get('title_match') != 'exact'
            or candidate.get('artist_credit_match') != 'complete'
            or candidate.get('duration_difference_seconds') is None
            or candidate['duration_difference_seconds'] > 3
            or version_signature(remote.get('title') or '')
            != version_signature((candidate.get('local_metadata') or {}).get('title') or '')):
        return set()
    return {candidate['persistent_id']}


def apply_reviewed_album_title_alias(*, track_snapshot, identity_map_path,
                                     proposals_path, source_evidence_path,
                                     candidate_audit_path, review_decisions_path,
                                     output_path, receipt_path):
    snapshot_path = Path(track_snapshot).resolve()
    map_path, map_bytes, identity_map = _read(identity_map_path)
    proposal_path, proposal_bytes, proposal_doc = _read(proposals_path)
    source_path, source_bytes, source = _read(source_evidence_path)
    audit_path, audit_bytes, audit = _read(candidate_audit_path)
    review_path, review_bytes, review = _read(review_decisions_path)
    output_path, receipt_path = Path(output_path).resolve(), Path(receipt_path).resolve()
    inputs = (snapshot_path, map_path, proposal_path, source_path, audit_path, review_path)
    if output_path.exists() or receipt_path.exists() or output_path == receipt_path:
        raise FileExistsError('Album map output and receipt must be new, distinct paths')
    if output_path in inputs or receipt_path in inputs:
        raise ValueError('Output paths cannot replace inputs')

    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    map_hash = _sha(map_bytes)
    snapshot_hash = _sha(snapshot_path.read_bytes())
    proposal_hash, source_hash = _sha(proposal_bytes), _sha(source_bytes)
    audit_hash, review_hash = _sha(audit_bytes), _sha(review_bytes)
    payload = proposal_doc.get('proposal') or {}
    remote_alias = payload.get('remote_album_alias')
    canonical_album = payload.get('canonical_library_album_target')
    if (proposal_doc.get('schema_version') != 3
            or proposal_doc.get('status') != 'needs_root_review'
            or proposal_doc.get('proposed_identity_map_schema_field') != 'album_title_aliases'
            or payload.get('metadata_only') is not True
            or payload.get('recording_identity_verified') is not False
            or payload.get('new_recording_edges_authorized') is not False
            or not isinstance(remote_alias, str) or not remote_alias
            or not isinstance(canonical_album, str) or not canonical_album):
        raise ValueError('Proposal is not the exact metadata-only album alias review')
    if (proposal_doc.get('source_evidence') != {'path': str(Path(source_evidence_path)),
                                                'sha256': source_hash}
            or proposal_doc.get('cache_audit') != {'path': str(Path(candidate_audit_path)),
                                                   'sha256': audit_hash}
            or source.get('proposed_exact_remote_album_alias') != remote_alias
            or source.get('local_album') != canonical_album
            or source.get('recording_identity_verified') is not False
            or source.get('album_edition_identity_verified') is not False
            or audit.get('source_evidence') != {'path': str(Path(source_evidence_path)),
                                                'sha256': source_hash}):
        raise ValueError('Source evidence or cache audit does not match the proposal')
    if (audit.get('library', {}).get('track_snapshot_sha256') != snapshot_hash
            or audit.get('library', {}).get('identity_map_sha256') != map_hash
            or audit.get('library', {}).get('library_sha256') != library_hash
            or audit.get('library', {}).get('local_album_pid_count') != sum(
                track.get('album') == canonical_album for track in tracks)):
        raise ValueError('Candidate audit is bound to a different Library or identity map')
    approved = review.get('approved_mapping') or {}
    approval = review.get('approval') or {}
    if (review.get('schema_version') != 1
            or review.get('proposal_sha256') != proposal_hash
            or review.get('source_evidence_sha256') != source_hash
            or review.get('candidate_audit_sha256') != audit_hash
            or review.get('input_identity_map_sha256') != map_hash
            or review.get('library_sha256') != library_hash
            or approval.get('reviewed_by') != 'root'
            or approval.get('metadata_equivalence_only') is not True
            or approval.get('recording_identity_verified') is not False
            or approval.get('new_recording_edges_authorized') is not False
            or approved != {'remote_album_alias': remote_alias,
                            'canonical_library_album_target': canonical_album}):
        raise ValueError('Root review does not approve this exact alias and evidence chain')

    cache_inputs = audit.get('cache_scan', {}).get('files')
    if not isinstance(cache_inputs, list) or not cache_inputs:
        raise ValueError('Candidate audit lacks hashed cache inputs')
    cached_rows = []
    for source_file in cache_inputs:
        if not isinstance(source_file, dict) or not isinstance(source_file.get('path'), str):
            raise ValueError('Malformed cached observation receipt')
        path = _resolve(source_file['path'])
        if not path.is_file() or _sha(path.read_bytes()) != source_file.get('sha256'):
            raise ValueError('A cached observation bound by the audit changed')
        doc = json.loads(path.read_bytes())
        for observation in doc.get('observations') or []:
            track = observation.get('track') or {}
            if (not observation.get('is_seed')
                    and (track.get('album') or {}).get('name') == remote_alias):
                cached_rows.append((path, observation))

    matcher = IdentityMap(identity_map, tracks, library_hash)
    if matcher.album_title_aliases:
        raise ValueError('Input map already contains album aliases; this transition expects the reviewed base map')
    candidate_pids = set()
    eligible_video_ids = set()
    eligible_observation_count = 0
    for _, observation in cached_rows:
        remote = observation.get('track') or {}
        pids = _candidate_pid_set(matcher, remote, remote_alias)
        if len(pids) != 1:
            continue
        pid = next(iter(pids))
        candidate_pids.add(pid)
        if remote.get('video_id'):
            eligible_video_ids.add(remote['video_id'])
        eligible_observation_count += 1
    expected_pids = {row.get('pid') for row in payload.get('candidate_tracks') or []}
    expected_yield = payload.get('candidate_yield') or {}
    if (candidate_pids != expected_pids
            or len(candidate_pids) != expected_yield.get(
                'distinct_local_pids_passing_exact_title_credit_duration_version_and_pre_album_uniqueness_gates_after_simulated_alias')
            or eligible_observation_count != expected_yield.get('eligible_cached_observations')
            or len(eligible_video_ids) != expected_yield.get('eligible_unique_videos')
            or audit.get('summary', {}).get('album_alias_only_candidate_yield_pid_count') != len(candidate_pids)):
        raise ValueError('Recomputed pre-album candidate yield differs from root-reviewed audit')

    result = copy.deepcopy(identity_map)
    result['album_title_aliases'] = [{'enabled': True,
                                     'canonical_library_album': canonical_album,
                                     'exact_remote_album_aliases': [remote_alias],
                                     'status': 'root_reviewed_metadata_alias',
                                     'evidence': {
                                         'proposal_sha256': proposal_hash,
                                         'source_evidence_sha256': source_hash,
                                         'candidate_audit_sha256': audit_hash,
                                         'review_decisions_sha256': review_hash,
                                         'input_identity_map_sha256': map_hash}}]
    new_matcher = IdentityMap(result, tracks, library_hash)
    # Every recomputed hit must gain exactly album metadata support while all
    # local metadata stays byte-for-byte the same and no recording links appear.
    verified_observations = 0
    for _, observation in cached_rows:
        remote = observation.get('track') or {}
        pids = _candidate_pid_set(matcher, remote, remote_alias)
        if len(pids) != 1:
            continue
        before, = matcher.match(remote)
        after, = new_matcher.match(remote)
        if (before != {**after, 'album_match': False}
                or after.get('persistent_id') not in expected_pids
                or after.get('album_match') is not True):
            raise ValueError('Alias changed candidate metadata beyond album_match')
        verified_observations += 1
    if verified_observations != eligible_observation_count or result.get('recording_links'):
        raise ValueError('Album alias unexpectedly changed candidate count or recording edges')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    output_hash = _sha(output_path.read_bytes())
    receipt = {'schema_version': 1,
               'operation': 'apply_root_reviewed_exact_whole_album_metadata_alias',
               'library_sha256': library_hash,
               'track_snapshot_path': str(snapshot_path), 'track_snapshot_sha256': snapshot_hash,
               'input_identity_map_path': str(map_path), 'input_identity_map_sha256': map_hash,
               'output_identity_map_path': str(output_path), 'output_identity_map_sha256': output_hash,
               'proposals_path': str(proposal_path), 'proposal_sha256': proposal_hash,
               'source_evidence_path': str(source_path), 'source_evidence_sha256': source_hash,
               'candidate_audit_path': str(audit_path), 'candidate_audit_sha256': audit_hash,
               'review_decisions_path': str(review_path), 'review_decisions_sha256': review_hash,
               'alias_mapping': {'remote_album_alias': remote_alias,
                                 'canonical_library_album_target': canonical_album},
               'candidate_yield': {'distinct_pids': len(candidate_pids),
                                   'eligible_observations': eligible_observation_count,
                                   'eligible_unique_videos': len(eligible_video_ids)},
               'verified_candidate_observation_count': verified_observations,
               'canonical_library_fields_changed': False,
               'recording_identity_verified': False, 'recording_edges_added': 0}
    with receipt_path.open('x', encoding='utf-8') as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return receipt


def verify_album_alias_transition(*, track_snapshot, identity_map_path,
                                  proposals_path, source_evidence_path,
                                  candidate_audit_path, review_decisions_path,
                                  output_path, receipt_path):
    output_path, receipt_path = Path(output_path).resolve(), Path(receipt_path).resolve()
    saved_receipt = json.loads(receipt_path.read_bytes())
    with tempfile.TemporaryDirectory(prefix='opengenius-album-alias-') as temporary:
        temporary = Path(temporary)
        rebuilt_map, rebuilt_receipt = temporary / 'map.json', temporary / 'receipt.json'
        rebuilt = apply_reviewed_album_title_alias(
            track_snapshot=track_snapshot, identity_map_path=identity_map_path,
            proposals_path=proposals_path, source_evidence_path=source_evidence_path,
            candidate_audit_path=candidate_audit_path,
            review_decisions_path=review_decisions_path,
            output_path=rebuilt_map, receipt_path=rebuilt_receipt)
        if _sha(output_path.read_bytes()) != rebuilt['output_identity_map_sha256']:
            raise ValueError('Staged identity map is not reproduced by its root-reviewed alias')
        saved_receipt.pop('output_identity_map_path', None)
        rebuilt.pop('output_identity_map_path', None)
        if saved_receipt != rebuilt:
            raise ValueError('Album alias receipt differs from recomputed review')
    return saved_receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--identity-map', required=True, type=Path)
    parser.add_argument('--proposals', required=True, type=Path)
    parser.add_argument('--source-evidence', required=True, type=Path)
    parser.add_argument('--candidate-audit', required=True, type=Path)
    parser.add_argument('--review-decisions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = apply_reviewed_album_title_alias(
            track_snapshot=args.track_snapshot, identity_map_path=args.identity_map,
            proposals_path=args.proposals, source_evidence_path=args.source_evidence,
            candidate_audit_path=args.candidate_audit,
            review_decisions_path=args.review_decisions,
            output_path=args.output, receipt_path=args.receipt)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '): ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
