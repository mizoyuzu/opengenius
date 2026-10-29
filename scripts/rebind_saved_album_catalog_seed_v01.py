"""Re-resolve saved public album metadata against the pinned current Library map.

This builds ordinary typed album-catalog seed evidence. It claims no recording
identity, source proof, outgoing root, or recommendation edge.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
sys.path.insert(0, str(REPO / 'scripts'))

from discover_album_catalog_seeds import build_album_catalog_evidence, build_album_plan
from music_identity_map import IdentityMap, load_library
from search_unindexed_album_sources import build_album_search_evidence, build_album_search_plan
from seed_video_evidence import (
    build_seed_video_evidence, combine_seed_video_evidence,
    select_seed_video_evidence, validate_album_catalog_evidence,
)

SNAPSHOT = REPO / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
IDENTITY_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
V16_STATE = REPO / 'data/ytmusic/typed-radio-source-rematch-v16-accepted-20261013-v01/artifact/state.json'
V16_GRAPH = REPO / 'data/ytmusic/typed-radio-source-rematch-v16-accepted-20261013-v01/artifact/accepted-graphs.json'
PILOT_PROGRESS = REPO / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
OLD_SEARCH_EVIDENCE = REPO / 'data/new-library-rebased-20261009-v03/live-album-search-20261009/album-search-evidence.json'
OLD_CATALOG_EVIDENCE = REPO / 'data/new-library-rebased-20261009-v03/live-album-catalog-20261009/album-catalog-evidence.json'
SEARCH_RESPONSE = REPO / 'data/new-library-rebased-20261009-v03/live-album-search-20261009/responses/search-03.json'
ALBUM_RESPONSE = REPO / 'data/new-library-rebased-20261009-v03/live-album-catalog-20261009/album-responses/c5b246fd2d7712eabc9a86b4385ca1766a7948cf50bf68471d54ba65fb6a9751.json'
DIRECT_CANDIDATE = Path('/tmp/opengenius-song-for-prism-direct-catalog-v01/candidate.json')
OUTPUT = Path('/tmp/opengenius-song-for-prism-standard-seed-v01')

PINS = {
    SNAPSHOT: '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    IDENTITY_MAP: '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
    V16_STATE: '182fabadd69120977ceaee9a767ef8a8840d98cb178eeae51d5633885c740db9',
    V16_GRAPH: '682d53a1baac103b6fa3f200ee71f9d04aa2c6215c18b931e11ba3ee3f662948',
    PILOT_PROGRESS: '1eea8551bdf70703c556b2273f36f921c2e99e4640f1ef69d33b41ff5c3ce5bf',
    OLD_SEARCH_EVIDENCE: 'c63366fbd627886f8422efd23562459c1a3bbed05a29bab8353bd5ebdbb63432',
    OLD_CATALOG_EVIDENCE: 'a3c1e0b150efa7e90593a85ca7325604c3bcc6071d669d67c6a098fd2c40bc4c',
    SEARCH_RESPONSE: 'b948d27dcf2cc968b2bab7b72fe2b9da5d3ab72ef1ec6c4a61cb2790404c54d8',
    ALBUM_RESPONSE: 'b8d870237882d75e4fd36f039a6c16fa5d2eb7c22315523c5f19e13f794657ef',
    DIRECT_CANDIDATE: '6d82aa3b82a28cfc94601ea7baea7ae34e7e785bb6e5bc96f97a260175a344bb',
}
LIBRARY_SHA256 = '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
ALBUM_ID = 'MPREb_8t2cRYXsUxq'
EXPECTED_PID_VIDEO = {
    '1A42473857629B66': 'NZUuy1z2CWw', '1EF33701D2CB08C1': 'XtmWrd8tRug',
    '3253BF0DF2F190B1': 'MXyhpkMS6_g', '4FBF8B0C59A1D07C': 'LKhPg7gcyX4',
    '6AB188BAC58382D3': '7nI_2u7lFVk', '6ED5BC8244B848D7': 'TXNBv7eaWBM',
    '73227DC34706404F': 'c1nqC0phDR4', '7381D578024B296A': 'Qy533Jiwhfo',
    '9A8FE2E79BDE63E3': '9U8thzgMWs4', 'A7C5DC91E5BF930C': 'nNer_QuMtyk',
    'ADA608E251A114F6': 'SA0Fo2ALLYQ', 'F39570C858AB3943': '_eWO0abtXX0',
    'F4C68020BF546813': 'HpMCl9t6anA', 'FFA65A4ADCE9AF6D': 'uvPHQmMdAtA',
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_pinned(path: Path) -> bytes:
    data = path.read_bytes()
    if _sha(data) != PINS[path]:
        raise ValueError(f'pinned source changed: {path.name}')
    return data


def _write(path: Path, document):
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True,
                         indent=2).encode('utf-8') + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(encoded)


def build(output: Path = OUTPUT):
    if output.exists():
        raise FileExistsError('candidate output namespace must be new')
    raw = {path: _read_pinned(path) for path in PINS}
    tracks, library_hash, _ = load_library(track_snapshot=SNAPSHOT)
    if library_hash != LIBRARY_SHA256:
        raise ValueError('pinned Library hash differs')
    map_doc = json.loads(raw[IDENTITY_MAP])
    matcher = IdentityMap(map_doc, tracks, library_hash)
    state_doc = json.loads(raw[V16_STATE])
    graph_doc = json.loads(raw[V16_GRAPH])
    progress_doc = json.loads(raw[PILOT_PROGRESS])
    old_search_doc = json.loads(raw[OLD_SEARCH_EVIDENCE])
    old_catalog_doc = json.loads(raw[OLD_CATALOG_EVIDENCE])
    search_response_doc = json.loads(raw[SEARCH_RESPONSE])
    album_response_doc = json.loads(raw[ALBUM_RESPONSE])
    direct_doc = json.loads(raw[DIRECT_CANDIDATE])
    identity_hash = _sha(raw[IDENTITY_MAP])
    state_hash = _sha(raw[V16_STATE])
    snapshot_hash = _sha(raw[SNAPSHOT])

    state_source = state_doc.get('source', {})
    if (state_source.get('library_sha256') != library_hash
            or state_source.get('identity_map_sha256') != identity_hash
            or graph_doc.get('library_sha256') != library_hash
            or graph_doc.get('identity_map_sha256') != identity_hash
            or graph_doc.get('source_state_sha256') != state_hash
            or direct_doc.get('identity_map_sha256') != identity_hash
            or direct_doc.get('library_sha256') != library_hash):
        raise ValueError('current graph/state/candidate source closure differs')
    if (old_search_doc.get('identity_map_sha256') == identity_hash
            or old_catalog_doc.get('identity_map_sha256') == identity_hash):
        raise ValueError('historical catalog inputs unexpectedly claim current-map scope')

    prior_pids = set(progress_doc.get('attempted_pids', []))
    active_roots = {row.get('root_pid') for row in graph_doc.get('accepted_roots', [])}
    indexed_pids = {row.get('persistent_id') for row in state_doc.get('rows', [])}
    if None in indexed_pids or None in active_roots:
        raise ValueError('source state or graph has a malformed PID row')

    old_rows = [row for row in old_search_doc.get('search_album_candidates', [])
                if row.get('browse_id') == ALBUM_ID]
    if len(old_rows) != 1:
        raise ValueError('historical saved search has no unique exact album result')
    old_row = old_rows[0]
    source = old_row.get('source') or {}
    query = old_row.get('query')
    if (source.get('response_sha256') != _sha(raw[SEARCH_RESPONSE])
            or source.get('response_path') != str(SEARCH_RESPONSE)
            or search_response_doc.get('endpoint') != 'search'
            or search_response_doc.get('arguments') != source.get('arguments')
            or source.get('arguments') != {'query': query, 'filter': 'albums', 'limit': 5}
            or search_response_doc.get('response', [])[source.get('result_position')].get('browseId') != ALBUM_ID):
        raise ValueError('saved album-search result does not match its raw response receipt')
    album_result = search_response_doc['response'][source['result_position']]
    search_plan = build_album_search_plan(
        tracks, library_hash, identity_hash, matcher, indexed_pids,
        state_hash=state_hash, track_snapshot_hash=snapshot_hash, limit=100)
    matching_query = [row for row in search_plan['albums'] if row.get('query') == query]
    if len(matching_query) != 1:
        raise ValueError('current-map search plan has no unique saved exact query')
    saved_search = {'query': query, 'path': str(SEARCH_RESPONSE),
                    'sha256': _sha(raw[SEARCH_RESPONSE]),
                    'response': search_response_doc['response']}

    output.mkdir(mode=0o700, parents=False)
    plan_path = output / 'current-map-album-search-plan.json'
    search_evidence_path = output / 'current-map-album-search-evidence.json'
    album_plan_path = output / 'current-map-album-catalog-plan.json'
    catalog_evidence_path = output / 'current-map-album-catalog-evidence.json'
    base_seed_path = output / 'base-seed-video-evidence.json'
    combined_seed_path = output / 'combined-seed-video-evidence.json'
    report_path = output / 'report.json'

    _write(plan_path, search_plan)
    search_evidence = build_album_search_evidence(
        search_plan, [saved_search], tracks, matcher, library_hash, identity_hash)
    _write(search_evidence_path, search_evidence)
    catalog_plan = build_album_plan(
        tracks, library_hash, identity_hash, indexed_pids, [], matcher,
        [search_evidence_path])
    _write(album_plan_path, catalog_plan)
    saved_album = [{'album_id': ALBUM_ID, 'path': str(ALBUM_RESPONSE),
                    'sha256': _sha(raw[ALBUM_RESPONSE]),
                    'response': album_response_doc['response']}]
    catalog_evidence = build_album_catalog_evidence(
        catalog_plan, saved_album, tracks, matcher, library_hash, identity_hash)
    catalog_evidence['album_plan_sha256'] = _sha(album_plan_path.read_bytes())
    _write(catalog_evidence_path, catalog_evidence)
    validated_rows = validate_album_catalog_evidence(
        catalog_evidence, catalog_evidence_path, tracks, matcher,
        library_hash, identity_hash)

    base_seed = build_seed_video_evidence([], matcher, library_hash, identity_hash)
    _write(base_seed_path, base_seed)
    combined = combine_seed_video_evidence(
        base_seed, base_seed_path, catalog_evidence, catalog_evidence_path,
        tracks, matcher, library_hash, identity_hash)
    _write(combined_seed_path, combined)
    all_seeds = [{'persistent_id': track['persistent_id']} for track in tracks]
    selected = select_seed_video_evidence(
        combined, combined_seed_path, all_seeds, tracks, matcher,
        library_hash, identity_hash)
    selected_pairs = {pid: row['video_id'] for pid, row in selected.items()}
    if selected_pairs != EXPECTED_PID_VIDEO:
        raise ValueError('current-map selector result differs from reviewed direct candidate')
    if set(selected_pairs) & (prior_pids | active_roots):
        raise ValueError('candidate set overlaps prior attempts or active accepted roots')

    report = {
        'schema_version': 1,
        'producer': 'opengenius-current-map-saved-album-standard-seed-v1',
        'status': 'independent_standard_consumer_pass',
        'current_identity_map_sha256': identity_hash,
        'library_sha256': library_hash,
        'v16_source_state_sha256': state_hash,
        'v16_accepted_graph_sha256': _sha(raw[V16_GRAPH]),
        'historical_catalog_map_used_as_current': False,
        'catalog_album_id': ALBUM_ID,
        'exact_current_map_search_query': query,
        'candidate_seed_count': len(selected_pairs),
        'candidate_pid_video_pairs': dict(sorted(selected_pairs.items())),
        'all_candidates_fresh_unattempted_nonroot': True,
        'recording_identity_verified': 0,
        'authenticated_watch_radio_proofs': 0,
        'outgoing_roots_created': 0,
        'recommendation_edges_created': 0,
        'network_requests': 0,
        'source_receipts': {
            'historical_search_evidence_sha256': _sha(raw[OLD_SEARCH_EVIDENCE]),
            'historical_search_response_sha256': _sha(raw[SEARCH_RESPONSE]),
            'historical_catalog_evidence_sha256': _sha(raw[OLD_CATALOG_EVIDENCE]),
            'saved_album_response_sha256': _sha(raw[ALBUM_RESPONSE]),
            'direct_candidate_sha256': _sha(raw[DIRECT_CANDIDATE]),
            'search_plan_sha256': _sha(plan_path.read_bytes()),
            'current_search_evidence_sha256': _sha(search_evidence_path.read_bytes()),
            'current_catalog_plan_sha256': _sha(album_plan_path.read_bytes()),
            'current_catalog_evidence_sha256': _sha(catalog_evidence_path.read_bytes()),
            'combined_seed_evidence_sha256': _sha(combined_seed_path.read_bytes()),
        },
        'validated_catalog_rows': len(validated_rows),
    }
    _write(report_path, report)
    return report


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False, sort_keys=True))
