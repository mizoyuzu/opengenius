"""Library-scoped, evidence-backed context claims for personal music tracks.

The sidecar stores context claims separately from Library metadata. It does not
infer relationships or recording identity and never contacts the network.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

from music_identity_map import load_library, version_facets


SCHEMA_VERSION = 1
MEDIUMS = frozenset({'anime', 'game', 'visual_novel', 'general_music', 'drama', 'film', 'unknown'})
ROLES = frozenset({'opening', 'ending', 'insert', 'character', 'background', 'theme', 'unknown'})
KINDS = frozenset({'vocal', 'off_vocal', 'instrumental', 'karaoke', 'background_music', 'soundtrack', 'unknown'})
STATUSES = frozenset({'confirmed', 'provisional'})
LOCAL_FIELDS = frozenset({'title', 'artist', 'album'})
PID_RE = re.compile(r'[0-9A-F]{16}')
SHA256_RE = re.compile(r'[0-9a-f]{64}')


def build_sidecar(tracks, library_hash):
    """Create an empty claims sidecar covering every Library PID."""
    return {
        'schema_version': SCHEMA_VERSION,
        'library_sha256': library_hash,
        'tracks': [{'persistent_id': track['persistent_id'], 'claims': []} for track in tracks],
    }


def _nonempty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')


def _optional_text(value, label):
    if value is not None and not isinstance(value, str):
        raise ValueError(label + ' must be a string or null')
    if isinstance(value, str) and (not value or value != value.strip()):
        raise ValueError(label + ' must not have surrounding whitespace')


def _validate_source(source):
    if not isinstance(source, dict) or not isinstance(source.get('type'), str):
        raise ValueError('Claim source must have a type')
    source_type = source['type']
    if source_type == 'local_metadata':
        if set(source) != {'type', 'fields'}:
            raise ValueError('local_metadata source needs only type and fields')
        fields = source['fields']
        if (not isinstance(fields, list) or not fields or
                any(not isinstance(field, str) or field not in LOCAL_FIELDS for field in fields) or
                len(fields) != len(set(fields))):
            raise ValueError('local_metadata fields must be unique title/artist/album names')
    elif source_type == 'user':
        if set(source) - {'type', 'reference'}:
            raise ValueError('Unknown user source fields')
        if 'reference' in source:
            _nonempty_string(source['reference'], 'user source reference')
    elif source_type == 'web':
        if set(source) - {'type', 'url', 'title', 'evidence'} or not {'url', 'title'} <= set(source):
            raise ValueError('web source needs URL and page title, with optional evidence')
        _nonempty_string(source['url'], 'web source URL')
        _nonempty_string(source['title'], 'web source title')
        parsed = urlsplit(source['url'])
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                any(ord(char) < 32 or ord(char) == 127 for char in source['url'])):
            raise ValueError('web source URL must be an HTTP(S) URL without embedded credentials')
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError('web source URL has an invalid port') from exc
        sensitive_query_keys = {'access_token', 'api_key', 'apikey', 'auth', 'authorization',
                                'key', 'password', 'secret', 'signature', 'token'}
        if any(key.casefold() in sensitive_query_keys for key, _ in parse_qsl(parsed.query)):
            raise ValueError('web source URL must not contain credential query parameters')
        if 'evidence' in source:
            evidence = source['evidence']
            _nonempty_string(evidence, 'web source evidence')
            if len(evidence) > 500:
                raise ValueError('web source evidence must be a short summary of at most 500 characters')
    else:
        raise ValueError('Unknown claim source type')


def _validate_claim(claim):
    required = {'work_title', 'franchise', 'medium', 'role', 'kind', 'notes', 'status', 'source'}
    if not isinstance(claim, dict) or set(claim) != required:
        raise ValueError('Claim fields do not match schema version 1')
    _optional_text(claim['work_title'], 'work_title')
    _optional_text(claim['franchise'], 'franchise')
    for field, allowed in (('medium', MEDIUMS), ('role', ROLES), ('kind', KINDS), ('status', STATUSES)):
        if not isinstance(claim[field], str) or claim[field] not in allowed:
            raise ValueError('Invalid claim ' + field)
    if not isinstance(claim['notes'], str):
        raise ValueError('Claim notes must be a string')
    _validate_source(claim['source'])


def validate_sidecar(document, tracks, library_hash):
    """Validate sidecar structure, Library binding, PIDs, claims, and sources."""
    if not isinstance(document, dict) or set(document) != {'schema_version', 'library_sha256', 'tracks'}:
        raise ValueError('Context sidecar fields do not match schema version 1')
    if type(document.get('schema_version')) is not int or document['schema_version'] != SCHEMA_VERSION:
        raise ValueError('Unsupported context sidecar schema version')
    digest = document.get('library_sha256')
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or digest != library_hash:
        raise ValueError('Context sidecar does not match this Library SHA-256')
    known_pids = {track.get('persistent_id') for track in tracks}
    if not isinstance(document.get('tracks'), list):
        raise ValueError('Context sidecar tracks must be a list')
    seen = set()
    for row in document['tracks']:
        if not isinstance(row, dict) or set(row) != {'persistent_id', 'claims'}:
            raise ValueError('Context sidecar track must contain persistent_id and claims')
        pid = row.get('persistent_id')
        if not isinstance(pid, str) or not PID_RE.fullmatch(pid) or pid not in known_pids or pid in seen:
            raise ValueError('Unknown, invalid, or duplicate context persistent ID')
        seen.add(pid)
        claims = row.get('claims')
        if not isinstance(claims, list):
            raise ValueError('Track claims must be a list')
        for claim in claims:
            _validate_claim(claim)
    return document


def build_context_report(tracks, library_hash, sidecar=None, sidecar_sha256=None,
                         input_hashes=None, script_sha256=None):
    """Build a full per-track context report without inferring missing claims."""
    by_pid = {}
    if sidecar is not None:
        validate_sidecar(sidecar, tracks, library_hash)
        by_pid = {row['persistent_id']: row['claims'] for row in sidecar['tracks']}

    rows = []
    medium_counts, role_counts, kind_counts = Counter(), Counter(), Counter()
    status_counts, source_counts = Counter(), Counter()
    tracks_with_claims = 0
    tracks_with_work_title = 0
    for track in tracks:
        claims = by_pid.get(track['persistent_id'], [])
        if claims:
            tracks_with_claims += 1
        if any(claim['work_title'] for claim in claims):
            tracks_with_work_title += 1
        for claim in claims:
            medium_counts[claim['medium']] += 1
            role_counts[claim['role']] += 1
            kind_counts[claim['kind']] += 1
            status_counts[claim['status']] += 1
            source_counts[claim['source']['type']] += 1
        rows.append({
            'persistent_id': track['persistent_id'],
            'metadata': {field: track.get(field) for field in ('title', 'artist', 'album', 'duration_ms')},
            'version_facets': version_facets(track.get('title') or ''),
            'claims': copy.deepcopy(claims),
        })

    claim_count = sum(len(row['claims']) for row in rows)
    return {
        'schema_version': SCHEMA_VERSION,
        'library_sha256': library_hash,
        'identity_status': 'unverified',
        'recording_identity_claims': 0,
        'network_requests': 0,
        'sidecar_sha256': sidecar_sha256,
        'input_hashes': input_hashes or {},
        'script_sha256': script_sha256,
        'summary': {
            'track_count': len(tracks),
            'tracks_with_claims': tracks_with_claims,
            'tracks_with_work_title': tracks_with_work_title,
            'claim_count': claim_count,
            'confirmed_claim_count': status_counts['confirmed'],
            'provisional_claim_count': status_counts['provisional'],
            'medium_claim_counts': dict(sorted(medium_counts.items())),
            'role_claim_counts': dict(sorted(role_counts.items())),
            'kind_claim_counts': dict(sorted(kind_counts.items())),
            'source_claim_counts': dict(sorted(source_counts.items())),
        },
        'tracks': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True,
                        help='Saved track metadata; does not read the original Library')
    parser.add_argument('--claims', type=Path, help='Optional Library-scoped context claims sidecar')
    parser.add_argument('--output', type=Path, required=True, help='New JSON report path')
    args = parser.parse_args()
    output = args.output.resolve()
    inputs = [args.track_snapshot.resolve()]
    if args.claims:
        inputs.append(args.claims.resolve())
    if output.exists() or output in inputs:
        parser.error('output must be a new file separate from all inputs')

    tracks, library_hash, provenance = load_library(track_snapshot=args.track_snapshot)
    track_bytes = args.track_snapshot.read_bytes()
    sidecar = None
    sidecar_hash = None
    input_hashes = {'track_snapshot_sha256': hashlib.sha256(track_bytes).hexdigest()}
    if args.claims:
        sidecar_bytes = args.claims.read_bytes()
        sidecar = json.loads(sidecar_bytes)
        sidecar_hash = hashlib.sha256(sidecar_bytes).hexdigest()
        input_hashes['claims_sidecar_sha256'] = sidecar_hash
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = build_context_report(tracks, library_hash, sidecar, sidecar_hash,
                                  input_hashes=input_hashes, script_sha256=script_hash)
    report['library_input'] = provenance
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), **report['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
