"""Apply root-reviewed, cache-bound title spelling aliases to a local identity map.

Title aliases only aid candidate generation. This workflow never verifies a recording.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from music_identity_map import (
    IdentityMap, apply_title_review, load_library, normalize, version_signature,
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _resolve(path):
    value = Path(path)
    if value.is_absolute():
        return value.resolve()
    return (Path(__file__).resolve().parents[1] / value).resolve()


def _load(path):
    resolved = Path(path).resolve()
    data = resolved.read_bytes()
    return resolved, data, json.loads(data)


def _duration_seconds(track):
    value = track.get('duration_seconds')
    if value is not None:
        return value
    length = track.get('length')
    if not isinstance(length, str):
        return None
    parts = length.split(':')
    if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
        return None
    result = 0
    for part in parts:
        result = result * 60 + int(part)
    return result


def apply_reviewed_title_aliases(*, track_snapshot, identity_map_path,
                                 proposals_path, review_decisions_path,
                                 output_path, receipt_path, _write_outputs=True):
    snapshot_path = Path(track_snapshot).resolve()
    map_path, map_bytes, identity_map = _load(identity_map_path)
    proposal_path, proposal_bytes, proposals = _load(proposals_path)
    review_path, review_bytes, review = _load(review_decisions_path)
    output_path, receipt_path = Path(output_path).resolve(), Path(receipt_path).resolve()
    if ((_write_outputs and (output_path.exists() or receipt_path.exists()))
            or output_path == receipt_path):
        raise FileExistsError('Title map output and receipt must be new, distinct paths')
    if output_path in (snapshot_path, map_path, proposal_path, review_path) or receipt_path in (
            snapshot_path, map_path, proposal_path, review_path):
        raise ValueError('Output paths cannot replace inputs')

    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    map_hash, proposal_hash, decision_hash = _sha(map_bytes), _sha(proposal_bytes), _sha(review_bytes)
    report_source = proposals.get('source_report') or {}
    report_path = _resolve(report_source.get('path') or '')
    if not report_path.is_file() or _sha(report_path.read_bytes()) != report_source.get('sha256'):
        raise ValueError('Title proposal source report is missing or changed')
    report = json.loads(report_path.read_bytes())
    if (report_source.get('library_sha256') != library_hash
            or report_source.get('track_snapshot_sha256') != _sha(snapshot_path.read_bytes())
            or report_source.get('identity_map_sha256') != map_hash
            or report.get('library', {}).get('identity_map_sha256') != map_hash):
        raise ValueError('Title proposal audit targets a different Library or identity map')
    if (review.get('schema_version') != 1
            or review.get('library_sha256') != library_hash
            or review.get('identity_map_sha256') != map_hash
            or review.get('proposal_sha256') != proposal_hash
            or review.get('source_report_sha256') != report_source.get('sha256')
            or review.get('approval', {}).get('reviewed_by') != 'root'
            or review.get('approval', {}).get('recording_identity_verified') is not False
            or review.get('approval', {}).get('new_recording_edges_authorized') is not False):
        raise ValueError('Title review does not authorize this exact map/proposal/report')

    audit_inputs = report.get('input_files')
    if not isinstance(audit_inputs, list) or not audit_inputs:
        raise ValueError('Title candidate report lacks cache-input receipts')
    input_hashes = {}
    for source in audit_inputs:
        if not isinstance(source, dict) or not isinstance(source.get('path'), str):
            raise ValueError('Malformed title candidate cache-input receipt')
        path = _resolve(source['path'])
        if not path.is_file() or _sha(path.read_bytes()) != source.get('sha256'):
            raise ValueError('A cached observation used by title review changed')
        input_hashes[str(path)] = source['sha256']

    matcher = IdentityMap(identity_map, tracks, library_hash)
    proposal_rows = proposals.get('proposals')
    review_rows = review.get('decisions')
    audited_rows = report.get('candidates')
    if (not isinstance(proposal_rows, list) or not proposal_rows
            or not isinstance(review_rows, list) or len(review_rows) != len(proposal_rows)
            or not isinstance(audited_rows, list) or len(audited_rows) != len(proposal_rows)):
        raise ValueError('Title decisions and cached candidate audit are incomplete')
    by_pid = {track['persistent_id']: track for track in tracks}
    candidate_lookup = {(row.get('persistent_id'), normalize(row.get('remote_title_alias') or '')): row
                        for row in audited_rows}
    proposal_keys, review_keys, accepted = set(), set(), []
    alias_owner = {}
    resolved_examples = 0
    for proposal in proposal_rows:
        pid, local_title, alias = (proposal.get('persistent_id'), proposal.get('local_title'),
                                   proposal.get('alias'))
        key = (pid, normalize(alias or ''))
        if key in proposal_keys or key not in candidate_lookup:
            raise ValueError('Duplicate proposal or missing exact candidate audit row')
        proposal_keys.add(key)
        local = by_pid.get(pid)
        if local is None or local.get('title') != local_title:
            raise ValueError('Title alias must target an exact existing local PID/title')
        if key[1] in alias_owner:
            raise ValueError('A title alias overlaps another approved local PID')
        alias_owner[key[1]] = pid
        decision = next((row for row in review_rows
                         if row.get('persistent_id') == pid
                         and normalize(row.get('alias') or '') == key[1]), None)
        if decision is None or normalize(decision.get('original_title') or '') != normalize(local_title or ''):
            raise ValueError('Root title review does not match the complete proposal row')
        review_key = (pid, key[1])
        if review_key in review_keys:
            raise ValueError('Duplicate title review decision')
        review_keys.add(review_key)
        if decision.get('reason') != proposal.get('reason'):
            raise ValueError('Title review reason differs from the proposal')
        audited = candidate_lookup[key]
        if (audited.get('local_title') != local_title
                or audited.get('remote_title_alias') != alias
                or audited.get('unique_video_pid_pairs') != audited.get('unique_video_ids')
                or audited.get('exact_album_observations') != audited.get('cached_observations')):
            raise ValueError('Title candidate audit summary is ambiguous or inconsistent')
        prefix, separator, suffix = alias.rpartition(' - ')
        if (not separator or not suffix or normalize(prefix) != normalize(local_title)
                or not version_signature(alias) == version_signature(local_title)
                or not any('\u3040' <= char <= '\u9fff' for char in prefix)
                or not any(('A' <= char <= 'Z') or ('a' <= char <= 'z') for char in suffix)):
            raise ValueError('Title alias does not preserve exact Japanese prefix and version facets')
        # A title alias may not make an existing active local title/alias ambiguous.
        existing_indices = matcher._active_title_indices.get(key[1], set())
        if existing_indices:
            raise ValueError('Reviewed title alias collides with an active local title or alias')
        examples = audited.get('examples')
        if not isinstance(examples, list) or not examples:
            raise ValueError('Title candidate has no exact cached examples')
        source_paths = set(audited.get('source_observation_files') or [])
        for example in examples:
            source_path = _resolve(example.get('observation_file') or '')
            if str(source_path) not in input_hashes or str(source_path) not in {
                    str(_resolve(path)) for path in source_paths}:
                raise ValueError('Title example is not bound to a hashed cache input')
            cached = json.loads(source_path.read_bytes())
            row = next((row for row in cached.get('observations', [])
                        if (row.get('track') or {}).get('video_id') == example.get('video_id')
                        and (row.get('track') or {}).get('title') == alias), None)
            if row is None:
                raise ValueError('Title example is absent from its bound cached response')
            remote = row['track']
            duration = _duration_seconds(remote)
            if (normalize((remote.get('album') or {}).get('name') or '') != normalize(local.get('album') or '')
                    or version_signature(remote.get('title') or '') != version_signature(local_title)
                    or duration is None
                    or local.get('duration_ms') is None
                    or abs(local['duration_ms'] / 1000 - duration) > 3):
                raise ValueError('Cached title evidence fails album, version, or duration gates')
            # Force only the title spelling to the local Japanese title to isolate credit/PID evidence.
            identity_hits = matcher.match({**remote, 'title': local_title})
            if (len(identity_hits) != 1 or identity_hits[0]['persistent_id'] != pid
                    or identity_hits[0]['artist_credit_match'] != 'complete'
                    or identity_hits[0]['duration_difference_seconds'] > 3
                    or identity_hits[0]['album_match'] is not True):
                raise ValueError('Complete credit does not resolve uniquely to the reviewed local track')
            resolved_examples += 1
        accepted.append({'persistent_id': pid, 'original_title': local_title,
                         'alias': alias, 'reason': proposal['reason'],
                         'evidence': {'proposal_sha256': proposal_hash,
                                      'source_report_sha256': report_source['sha256'],
                                      'cached_observation_files': sorted(source_paths)}})
    if proposal_keys != review_keys:
        raise ValueError('Title review includes missing or unrequested aliases')

    result = apply_title_review(identity_map, {'schema_version': 1,
                                               'library_sha256': library_hash,
                                               'decisions': accepted}, tracks, library_hash)
    # The only enabled title values beyond the input map are the exact root-reviewed aliases.
    new_matcher = IdentityMap(result, tracks, library_hash)
    for proposal in proposal_rows:
        pid, alias = proposal['persistent_id'], proposal['alias']
        if new_matcher._active_title_indices.get(normalize(alias)) != {
                next(index for index, track in enumerate(tracks) if track['persistent_id'] == pid)}:
            raise ValueError('Activated title alias is not uniquely bound to its reviewed PID')

    output_bytes = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    output_hash = _sha(output_bytes)
    if _write_outputs:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('xb') as handle:
            handle.write(output_bytes)
    receipt = {'schema_version': 1, 'operation': 'apply_reviewed_exact_title_spelling_aliases',
               'library_sha256': library_hash, 'input_identity_map_path': str(map_path),
               'input_identity_map_sha256': map_hash, 'output_identity_map_path': str(output_path),
               'output_identity_map_sha256': output_hash,
               'track_snapshot_path': str(snapshot_path),
               'track_snapshot_sha256': _sha(snapshot_path.read_bytes()),
               'proposals_path': str(proposal_path), 'proposal_sha256': proposal_hash,
               'source_report_path': str(report_path), 'source_report_sha256': report_source['sha256'],
               'review_decisions_path': str(review_path), 'review_decisions_sha256': decision_hash,
               'activated_title_alias_count': len(accepted),
               'verified_cached_example_count': resolved_examples,
               'local_title_alias_collisions': 0,
               'recording_identity_verified': False, 'recording_edges_added': 0}
    if _write_outputs:
        with receipt_path.open('x', encoding='utf-8') as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    return receipt


def verify_title_alias_transition(*, track_snapshot, input_identity_map_path,
                                  output_identity_map_path, proposals_path,
                                  review_decisions_path, receipt_path):
    """Rebuild and verify an applied title-map transition from its bound inputs."""
    output_path = Path(output_identity_map_path).resolve()
    receipt_file = Path(receipt_path).resolve()
    final_bytes = output_path.read_bytes()
    saved_receipt = json.loads(receipt_file.read_bytes())
    receipt = apply_reviewed_title_aliases(
        track_snapshot=track_snapshot,
        identity_map_path=input_identity_map_path,
        proposals_path=proposals_path,
        review_decisions_path=review_decisions_path,
        output_path=output_path, receipt_path=receipt_file,
        _write_outputs=False)
    if _sha(final_bytes) != receipt['output_identity_map_sha256']:
        raise ValueError('Final identity map is not reproduced by its reviewed title decision')
    # Output location is incidental; all other receipt fields are required to match.
    saved_receipt.pop('output_identity_map_path', None)
    receipt.pop('output_identity_map_path', None)
    if saved_receipt != receipt:
        raise ValueError('Saved title application receipt differs from recomputed review')
    return saved_receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--identity-map', required=True, type=Path)
    parser.add_argument('--proposals', required=True, type=Path)
    parser.add_argument('--review-decisions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = apply_reviewed_title_aliases(
            track_snapshot=args.track_snapshot, identity_map_path=args.identity_map,
            proposals_path=args.proposals, review_decisions_path=args.review_decisions,
            output_path=args.output, receipt_path=args.receipt)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '): ' + str(error) + '\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
