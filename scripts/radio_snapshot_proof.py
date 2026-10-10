"""Source-bound proof for authenticated YT Music radio-only recommendations.

This policy requires a real ``get_watch_playlist(radio=True)`` response and
replays that response against its exact Library snapshot and active identity
map. Related-song observations are auxiliary and are intentionally omitted.
The response-cache hash is sealed when this proof is created; it does not
claim that this hash was captured by the original collector process.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library, version_signature
from probe_ytmusic import observe
from probe_ytmusic_batch import summary as collector_result_summary
from seed_video_evidence import select_seed_video_evidence
from emulate_ytmusic_candidates import candidate_graph


PRODUCER = 'opengenius-authenticated-radio-snapshot-proof-v1'
POLICY = 'authenticated-watch-radio-only-v1'


class RadioSnapshotIneligible(ValueError):
    """Inputs replay but fail the strict seed or outgoing-target gate."""

    def __init__(self, persistent_id, reason='no_strict_unique_outgoing_targets',
                 details=None):
        self.persistent_id = persistent_id
        self.reason = reason
        self.details = details or {}
        super().__init__(self.reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    resolved = Path(path).resolve()
    data = resolved.read_bytes()
    return resolved, data, json.loads(data)


def _hash_file(path):
    resolved, data, _ = _read(path)
    return {'path': str(resolved), 'sha256': _sha(data)}


def _check_collector_summary(summary, persistent_id, library_hash, snapshot):
    if (not isinstance(summary, dict) or type(summary.get('schema_version')) is not int
            or summary.get('schema_version') != 1
            or summary.get('account_authentication_verified') is not True
            or summary.get('library_sha256') != library_hash
            or summary.get('http_error_status') is not None):
        raise ValueError('Collector summary lacks the authenticated schema-1 receipt')
    status = summary.get('status')
    if status == 'completed':
        valid_budget = (type(summary.get('http_requests')) is int
                        and type(summary.get('request_budget')) is int
                        and 0 < summary['http_requests'] <= summary['request_budget'])
    elif status == 'stopped':
        valid_budget = (type(summary.get('http_requests')) is int
                        and type(summary.get('request_budget')) is int
                        and summary['http_requests'] == summary['request_budget']
                        and summary.get('error_type') == 'RuntimeError'
                        and isinstance(summary.get('error_location'), dict)
                        and summary['error_location'].get('function') == 'request')
    else:
        valid_budget = False
    if not valid_budget:
        raise ValueError('Collector summary does not prove a bounded successful or capped run')
    results = [row for row in summary.get('results', [])
               if isinstance(row, dict) and row.get('persistent_id') == persistent_id]
    if len(results) != 1 or results[0].get('status') != 'collected':
        raise ValueError('Collector summary does not contain exactly one collected seed result')
    recomputed = collector_result_summary(snapshot)
    if any(results[0].get(key) != value for key, value in recomputed.items()):
        raise ValueError('Collector result counters differ from its saved snapshot')
    return results[0]


def _strict_candidate(candidate, remote_title):
    local = candidate.get('local_metadata') or {}
    delta = candidate.get('duration_difference_seconds')
    return (candidate.get('alias_review_status') == 'active_metadata_candidate'
            and candidate.get('title_match') in ('exact', 'explicit_alias')
            and candidate.get('artist_credit_match') == 'complete'
            and candidate.get('album_match') is True
            and type(delta) in (int, float) and 0 <= delta <= 3
            and version_signature(local.get('title') or '') == version_signature(remote_title))


def _proof_from_inputs(*, track_snapshot_path, identity_map_path, snapshot_path,
                       collector_summary_path, collector_seed_manifest_path,
                       batch_intent_path, raw_radio_cache_path, sealed_at_utc):
    track_path, track_bytes, _ = _read(track_snapshot_path)
    map_path, map_bytes, map_doc = _read(identity_map_path)
    snapshot_file, snapshot_bytes, snapshot = _read(snapshot_path)
    summary_file, summary_bytes, summary = _read(collector_summary_path)
    manifest_file, manifest_bytes, manifest = _read(collector_seed_manifest_path)
    intent_file, intent_bytes, intent = _read(batch_intent_path)
    cache_file, cache_bytes, cache = _read(raw_radio_cache_path)
    tracks, library_hash, _ = load_library(track_snapshot=track_path)
    map_hash = _sha(map_bytes)
    if (map_doc.get('library_sha256') != library_hash
            or snapshot.get('library_sha256') != library_hash
            or snapshot.get('identity_map_sha256') != map_hash
            or snapshot.get('account_authentication_verified') is not True):
        raise ValueError('Snapshot, identity map, and Library provenance do not agree')
    config = intent.get('runner_config') if isinstance(intent, dict) else None
    if (not isinstance(config, dict)
            or intent.get('library_sha256') != library_hash
            or intent.get('identity_map_sha256') != map_hash
            or config.get('track_snapshot') != {'path': str(track_path), 'sha256': _sha(track_bytes)}
            or config.get('identity_map') != {'path': str(map_path), 'sha256': map_hash}
            or config.get('candidate_limit') != 100):
        raise ValueError('Batch intent does not bind this exact snapshot, map, and radio window')
    for source in intent.get('source_inputs', []):
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Batch intent has a malformed source receipt')
        source_path = Path(source['path']).resolve()
        if _sha(source_path.read_bytes()) != source['sha256']:
            raise ValueError('A batch intent source receipt changed')

    local_seed = snapshot.get('local_seed')
    if not isinstance(local_seed, dict):
        raise ValueError('Collector snapshot has no local seed receipt')
    pid = local_seed.get('persistent_id')
    if not isinstance(pid, str) or not pid:
        raise ValueError('Collector snapshot has no seed PID')
    result_summary = _check_collector_summary(summary, pid, library_hash, snapshot)
    selected_video_id = snapshot.get('selected_video_id')
    if (not isinstance(selected_video_id, str) or len(selected_video_id) != 11
            or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for ch in selected_video_id)):
        raise ValueError('Snapshot selected video ID is malformed')
    manifest_seeds = manifest.get('seeds') if isinstance(manifest, dict) else None
    plan_ref, evidence_ref = intent.get('seed_plan'), intent.get('seed_video_evidence')
    if (manifest.get('library_sha256') != library_hash
            or manifest.get('identity_map_sha256') != map_hash
            or not isinstance(manifest_seeds, list)
            or not isinstance(plan_ref, dict) or not isinstance(evidence_ref, dict)):
        raise ValueError('Collector seed manifest differs from the authenticated batch intent')
    manifest_seed_rows = [row for row in manifest_seeds
                          if isinstance(row, dict) and row.get('persistent_id') == pid]
    if (len(manifest_seed_rows) != 1 or manifest_seed_rows[0] != local_seed
            or pid not in intent.get('seed_pids', [])):
        raise ValueError('Snapshot seed does not exactly match its batch seed manifest')
    seed_plan_path, seed_plan_bytes, seed_plan = _read(plan_ref.get('path'))
    seed_evidence_path, seed_evidence_bytes, seed_evidence = _read(evidence_ref.get('path'))
    if (_sha(seed_plan_bytes) != plan_ref.get('sha256')
            or _sha(seed_evidence_bytes) != evidence_ref.get('sha256')
            or manifest.get('seed_plan_sha256') != plan_ref.get('sha256')
            or manifest.get('seed_video_evidence_sha256') != evidence_ref.get('sha256')
            or seed_plan.get('source_library_sha256') != library_hash
            or not any(isinstance(row, dict) and row.get('persistent_id') == pid
                       for row in seed_plan.get('seeds', []))):
        raise ValueError('Seed plan or selected-video evidence differs from its intent receipt')

    evidence = [row for row in snapshot.get('request_evidence', [])
                if isinstance(row, dict) and row.get('endpoint') == 'get_watch_playlist'
                and isinstance(row.get('arguments'), dict)
                and row['arguments'].get('radio') is True]
    if len(evidence) != 1:
        raise ValueError('Snapshot must contain exactly one radio=true watch request')
    request = evidence[0]
    arguments = request['arguments']
    if (arguments.get('videoId') != selected_video_id
            or arguments.get('limit') != 100
            or set(arguments) != {'videoId', 'radio', 'limit'}
            or request.get('cache_hit') is not False
            or not isinstance(request.get('observed_at'), str)):
        raise ValueError('Radio request is not an exact live seed-bound limit-100 call')
    request_rows = [row for row in snapshot.get('requests', [])
                    if isinstance(row, dict) and row.get('endpoint') == 'watch_radio']
    if (len(request_rows) != 1
            or request_rows[0].get('observed_at') != request.get('observed_at')):
        raise ValueError('Collector radio request receipt differs from its source evidence')

    if (cache.get('endpoint') != 'get_watch_playlist'
            or cache.get('arguments') != arguments
            or cache.get('observed_at') != request.get('observed_at')
            or not isinstance(cache.get('response'), dict)
            or not isinstance(cache['response'].get('tracks'), list)):
        raise ValueError('Raw cache entry does not bind the exact live radio call')
    raw_rows = cache['response']['tracks']
    if request_rows[0].get('returned_track_count') != len(raw_rows):
        raise ValueError('Radio cache response count differs from the collector snapshot')

    matcher = IdentityMap(map_doc, tracks, library_hash)
    validated_seed_evidence = select_seed_video_evidence(
        seed_evidence, seed_evidence_path, manifest_seeds, tracks, matcher,
        library_hash, map_hash)
    selected_seed_evidence = validated_seed_evidence.get(pid)
    if (not isinstance(selected_seed_evidence, dict)
            or selected_seed_evidence.get('video_id') != selected_video_id):
        raise ValueError('Radio call video ID is not the exact validated video for its local seed')
    replayed = observe(raw_rows, 'radio', request['observed_at'], tracks,
                       selected_video_id, candidate_matcher=matcher.match)
    saved_radio = [row for row in snapshot.get('observations', [])
                   if isinstance(row, dict) and row.get('relation') == 'radio']
    if replayed != saved_radio:
        raise ValueError('Raw radio response does not reproduce the saved radio observations')

    seed_rows = [row for row in replayed if row.get('is_seed')]
    seed_candidates = matcher.match(seed_rows[0]['track']) if len(seed_rows) == 1 else []
    if (len(seed_candidates) != 1 or seed_candidates[0].get('persistent_id') != pid
            or not _strict_candidate(seed_candidates[0], seed_rows[0]['track'].get('title'))):
        seed_title = seed_rows[0]['track'].get('title') if len(seed_rows) == 1 else None
        details = {
            'selected_video_id': selected_video_id,
            'remote_seed_title': seed_title,
            'candidate_pids': sorted({row.get('persistent_id')
                                      for row in seed_candidates
                                      if isinstance(row.get('persistent_id'), str)}),
            'candidate_dispositions': [
                {'persistent_id': row.get('persistent_id'),
                 'title_match': row.get('title_match'),
                 'artist_credit_match': row.get('artist_credit_match'),
                 'album_match': row.get('album_match'),
                 'duration_difference_seconds': row.get('duration_difference_seconds'),
                 'version_signature_match': (
                     version_signature((row.get('local_metadata') or {}).get('title') or '')
                     == version_signature(seed_title or ''))}
                for row in seed_candidates],
        }
        raise RadioSnapshotIneligible(pid, 'seed_not_strictly_matched', details)

    observations_by_video = {}
    strict_by_position = {}
    for row in replayed:
        if row.get('is_seed'):
            continue
        video = row['track']['video_id']
        matches = matcher.match(row['track'])
        strict = ([candidate for candidate in matches
                   if _strict_candidate(candidate, row['track'].get('title'))]
                  if len(matches) == 1 else [])
        strict_by_position[row['position']] = len(strict) == 1
        observations_by_video.setdefault(video, []).append({
            'position': row['position'],
            'candidate_pid': strict[0]['persistent_id'] if strict else None,
            'valid': len(strict) == 1,
        })

    target_by_video = {}
    dispositions = []
    for video, rows in observations_by_video.items():
        pids = {row['candidate_pid'] for row in rows if row['valid']}
        all_valid = all(row['valid'] for row in rows)
        if all_valid and len(pids) == 1:
            target = next(iter(pids))
            if target != pid:
                target_by_video[video] = {'persistent_id': target,
                                          'positions': [row['position'] for row in rows]}
                dispositions.append({'video_id': video, 'status': 'accepted_unique_target',
                                     'persistent_id': target,
                                     'positions': [row['position'] for row in rows]})
            else:
                dispositions.append({'video_id': video, 'status': 'seed_self_edge_rejected',
                                     'persistent_id': target,
                                     'positions': [row['position'] for row in rows]})
        else:
            dispositions.append({'video_id': video,
                                 'status': 'ambiguous_or_failed_strict_match',
                                 'candidate_pids': sorted(pids),
                                 'positions': [row['position'] for row in rows]})

    target_pids, seen = [], set()
    for video, row in target_by_video.items():
        target = row['persistent_id']
        if target not in seen:
            target_pids.append(target)
            seen.add(target)
    if not target_pids:
        raise RadioSnapshotIneligible(pid)
    eligible_positions = sorted({position for row in target_by_video.values()
                                 for position in row['positions']})
    strict_observations = [row for row in replayed
                           if row.get('is_seed') or row.get('position') in eligible_positions]
    radio_graph = candidate_graph({
        'library_sha256': library_hash,
        'selected_video_id': selected_video_id,
        'observations': strict_observations,
    }, matcher, library_hash)
    if (radio_graph.get('root_pid') != pid
            or radio_graph.get('ordered_target_pids') != target_pids):
        raise ValueError('Strict radio candidate graph differs from the versioned proof targets')

    try:
        parsed_seal = datetime.fromisoformat(sealed_at_utc.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        raise ValueError('Raw cache sealing time is not an ISO timestamp')
    if parsed_seal.tzinfo is None or parsed_seal.utcoffset() is None or parsed_seal.utcoffset().total_seconds() != 0:
        raise ValueError('Raw cache sealing time must be UTC')

    return {
        'schema_version': 1,
        'producer': PRODUCER,
        'policy': POLICY,
        'sealed_at_utc': sealed_at_utc,
        'raw_cache_hash_sealing': 'hash captured when this proof was created; not claimed to be captured at API request time',
        'track_snapshot': {'path': str(track_path), 'sha256': _sha(track_bytes)},
        'identity_map': {'path': str(map_path), 'sha256': map_hash},
        'library_sha256': library_hash,
        'collector_snapshot': {'path': str(snapshot_file), 'sha256': _sha(snapshot_bytes)},
        'collector_summary': {'path': str(summary_file), 'sha256': _sha(summary_bytes),
                              'account_authentication_verified': True,
                              'result': result_summary},
        'collector_seed_manifest': {'path': str(manifest_file), 'sha256': _sha(manifest_bytes)},
        'batch_intent': {'path': str(intent_file), 'sha256': _sha(intent_bytes)},
        'seed_plan': {'path': str(seed_plan_path), 'sha256': _sha(seed_plan_bytes)},
        'seed_video_evidence': {'path': str(seed_evidence_path), 'sha256': _sha(seed_evidence_bytes)},
        'selected_seed': {'persistent_id': pid, 'video_id': selected_video_id},
        'radio_request': {'endpoint': 'get_watch_playlist',
                          'arguments': arguments,
                          'observed_at': request['observed_at'],
                          'cache_hit': False},
        'raw_radio_cache': {'path': str(cache_file), 'sha256': _sha(cache_bytes),
                            'response_track_count': len(raw_rows)},
        'radio_observation_count': len(replayed),
        'strict_unique_target_video_count': len(target_by_video),
        'outgoing_target_pids': target_pids,
        'strict_radio_observation_positions': eligible_positions,
        'outgoing_recommendation_edges': [
            {'source_pid': pid, 'target_pid': target,
             'video_ids': [video for video, value in target_by_video.items()
                           if value['persistent_id'] == target],
             'source_relation': 'authenticated_watch_radio'}
            for target in target_pids
        ],
        'video_dispositions': dispositions,
        'related_endpoint_used_for_eligibility': False,
        'recording_identity_verified': False,
    }


def create_radio_snapshot_proof(*, track_snapshot_path, identity_map_path,
                                snapshot_path, collector_summary_path,
                                collector_seed_manifest_path, batch_intent_path,
                                raw_radio_cache_path, output_path,
                                sealed_at_utc=None):
    """Create a new immutable proof file after independently replaying inputs."""
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError('Radio proof output exists; choose a new path')
    if sealed_at_utc is None:
        sealed_at_utc = datetime.now(timezone.utc).isoformat()
    proof = _proof_from_inputs(
        track_snapshot_path=track_snapshot_path, identity_map_path=identity_map_path,
        snapshot_path=snapshot_path, collector_summary_path=collector_summary_path,
        collector_seed_manifest_path=collector_seed_manifest_path,
        batch_intent_path=batch_intent_path,
        raw_radio_cache_path=raw_radio_cache_path, sealed_at_utc=sealed_at_utc)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return proof


def verify_radio_snapshot_proof(proof_path):
    """Recompute the full radio-only proof from its hash-bound source files."""
    proof_file, proof_bytes, proof = _read(proof_path)
    if (type(proof.get('schema_version')) is not int or proof.get('schema_version') != 1
            or proof.get('producer') != PRODUCER
            or proof.get('policy') != POLICY):
        raise ValueError('Unsupported authenticated radio proof schema or policy')
    recomputed = _proof_from_inputs(
        track_snapshot_path=proof.get('track_snapshot', {}).get('path'),
        identity_map_path=proof.get('identity_map', {}).get('path'),
        snapshot_path=proof.get('collector_snapshot', {}).get('path'),
        collector_summary_path=proof.get('collector_summary', {}).get('path'),
        collector_seed_manifest_path=proof.get('collector_seed_manifest', {}).get('path'),
        batch_intent_path=proof.get('batch_intent', {}).get('path'),
        raw_radio_cache_path=proof.get('raw_radio_cache', {}).get('path'),
        sealed_at_utc=proof.get('sealed_at_utc'))
    if proof != recomputed:
        raise ValueError('Radio-only proof differs from full raw response and identity-map replay')
    return {'verified': True, 'proof_path': str(proof_file), 'proof_sha256': _sha(proof_bytes),
            'persistent_id': proof['selected_seed']['persistent_id'],
            'outgoing_target_count': len(proof['outgoing_target_pids']),
            'outgoing_edge_count': len(proof['outgoing_recommendation_edges']),
            'raw_radio_cache_sha256': proof['raw_radio_cache']['sha256'],
            'collector_snapshot_sha256': proof['collector_snapshot']['sha256'],
            'collector_summary_sha256': proof['collector_summary']['sha256']}


def radio_only_snapshot_from_proof(proof_path):
    """Return an authenticated radio-only input after complete proof replay."""
    validation = verify_radio_snapshot_proof(proof_path)
    proof_file, proof_bytes, proof = _read(proof_path)
    _, _, original = _read(proof['collector_snapshot']['path'])
    eligible_positions = set(proof['strict_radio_observation_positions'])
    observations = [row for row in original.get('observations', [])
                    if row.get('relation') == 'radio'
                    and (row.get('is_seed') or row.get('position') in eligible_positions)]
    tracks, library_hash, _ = load_library(
        track_snapshot=Path(proof['track_snapshot']['path']))
    matcher = IdentityMap(json.loads(Path(proof['identity_map']['path']).read_bytes()),
                          tracks, library_hash)
    graph = candidate_graph({'library_sha256': library_hash,
                             'selected_video_id': proof['selected_seed']['video_id'],
                             'observations': observations}, matcher, library_hash)
    if (graph['root_pid'] != proof['selected_seed']['persistent_id']
            or graph['ordered_target_pids'] != proof['outgoing_target_pids']):
        raise ValueError('Sanitized radio-only snapshot does not yield the proved target graph')
    return {
        'schema_version': 1,
        'origin': POLICY,
        'account_authentication_verified': True,
        'library_sha256': proof['library_sha256'],
        'identity_map_sha256': proof['identity_map']['sha256'],
        'local_seed': original['local_seed'],
        'selected_video_id': proof['selected_seed']['video_id'],
        'requests': [{'endpoint': 'watch_radio',
                      'observed_at': proof['radio_request']['observed_at'],
                      'returned_track_count': proof['raw_radio_cache']['response_track_count']}],
        'observations': observations,
        'radio_only_source_proof': {'path': str(proof_file),
                                    'sha256': validation['proof_sha256']},
        'related_endpoint_used_for_eligibility': False,
        'recording_identity_verified': False,
    }


def write_radio_only_snapshot_from_proof(proof_path, output_path):
    """Write a sanitized radio-only snapshot without modifying its source."""
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError('Radio-only snapshot output exists; choose a new path')
    snapshot = radio_only_snapshot_from_proof(proof_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {'path': str(output), 'sha256': _sha(output.read_bytes()),
            'radio_only_source_proof': snapshot['radio_only_source_proof']}
