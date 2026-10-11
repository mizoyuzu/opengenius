"""Source-bound proof for authenticated YT Music radio-only recommendations.

This policy requires a real ``get_watch_playlist(radio=True)`` response and
replays that response against its exact Library snapshot and active identity
map. Related-song observations are auxiliary and are intentionally omitted.
The response-cache hash is sealed when this proof is created; it does not
claim that this hash was captured by the original collector process.
"""
from datetime import datetime, timezone
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from music_identity_map import IdentityMap, load_library, version_signature
from probe_ytmusic import observe
from probe_ytmusic_batch import (resolve_seed_search,
                                 summary as collector_result_summary)
from seed_video_evidence import select_seed_video_evidence
from emulate_ytmusic_candidates import candidate_graph
import seed_video_evidence as _seed_video_evidence


PRODUCER = 'opengenius-authenticated-radio-snapshot-proof-v1'
POLICY = 'authenticated-watch-radio-only-v1'
_ACTIVE_VERIFICATION_CACHE = ContextVar('radio_snapshot_proof_verification_cache', default=None)
_CACHE_LIMIT = 256
_SHA_RE = re.compile(r'[a-f0-9]{64}\Z')


@contextmanager
def radio_snapshot_proof_cache_scope():
    """Opt into a bounded cache for one synchronous validation invocation."""
    cache = OrderedDict()
    token = _ACTIVE_VERIFICATION_CACHE.set(cache)
    try:
        yield
    finally:
        _ACTIVE_VERIFICATION_CACHE.reset(token)


def _verified_source_closure(proof):
    """Enumerate exactly the source files replayed by the full verifier.

    Historical receipt-shaped fields are not necessarily live dependencies.
    Typed transitions expose their fully revalidated current closure; catalog
    evidence has explicit source receipts, while cached-search replay scans
    collector directories and tests request-path existence. Those directories
    are sealed by both their contents and membership.
    """
    receipts = {}

    def add(path_value, expected):
        if (not isinstance(path_value, str) or not path_value
                or not isinstance(expected, str) or not _SHA_RE.fullmatch(expected)):
            raise ValueError('Malformed verified radio-proof source receipt')
        path = Path(path_value).resolve()
        key = str(path)
        previous = receipts.get(key)
        if previous is not None and previous != expected:
            raise ValueError('Conflicting verified radio-proof source receipts: ' + key)
        try:
            actual = _sha(path.read_bytes())
        except OSError:
            raise ValueError('A verified radio-proof source is missing: ' + key) from None
        if actual != expected:
            raise ValueError('A verified radio-proof source changed: ' + key)
        receipts[key] = expected

    def add_tree(directory):
        """Hash every file plus membership under a replay-scanned directory."""
        root = Path(directory).resolve()
        if not root.is_dir():
            raise ValueError('A replay-scanned radio-proof directory is missing: ' + str(root))
        entries, files = _directory_tree_snapshot(root)
        for path, digest in files:
            add(path, digest)
        receipts['@directory:' + str(root)] = _directory_tree_digest(entries)

    for key in ('track_snapshot', 'identity_map', 'collector_snapshot',
                'collector_summary', 'collector_seed_manifest', 'batch_intent',
                'seed_plan', 'seed_video_evidence', 'raw_radio_cache'):
        ref = proof.get(key)
        if not isinstance(ref, dict):
            raise ValueError('Radio proof omits a direct source receipt: ' + key)
        add(ref.get('path'), ref.get('sha256'))
    search_binding = proof.get('seed_video_search_binding')
    if search_binding is not None:
        if (not isinstance(search_binding, dict)
                or search_binding.get('kind') != 'authenticated-search-strict-unique-seed-v1'
                or not isinstance(search_binding.get('raw_search_cache'), dict)):
            raise ValueError('Malformed search-derived seed binding receipt')
        search_cache = search_binding['raw_search_cache']
        add(search_cache.get('path'), search_cache.get('sha256'))

    intent_doc = json.loads(Path(proof['batch_intent']['path']).read_bytes())
    for source in intent_doc.get('source_inputs', []):
        if not isinstance(source, dict):
            raise ValueError('Malformed batch intent source receipt')
        add(source.get('path'), source.get('sha256'))

    evidence_doc = json.loads(Path(proof['seed_video_evidence']['path']).read_bytes())
    if evidence_doc.get('producer') == 'opengenius-combined-rebound-seed-evidence-v1':
        rebound_ref = evidence_doc.get('rebound_evidence')
        current_ref = evidence_doc.get('current_radio_source')
        for ref in (rebound_ref, current_ref):
            if not isinstance(ref, dict):
                raise ValueError('Combined hint evidence omits a typed source receipt')
            add(ref.get('path'), ref.get('sha256'))
        rebound_path = Path(rebound_ref['path']).resolve()
        rebound_doc = json.loads(rebound_path.read_bytes())
    elif evidence_doc.get('producer') == 'opengenius-map-transition-rebound-seed-evidence-v1':
        rebound_doc = evidence_doc
    else:
        rebound_doc = None

    if rebound_doc is not None:
        for key in ('track_snapshot', 'catalog_evidence', 'cached_hint_evidence'):
            ref = rebound_doc.get(key)
            if ref is not None:
                if not isinstance(ref, dict):
                    raise ValueError('Malformed rebound source receipt: ' + key)
                add(ref.get('path'), ref.get('sha256'))
        transition = rebound_doc.get('typed_map_transition')
        if not isinstance(transition, dict):
            raise ValueError('Rebound hint evidence omits typed transition inputs')
        for ref in transition.values():
            if not isinstance(ref, dict):
                raise ValueError('Malformed typed transition input receipt')
            add(ref.get('path'), ref.get('sha256'))
        old_hash = transition['old_identity_map']['sha256']
        new_hash = transition['new_identity_map']['sha256']
        library_hash = proof['library_sha256']
        matches = [entry for cache_key, entry
                   in _seed_video_evidence._TYPED_TRANSITION_PROOF_CACHE.items()
                   if cache_key[:3] == (old_hash, new_hash, library_hash)]
        if len(matches) != 1:
            raise ValueError('Full replay did not expose exactly one typed transition closure')
        for ref in matches[0].get('hash_inputs', []):
            add(ref.get('path'), ref.get('sha256'))

        catalog_ref = rebound_doc.get('catalog_evidence')
        if catalog_ref is not None:
            catalog_path = Path(catalog_ref['path']).resolve()
            catalog_doc = json.loads(catalog_path.read_bytes())
            for path, digest in _receipt_refs_scoped(catalog_doc, catalog_path.parent):
                add(str(path), digest)

        cached_ref = rebound_doc.get('cached_hint_evidence')
        if cached_ref is not None:
            cached_doc = json.loads(Path(cached_ref['path']).read_bytes())
            source_runs = cached_doc.get('source_runs')
            if not isinstance(source_runs, list):
                raise ValueError('Cached-hint source runs are not enumerable')
            collector_dirs = set()
            for run in source_runs:
                if not isinstance(run, dict) or not isinstance(run.get('collector_dir'), str):
                    raise ValueError('Malformed cached-hint source run')
                collector_dirs.add(str(Path(run['collector_dir']).resolve()))
            # Revalidation enumerates summary*.json and tests fallback request
            # existence. Seal membership and bytes so additions, removals, and
            # edits invalidate the memo instead of changing discovery results.
            for collector_dir in sorted(collector_dirs):
                add_tree(collector_dir)

    return tuple(sorted(receipts.items()))


def _rehash_cached_closure(receipts):
    for path, expected in receipts:
        if path.startswith('@directory:'):
            root = Path(path[len('@directory:'):])
            if not root.is_dir():
                raise ValueError('A cached radio-proof source directory is missing: ' + str(root))
            entries, _ = _directory_tree_snapshot(root)
            if _directory_tree_digest(entries) != expected:
                raise ValueError('A cached radio-proof source directory changed: ' + str(root))
            continue
        try:
            if _sha(Path(path).read_bytes()) != expected:
                raise ValueError('A cached radio-proof source receipt changed: ' + path)
        except OSError:
            raise ValueError('A cached radio-proof source is missing: ' + path) from None


def _receipt_refs_scoped(document, parent):
    """Read receipt-shaped fields only inside a source document actively replayed."""
    found = []
    def visit(value):
        if isinstance(value, dict):
            pairs = [('path', 'sha256'), ('input_path', 'input_sha256')]
            pairs.extend((key, key[:-4] + 'sha256') for key in value
                         if isinstance(key, str) and key.endswith('_path'))
            for path_key, hash_key in pairs:
                path_value, digest = value.get(path_key), value.get(hash_key)
                if (isinstance(path_value, str) and path_value
                        and isinstance(digest, str) and _SHA_RE.fullmatch(digest)):
                    path = Path(path_value)
                    if not path.is_absolute():
                        path = parent / path
                    found.append((path.resolve(), digest))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(document)
    return found


def _directory_tree_snapshot(root):
    root = Path(root).resolve()
    entries, files = [], []
    for item in sorted(root.rglob('*')):
        if item.is_symlink():
            raise ValueError('Symlinks are not cacheable radio-proof sources: ' + str(item))
        rel = item.relative_to(root).as_posix()
        if item.is_dir():
            entries.append((rel, 'directory', None))
        elif item.is_file():
            digest = _sha(item.read_bytes())
            entries.append((rel, 'file', digest))
            files.append((str(item.resolve()), digest))
        else:
            raise ValueError('Unsupported replay-scanned source entry: ' + str(item))
    return entries, files


def _directory_tree_digest(entries):
    manifest = json.dumps(entries, ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8')
    return _sha(manifest)


def _replay_scanned_directory_state(proof):
    """Snapshot dynamic cached-search trees before/after full source replay."""
    evidence_ref = proof.get('seed_video_evidence') or {}
    evidence_path = Path(evidence_ref.get('path', '')).resolve()
    evidence_bytes = evidence_path.read_bytes()
    if _sha(evidence_bytes) != evidence_ref.get('sha256'):
        raise ValueError('Seed-video evidence changed before proof replay')
    evidence_doc = json.loads(evidence_bytes)
    if evidence_doc.get('producer') == 'opengenius-combined-rebound-seed-evidence-v1':
        rebound_ref = evidence_doc.get('rebound_evidence') or {}
        rebound_path = Path(rebound_ref.get('path', '')).resolve()
        rebound_bytes = rebound_path.read_bytes()
        if _sha(rebound_bytes) != rebound_ref.get('sha256'):
            raise ValueError('Rebound seed evidence changed before proof replay')
        rebound_doc = json.loads(rebound_bytes)
    elif evidence_doc.get('producer') == 'opengenius-map-transition-rebound-seed-evidence-v1':
        rebound_doc = evidence_doc
    else:
        return ()
    cached_ref = rebound_doc.get('cached_hint_evidence')
    if cached_ref is None:
        return ()
    cached_path = Path(cached_ref.get('path', '')).resolve()
    cached_bytes = cached_path.read_bytes()
    if _sha(cached_bytes) != cached_ref.get('sha256'):
        raise ValueError('Cached-hint evidence changed before proof replay')
    source_runs = json.loads(cached_bytes).get('source_runs')
    if not isinstance(source_runs, list):
        raise ValueError('Cached-hint source runs are not enumerable')
    state = []
    for directory in sorted({str(Path(row['collector_dir']).resolve())
                             for row in source_runs
                             if isinstance(row, dict)
                             and isinstance(row.get('collector_dir'), str)}):
        root = Path(directory)
        entries, _ = _directory_tree_snapshot(root)
        state.append((directory, _directory_tree_digest(entries)))
    return tuple(state)


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


def _request_cache_path(collector_snapshot_path, endpoint, arguments):
    """Locate the content-addressed raw request receipt for one exact call."""
    encoded = json.dumps([endpoint, arguments], sort_keys=True,
                         ensure_ascii=False).encode('utf-8')
    digest = _sha(encoded)
    path = Path(collector_snapshot_path).resolve().parent / 'requests' / (digest + '.json')
    if not path.is_file():
        raise ValueError('Raw search response cache is missing')
    return path.resolve()


def _search_derived_seed_binding(*, snapshot, snapshot_path, local_seed,
                                 selected_video_id, tracks, matcher):
    """Validate a seed ID derived from this snapshot's exact live search.

    The ordinary seed-video sidecar remains preferred. This fallback is only
    for a recorded live search request with its content-addressed response
    cache present; it requires the existing resolver to choose the radio seed
    video and exactly one candidate to survive every strict metadata gate.
    """
    evidence = [row for row in snapshot.get('request_evidence', [])
                if isinstance(row, dict) and row.get('endpoint') == 'search']
    if len(evidence) != 1:
        raise ValueError('Search-derived seed binding requires exactly one search request')
    request = evidence[0]
    arguments = request.get('arguments')
    expected_arguments = {
        'query': (local_seed.get('title') or '') + ' ' + (local_seed.get('artist') or ''),
        'filter': 'songs', 'limit': 5,
    }
    if (arguments != expected_arguments or request.get('cache_hit') is not False
            or not isinstance(request.get('observed_at'), str)
            or not request.get('observed_at')):
        raise ValueError('Search-derived seed request is not the exact live seed query')
    cache_path = _request_cache_path(snapshot_path, 'search', arguments)
    cache_bytes = cache_path.read_bytes()
    cache = json.loads(cache_bytes)
    if (cache.get('endpoint') != 'search' or cache.get('arguments') != arguments
            or cache.get('observed_at') != request.get('observed_at')
            or not isinstance(cache.get('response'), list)):
        raise ValueError('Raw search cache does not bind the exact live seed request')

    resolved_video, candidate_video_count, resolver_status = resolve_seed_search(
        cache['response'], local_seed, tracks, matcher)
    if resolved_video != selected_video_id:
        raise ValueError('Replayed seed search does not resolve to the radio seed video')
    pid = local_seed.get('persistent_id')
    strict_by_video = {}
    selected_video_strict_rows = []
    for row in cache['response']:
        video_id = row.get('videoId') if isinstance(row, dict) else None
        if not isinstance(video_id, str):
            continue
        strict = [candidate for candidate in matcher.match(row)
                  if _strict_candidate(candidate, row.get('title'))]
        if video_id == selected_video_id:
            selected_video_strict_rows.append(
                {candidate.get('persistent_id') for candidate in strict})
        if strict:
            strict_by_video.setdefault(video_id, set()).update(
                candidate.get('persistent_id') for candidate in strict)
    strict_video_ids = sorted(video for video, pids in strict_by_video.items()
                              if pid in pids)
    selected_video_strict = strict_by_video.get(selected_video_id, set())
    if (strict_video_ids != [selected_video_id]
            or selected_video_strict != {pid}
            or not selected_video_strict_rows
            or any(row_pids != {pid} for row_pids in selected_video_strict_rows)):
        raise ValueError('Seed search does not have one strict unique video for its local PID')
    return {
        'kind': 'authenticated-search-strict-unique-seed-v1',
        'request': {'endpoint': 'search', 'arguments': arguments,
                    'observed_at': request['observed_at'], 'cache_hit': False},
        'raw_search_cache': {'path': str(cache_path), 'sha256': _sha(cache_bytes),
                             'response_result_count': len(cache['response'])},
        'resolver': {'video_id': resolved_video,
                     'candidate_video_count': candidate_video_count,
                     'status': resolver_status},
        'strict_unique_video_ids': strict_video_ids,
        'persistent_id': pid,
    }


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


def _search_bound_seed_graph_matcher(matcher, selected_video_id, persistent_id):
    """Use the typed strict search binding only to disambiguate its seed row."""
    class StrictSearchBoundSeedMatcher:
        tracks = matcher.tracks

        def match(self, remote, include_proposed=False):
            candidates = matcher.match(remote, include_proposed=include_proposed)
            if remote.get('video_id') != selected_video_id:
                return candidates
            return [candidate for candidate in candidates
                    if candidate.get('persistent_id') == persistent_id
                    and _strict_candidate(candidate, remote.get('title'))]

    return StrictSearchBoundSeedMatcher()


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
    collection_mode = config.get('collector_collection_mode', 'related-v1')
    if collection_mode not in ('related-v1', 'radio-only-v1'):
        raise ValueError('Batch intent has an unsupported collector collection mode')
    declared_modes = (summary.get('collector_collection_mode', 'related-v1'),
                      manifest.get('collector_collection_mode', 'related-v1'),
                      snapshot.get('collector_collection_mode', 'related-v1'))
    if any(mode != collection_mode for mode in declared_modes):
        raise ValueError('Collector summary, manifest, snapshot, and intent modes differ')
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
    if collection_mode == 'radio-only-v1':
        evidence_rows = snapshot.get('request_evidence')
        request_rows = snapshot.get('requests')
        if (not isinstance(evidence_rows, list) or not isinstance(request_rows, list)
                or any(not isinstance(row, dict) for row in evidence_rows + request_rows)):
            raise ValueError('Radio-only collector request evidence is malformed')
        logical_endpoints = {row.get('endpoint') for row in evidence_rows}
        request_endpoints = [row.get('endpoint') for row in request_rows]
        if (not logical_endpoints.issubset({'search', 'get_watch_playlist'})
                or any(row.get('endpoint') == 'get_watch_playlist'
                       and row.get('arguments', {}).get('radio') is not True
                       for row in snapshot.get('request_evidence', []) if isinstance(row, dict))
                or request_endpoints != ['watch_radio']
                or snapshot.get('related_status') != 'not_requested'):
            raise ValueError('Radio-only collector snapshot contains auxiliary watch/related requests')
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
    seed_video_search_binding = None
    if isinstance(selected_seed_evidence, dict):
        if selected_seed_evidence.get('video_id') != selected_video_id:
            raise ValueError('Radio call video ID differs from its validated seed-video sidecar')
    else:
        seed_video_search_binding = _search_derived_seed_binding(
            snapshot=snapshot, snapshot_path=snapshot_file, local_seed=local_seed,
            selected_video_id=selected_video_id, tracks=tracks, matcher=matcher)
    replayed = observe(raw_rows, 'radio', request['observed_at'], tracks,
                       selected_video_id, candidate_matcher=matcher.match)
    saved_radio = [row for row in snapshot.get('observations', [])
                   if isinstance(row, dict) and row.get('relation') == 'radio']
    if replayed != saved_radio:
        raise ValueError('Raw radio response does not reproduce the saved radio observations')

    seed_rows = [row for row in replayed if row.get('is_seed')]
    seed_candidates = matcher.match(seed_rows[0]['track']) if len(seed_rows) == 1 else []
    if seed_video_search_binding is not None:
        strict_seed_candidates = [candidate for candidate in seed_candidates
                                  if _strict_candidate(
                                      candidate, seed_rows[0]['track'].get('title'))]
        valid_seed = (len(strict_seed_candidates) == 1
                      and strict_seed_candidates[0].get('persistent_id') == pid)
    else:
        valid_seed = (len(seed_candidates) == 1
                      and seed_candidates[0].get('persistent_id') == pid
                      and _strict_candidate(
                          seed_candidates[0], seed_rows[0]['track'].get('title')))
    if not valid_seed:
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
    graph_matcher = (matcher if seed_video_search_binding is None else
                     _search_bound_seed_graph_matcher(matcher, selected_video_id, pid))
    radio_graph = candidate_graph({
        'library_sha256': library_hash,
        'selected_video_id': selected_video_id,
        'observations': strict_observations,
    }, graph_matcher, library_hash)
    if (radio_graph.get('root_pid') != pid
            or radio_graph.get('ordered_target_pids') != target_pids):
        raise ValueError('Strict radio candidate graph differs from the versioned proof targets')

    try:
        parsed_seal = datetime.fromisoformat(sealed_at_utc.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        raise ValueError('Raw cache sealing time is not an ISO timestamp')
    if parsed_seal.tzinfo is None or parsed_seal.utcoffset() is None or parsed_seal.utcoffset().total_seconds() != 0:
        raise ValueError('Raw cache sealing time must be UTC')

    proof = {
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
    if seed_video_search_binding is not None:
        proof['seed_video_search_binding'] = seed_video_search_binding
    return proof


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
    cache = _ACTIVE_VERIFICATION_CACHE.get()
    cache_key = (str(proof_file), _sha(proof_bytes),
                 (proof.get('identity_map') or {}).get('sha256'),
                 proof.get('library_sha256'))
    if cache is not None and cache_key in cache:
        cached = cache[cache_key]
        _rehash_cached_closure(cached['source_closure'])
        cache.move_to_end(cache_key)
        return deepcopy(cached['result'])
    if (type(proof.get('schema_version')) is not int or proof.get('schema_version') != 1
            or proof.get('producer') != PRODUCER
            or proof.get('policy') != POLICY):
        raise ValueError('Unsupported authenticated radio proof schema or policy')
    directory_state_before = (_replay_scanned_directory_state(proof)
                              if cache is not None else None)
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
    directory_state_after = (_replay_scanned_directory_state(proof)
                             if cache is not None else None)
    if directory_state_before != directory_state_after:
        raise ValueError('A cached-search source directory changed during radio proof replay')
    result = {'verified': True, 'proof_path': str(proof_file), 'proof_sha256': _sha(proof_bytes),
              'persistent_id': proof['selected_seed']['persistent_id'],
              'outgoing_target_count': len(proof['outgoing_target_pids']),
              'outgoing_edge_count': len(proof['outgoing_recommendation_edges']),
              'raw_radio_cache_sha256': proof['raw_radio_cache']['sha256'],
              'collector_snapshot_sha256': proof['collector_snapshot']['sha256'],
              'collector_summary_sha256': proof['collector_summary']['sha256']}
    if cache is not None:
        source_closure = _verified_source_closure(proof)
        closure_directory_state = tuple(sorted(
            (path[len('@directory:'):], digest)
            for path, digest in source_closure
            if path.startswith('@directory:')))
        if directory_state_before != closure_directory_state:
            raise ValueError('A cached-search source directory changed while sealing proof memo')
        cache[cache_key] = {'source_closure': source_closure,
                            'result': deepcopy(result)}
        cache.move_to_end(cache_key)
        if len(cache) > _CACHE_LIMIT:
            cache.popitem(last=False)
    return result


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
    graph_matcher = matcher
    if 'seed_video_search_binding' in proof:
        graph_matcher = _search_bound_seed_graph_matcher(
            matcher, proof['selected_seed']['video_id'],
            proof['selected_seed']['persistent_id'])
    graph = candidate_graph({'library_sha256': library_hash,
                             'selected_video_id': proof['selected_seed']['video_id'],
                             'observations': observations}, graph_matcher, library_hash)
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
