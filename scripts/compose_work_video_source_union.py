"""Compose an offline candidate-only graph from the pinned 870 parent and audited work-radio stages.

This tool never authenticates, performs requests, edits a Library database, or
marks graph credit accepted. Historical parent rows remain an input candidate
projection and work rows must be replayed from each audited stage's own cache.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import audit_work_video_radio as work_video_audit

ROOT = Path(__file__).resolve().parents[1]
V15 = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v15-shiny-ost-radio-live-v01'
OLD_GRAPH = V15 / 'batch-0012/growth/accepted-graphs.json'
OLD_STATE = V15 / 'batch-0012/growth/state.json'
OLD_PROGRESS = V15 / 'progress.json'
OLD_TERMINAL = V15 / 'v16-recovery-continuation-terminal-v01.json'
NATIVE_DIR = ROOT / 'data/ytmusic/native-genius-baseline-515-v13-alias-child-v02'
NATIVE_GRAPH = NATIVE_DIR / 'accepted-baseline-graphs.json'
NATIVE_STATE = NATIVE_DIR / 'baseline-state.json'
TRACK_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
IDENTITY_MAP = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/reviewed-shiny-ost-title-aliases-v01/identity-map.json'
WORK_PACKET = ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
WORK_BINDINGS = WORK_PACKET / 'candidate-bindings.json'
WORK_REPORT = WORK_PACKET / 'report.json'
WORK_HTML = WORK_PACKET / 'hallab-official-page.html'
WORK_RECIPE = WORK_PACKET / 'root-metadata-recipe-review.json'

PINS = {
    'old_graph': '4d8045953eac5ce2885dcd545d96850b578e20420f1726e24a5c9ad718ae9935',
    'old_state': '5b2af6641c9a89373bace5cc5f8b78a9b61b37f12c0bb613328a867ed7daa533',
    'progress': 'cd4cd13ec7d58734854e5bde60c4a11832ee22cc55f2ac947ed703ee0b82ddf0',
    'terminal': 'fee44e5c45924697c4ee8c1498c4fc580fb783a6857719d7c18cd71d3c6b779e',
    'native_graph': '60a410d9e3a499b97e24d2ac28be4269d50e6a33e07f4526df3f3ee461f882ff',
    'native_state': '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
    'track_snapshot': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    'identity_map': 'f4ed5d23c1a6b2909defa135387867937f0133e550fe1db0c30a278e5fda4734',
    'work_bindings': '7356f7efa579eb574a7df78b3d4ee1f080f0a79c9ceab2b32baf847d859aea62',
    'work_report': 'cbb4d07ccbd2ad8792a85eb7b1714370fafcd488e684a8776bb35859a50d7505',
    'work_html': '2e66073594e9f3c5d450ac4508d576f09da88cb369b66fafb26be309bb3193d0',
    'work_recipe': 'f07f420cde92b95f0e4098b4095ce91e057c366be1d8a6ff3f0b07efd02a388c',
    'runtime_manifest': '46f45b9a2b3fd3190467541dd5301cdeb5f31d9969025869f920b634f1bc94c1',
    'work_stage_graph': 'a8a83035a791dac15567f31250dd1c44d7e66b797f210babcd8468157c415bd6',
    'work_audit_receipt': 'd67be3374b29dd44f4a234c6b8d8f1cf66b459e5733e86d800d4d903a00bd0e5',
    'work_validated_graph': '0f2aed57f653704ea4869482d8d53506737c7f1ea3579697c16c50548e863134',
}
WORK_AUDIT = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001-independent-audit-v02'
WORK_STAGE = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001'
RUNTIME = Path('/tmp/opengenius-work-video-capture-runtime-v02')
PREFIX_PROFILE = ROOT / (
    'data/ytmusic/post870-kirby-playlist-stage2-inputs-20261015-v01/'
    'root-reviewed-capture-profile.json')
PREFIX_PROFILE_SHA256 = (
    '115182969c289ca4eb1a20fed2190ac9abe8848b35116456434d37e91951a594')


class UnionError(ValueError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def canonical_sha(value) -> str:
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8'))


def require(ok: bool, message: str):
    if not ok:
        raise UnionError(message)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnionError(f'invalid JSON input: {path.name}') from exc


def _load_runtime_module(runtime: Path, rel: str, name: str):
    path = runtime / rel
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None,
            f'cannot load frozen module: {Path(rel).name}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify_frozen_runtime(runtime: Path, expected_manifest_sha256: str | None = None,
                          expected_module_count: int = 187):
    manifest_path = runtime / 'runtime-manifest.json'
    expected_manifest_sha256 = expected_manifest_sha256 or PINS['runtime_manifest']
    require(file_sha(manifest_path) == expected_manifest_sha256,
            'work-video runtime manifest hash changed')
    manifest = read_json(manifest_path)
    modules = manifest.get('modules')
    require(isinstance(modules, list) and len(modules) == expected_module_count
            and expected_module_count in (187, 188),
            'work-video runtime module closure has changed')
    for row in modules:
        rel = Path(row.get('path', ''))
        require(not rel.is_absolute() and '..' not in rel.parts
                and file_sha(runtime / rel) == row.get('sha256'),
                'work-video runtime module hash changed')
    binder = _load_runtime_module(runtime, 'scripts/work_track_video_bindings.py',
                                  '_work_union_frozen_bindings')
    return binder


_STAGE_PIN_KEYS = {
    'schema_version', 'producer', 'stage_path', 'audit_dir', 'runtime_path',
    'wrapper_path', 'reviewed_profile_path', 'reviewed_profile_sha256',
    'stage_graph_sha256', 'audit_receipt_sha256', 'validated_graph_sha256',
    'runtime_manifest_sha256', 'capture_wrapper_sha256',
}


def load_reviewed_stage_pin(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    require(sha(raw) == expected_sha256,
            'root-reviewed per-stage audit pin file hash differs')
    pin = json.loads(raw)
    require(isinstance(pin, dict) and set(pin) == _STAGE_PIN_KEYS
            and pin.get('schema_version') == 1
            and pin.get('producer') == 'opengenius-reviewed-work-stage-audit-pin-v1',
            'root-reviewed per-stage audit pin schema differs')
    for key in ('stage_path', 'audit_dir', 'runtime_path', 'wrapper_path',
                'reviewed_profile_path'):
        value = pin.get(key)
        require(isinstance(value, str) and Path(value).is_absolute(),
                'stage audit pin requires fixed absolute paths')
    for key in ('reviewed_profile_sha256', 'stage_graph_sha256',
                'audit_receipt_sha256', 'validated_graph_sha256',
                'runtime_manifest_sha256', 'capture_wrapper_sha256'):
        value = pin.get(key)
        require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value),
                'stage audit pin contains a malformed digest')
    profile_path = Path(pin['reviewed_profile_path'])
    require(file_sha(profile_path) == pin['reviewed_profile_sha256'],
            'root-reviewed capture profile bytes differ from stage pin')
    profile = read_json(profile_path)
    require(profile.get('runtime_manifest_sha256') == pin['runtime_manifest_sha256']
            and profile.get('wrapper_sha256') == pin['capture_wrapper_sha256']
            and Path(profile.get('stage', '')).resolve() == Path(pin['stage_path']).resolve()
            and Path(profile.get('runtime', '')).resolve() == Path(pin['runtime_path']).resolve()
            and Path(profile.get('wrapper', '')).resolve() == Path(pin['wrapper_path']).resolve(),
            'capture profile and stage pin disagree')
    return pin


_PREFIX_PIN_KEYS = {
    'schema_version', 'producer', 'reviewed_profile_path',
    'reviewed_profile_sha256', 'prefix_receipt_path',
    'prefix_receipt_sha256', 'prefix_projection_path',
    'prefix_projection_sha256', 'stage_path', 'stage_process_sha256',
    'stage_progress_sha256', 'supervisor_terminal_sha256',
    'runtime_path', 'runtime_manifest_sha256', 'wrapper_path',
    'capture_wrapper_sha256',
}


def load_reviewed_prefix_pin(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    require(sha(raw) == expected_sha256,
            'root-reviewed interrupted-prefix pin file hash differs')
    pin = json.loads(raw)
    require(isinstance(pin, dict) and set(pin) == _PREFIX_PIN_KEYS
            and pin.get('schema_version') == 1
            and pin.get('producer') ==
            'opengenius-reviewed-interrupted-prefix-audit-pin-v1',
            'root-reviewed interrupted-prefix pin schema differs')
    for key in ('reviewed_profile_path', 'prefix_receipt_path',
                'prefix_projection_path', 'stage_path', 'runtime_path',
                'wrapper_path'):
        value = pin.get(key)
        require(isinstance(value, str) and Path(value).is_absolute(),
                'prefix audit pin requires fixed absolute paths')
    for key in ('reviewed_profile_sha256', 'prefix_receipt_sha256',
                'prefix_projection_sha256', 'stage_process_sha256',
                'stage_progress_sha256', 'supervisor_terminal_sha256',
                'runtime_manifest_sha256', 'capture_wrapper_sha256'):
        require(isinstance(pin.get(key), str)
                and re.fullmatch(r'[0-9a-f]{64}', pin[key]),
                'prefix audit pin contains a malformed digest')
    require(pin['reviewed_profile_sha256'] == PREFIX_PROFILE_SHA256
            and Path(pin['reviewed_profile_path']).resolve() == PREFIX_PROFILE.resolve(),
            'prefix pin is not bound to the fixed reviewed v01 profile')
    profile = read_json(PREFIX_PROFILE)
    require(file_sha(PREFIX_PROFILE) == pin['reviewed_profile_sha256']
            and Path(profile['stage']).resolve() == Path(pin['stage_path']).resolve()
            and Path(profile['runtime']).resolve() == Path(pin['runtime_path']).resolve()
            and Path(profile['wrapper']).resolve() == Path(pin['wrapper_path']).resolve()
            and profile['runtime_manifest_sha256'] == pin['runtime_manifest_sha256']
            and profile['wrapper_sha256'] == pin['capture_wrapper_sha256'],
            'reviewed prefix profile and pin disagree')
    stage = Path(pin['stage_path'])
    supervisor = Path(profile['supervisor'])
    require(file_sha(stage / 'process.json') == pin['stage_process_sha256']
            and file_sha(stage / 'progress.json') == pin['stage_progress_sha256']
            and file_sha(supervisor / 'terminal.json') == pin['supervisor_terminal_sha256'],
            'interrupted prefix source process/progress/terminal hash differs')
    return pin


def load_reviewed_work_prefix(pin_path: Path, expected_sha256: str) -> dict:
    """Replay the five completed own pairs; keep the sixth partial radio non-root."""
    pin = load_reviewed_prefix_pin(pin_path, expected_sha256)
    prefix_path = Path(__file__).resolve().with_name(
        'audit_work_video_prefix_candidates.py')
    spec = importlib.util.spec_from_file_location(
        '_work_union_interrupted_prefix_auditor', prefix_path)
    require(spec is not None and spec.loader is not None,
            'cannot load the fixed interrupted-prefix replay module')
    prefix_auditor = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prefix_auditor
    spec.loader.exec_module(prefix_auditor)
    receipt, projection, batch = prefix_auditor.replay_prefix_for_composer(
        Path(pin['reviewed_profile_path']), pin['reviewed_profile_sha256'])
    receipt_path = Path(pin['prefix_receipt_path'])
    projection_path = Path(pin['prefix_projection_path'])
    require(file_sha(receipt_path) == pin['prefix_receipt_sha256']
            and file_sha(projection_path) == pin['prefix_projection_sha256'],
            'root-reviewed prefix receipt or projection bytes differ')
    saved_receipt = read_json(receipt_path)
    saved_projection = read_json(projection_path)
    require(canonical_sha(saved_receipt) == canonical_sha(receipt),
            'prefix receipt does not equal a replay of the five own pairs')
    projection_link = saved_projection.pop('source_prefix_receipt', None)
    require(projection_link == {
        'path': str(receipt_path), 'sha256': pin['prefix_receipt_sha256']}
            and canonical_sha(saved_projection) == canonical_sha(projection),
            'prefix candidate projection differs from the raw-proof replay')
    require(receipt.get('status') == 'interrupted_prefix_validated_candidate_only'
            and receipt.get('validated_complete_pairs') == 5
            and receipt.get('validated_candidate_graphs') == 5
            and receipt.get('partial_seed_persistent_id') == 'C25E18E3D7DEEA7D'
            and receipt.get('partial_pair_root_credit') == 0
            and receipt.get('root_credit') == receipt.get('edge_credit')
            == receipt.get('source_credit') == 0
            and batch.get('prefix_status') == receipt['status']
            and len(batch.get('sources', [])) == 5
            and len(batch.get('pairs', [])) == 5
            and batch.get('partial_pair_has_source') is False,
            'interrupted prefix is not a five-pair candidate-only source')
    partial_pid = batch['partial_pid']
    require(all(source['root_pid'] != partial_pid for source in batch['sources'])
            and all(pid != partial_pid for pid, _ in batch['pairs'])
            and any(row.get('persistent_id') == partial_pid
                    for row in batch['bindings'])
            and batch.get('raw_radio_response_count') == 6,
            'sixth partial radio was credited as a root or omitted from ambiguity inputs')
    batch.update({
        'prefix_audit_pin_path': str(pin_path.resolve()),
        'prefix_audit_pin_sha256': expected_sha256,
        'prefix_receipt_sha256': pin['prefix_receipt_sha256'],
        'prefix_projection_sha256': pin['prefix_projection_sha256'],
        'stage_process_sha256': pin['stage_process_sha256'],
        'stage_progress_sha256': pin['stage_progress_sha256'],
        'supervisor_terminal_sha256': pin['supervisor_terminal_sha256'],
        'runtime_manifest_sha256': pin['runtime_manifest_sha256'],
        'reviewed_profile_sha256': pin['reviewed_profile_sha256'],
    })
    return batch


def binding_signature(row: dict) -> tuple:
    return tuple(row.get(key) for key in (
        'persistent_id', 'video_id', 'disc_track', 'official_japanese_title',
        'local_title', 'local_artist', 'local_album', 'local_duration_ms',
        'remote_title', 'remote_artists', 'remote_album', 'remote_length',
        'duration_delta_ms'))


def _verify_request(stage: Path, key: str, receipt_hash: str,
                    video_id: str, radio: bool, limit: int) -> dict:
    require(isinstance(key, str) and re.fullmatch(r'[0-9a-f]{64}', key),
            'candidate graph request key is malformed')
    path = stage / 'requests' / f'{key}.json'
    raw = path.read_bytes()
    require(sha(raw) == receipt_hash, 'raw request receipt hash differs')
    request = json.loads(raw)
    args = {'videoId': video_id, 'radio': radio, 'limit': limit}
    expected = sha(json.dumps(['get_watch_playlist', args], ensure_ascii=False,
                              sort_keys=True).encode())
    require(expected == key and request.get('endpoint') == 'get_watch_playlist'
            and request.get('arguments') == args
            and set(request) == {'endpoint', 'arguments', 'observed_at', 'response'},
            'raw request cache does not bind this own seed and endpoint limit')
    return request


def load_audited_work_stage(stage: Path, audit_dir: Path, *, binder,
                            official: dict, local_tracks: list[dict],
                            stage_pin: dict | None = None,
                            stage_pin_path: Path | None = None,
                            stage_pin_sha256: str | None = None) -> dict:
    """Recheck the saved audit receipt and every root's raw watch pair offline."""
    if stage_pin is None:
        require(stage.resolve() == WORK_STAGE.resolve()
                and audit_dir.resolve() == WORK_AUDIT.resolve(),
                'unreviewed later-stage composition requires a root audit pin')
        expected_receipt_sha = PINS['work_audit_receipt']
        expected_graph_sha = PINS['work_validated_graph']
        expected_runtime_sha = PINS['runtime_manifest']
    else:
        require(Path(stage_pin['stage_path']).resolve() == stage.resolve()
                and Path(stage_pin['audit_dir']).resolve() == audit_dir.resolve(),
                'root audit pin paths do not match the selected stage')
        expected_receipt_sha = stage_pin['audit_receipt_sha256']
        expected_graph_sha = stage_pin['validated_graph_sha256']
        expected_runtime_sha = stage_pin['runtime_manifest_sha256']
    receipt_path = audit_dir / 'audit-receipt.json'
    graph_path = audit_dir / 'validated-candidate-graphs.json'
    stage_graph_path = stage / 'candidate-graphs.json'
    require(file_sha(receipt_path) == expected_receipt_sha
            and file_sha(graph_path) == expected_graph_sha
            and file_sha(stage_graph_path) == (
                stage_pin['stage_graph_sha256'] if stage_pin is not None
                else PINS['work_stage_graph']),
            'pinned independent work-stage audit artifact changed')
    receipt, projection = read_json(receipt_path), read_json(graph_path)
    require(receipt.get('status') == 'independently_validated_candidate_only'
            and receipt.get('runtime_manifest_sha256') == expected_runtime_sha
            and receipt.get('recipe_sha256') == PINS['work_recipe']
            and receipt.get('stage_graph_sha256') == file_sha(stage_graph_path)
            and receipt.get('validated_graph_sha256') == file_sha(graph_path)
            and receipt.get('root_credit') == receipt.get('edge_credit')
            == receipt.get('source_credit') == 0
            and receipt.get('database_written') is False,
            'work-stage audit receipt does not bind this candidate-only source')
    require(projection.get('status') == 'validated_candidate_only'
            and projection.get('source_stage_graph_sha256') == file_sha(stage_graph_path)
            and projection.get('root_credit') == projection.get('edge_credit')
            == projection.get('source_credit') == 0
            and projection.get('recording_identity_verified') is False,
            'validated work-stage graph has wrong provenance or claims credit')
    stage_graph = read_json(stage_graph_path)
    process = read_json(stage / 'process.json')
    manifest = process.get('request_cache_manifest')
    require(process.get('status') == 'completed'
            and process.get('account_authentication_verified') is True
            and process.get('http_requests') == receipt['http_used']
            and isinstance(manifest, list)
            and process.get('request_cache_manifest_sha256') == canonical_sha(manifest)
            and process.get('request_cache_manifest_sha256') ==
            receipt.get('summary', {}).get('request_cache_manifest_sha256')
            and stage_graph_path.is_file()
            and process.get('candidate_graphs_sha256') == file_sha(stage_graph_path)
            and read_json(stage_graph_path).get('summary', {}).get('request_cache_hits') == 0
            and len(manifest) == 2 * len(stage_graph.get('results', []))
            and len(stage_graph.get('results', []))
            == receipt.get('summary', {}).get('validated_seed_pairs'),
            'stage process or request-cache manifest differs from audit receipt')
    manifest_by_name = {row['path'].split('/')[-1]: row for row in manifest}
    require(len(manifest_by_name) == len(manifest),
            'stage request-cache manifest has duplicate filenames')
    for row in manifest:
        rel = Path(row.get('path', ''))
        require(not rel.is_absolute() and len(rel.parts) == 2
                and rel.parts[0] == 'requests' and rel.suffix == '.json',
                'stage request manifest has an unsafe path')
        raw_path = stage / rel
        require(raw_path.is_file() and raw_path.stat().st_size == row.get('bytes')
                and file_sha(raw_path) == row.get('sha256'),
                'stage request cache differs from the audited manifest')
    pairs, bindings, sources = [], [], []
    for graph_row in projection.get('candidate_graphs', []):
        pid, video = graph_row.get('persistent_id'), graph_row.get('seed_video_id')
        seed_binding = graph_row.get('seed_binding')
        require(isinstance(pid, str) and isinstance(video, str)
                and seed_binding.get('persistent_id') == pid
                and seed_binding.get('video_id') == video,
                'validated candidate lacks its own seed binding')
        radio_key, radio_hash = (graph_row.get('watch_radio_request_key'),
                                 graph_row.get('watch_radio_receipt_sha256'))
        watch_key, watch_hash = (graph_row.get('watch_seed_request_key'),
                                 graph_row.get('watch_seed_receipt_sha256'))
        radio_path = stage / 'requests' / f'{radio_key}.json'
        watch_path = stage / 'requests' / f'{watch_key}.json'
        for path, digest in ((radio_path, radio_hash), (watch_path, watch_hash)):
            entry = manifest_by_name.get(path.name)
            require(entry is not None and entry.get('sha256') == digest
                    and entry.get('bytes') == path.stat().st_size
                    and file_sha(path) == digest,
                    'per-root own request is missing from the sealed stage manifest')
        radio = _verify_request(stage, radio_key, radio_hash, video, True, 100)
        watch = _verify_request(stage, watch_key, watch_hash, video, False, 25)
        returned_seed_rows = [row for row in watch.get('response', {}).get('tracks', [])
                              if row.get('videoId') == video]
        seed_bound, _ = binder.bind_rows(returned_seed_rows, official, local_tracks)
        require(len(seed_bound) == 1 and seed_bound[0]['persistent_id'] == pid
                and binding_signature(seed_bound[0]) == binding_signature(seed_binding)
                and binding_signature(graph_row.get('fresh_seed_return_binding', {}))
                == binding_signature(seed_binding),
                'fresh own watch_seed receipt does not bind the selected work root')
        radio_rows = radio.get('response', {}).get('tracks')
        targets, _ = binder.ordered_radio_targets(radio_rows, pid, official, local_tracks)
        require([binding_signature(x) for x in targets]
                == [binding_signature(x) for x in graph_row.get('targets', [])]
                and [x['persistent_id'] for x in targets]
                == graph_row.get('ordered_target_pids'),
                'candidate targets differ from recomputed ordered raw radio rows')
        require(graph_row.get('status') == 'review_candidate_only'
                and graph_row.get('recording_identity_verified') is False
                and graph_row.get('root_credit') == graph_row.get('edge_credit')
                == graph_row.get('source_credit') == 0,
                'work source row claims identity or recommendation credit')
        pairs.append((pid, video))
        bindings.append(seed_binding)
        bindings.extend(targets)
        sources.append({'root_pid': pid, 'video_id': video,
                        'ordered_target_pids': [x['persistent_id'] for x in targets],
                        'radio_request_key': radio_key, 'radio_receipt_sha256': radio_hash,
                        'watch_seed_request_key': watch_key,
                        'watch_seed_receipt_sha256': watch_hash})
    require(len(sources) == receipt.get('summary', {}).get('validated_roots')
            and len(sources) <= receipt.get('summary', {}).get('validated_seed_pairs'),
            'candidate root count differs from independent audit summary')
    # Failed identity/empty-target pairs do not become roots, but their observed
    # metadata still participates in collisions across separately audited stages.
    for result in stage_graph['results']:
        bindings.append(result['seed_binding'])
        radio = _verify_request(stage, result['watch_radio']['request_key'],
            result['watch_radio']['receipt_sha256'], result['seed_video_id'], True, 100)
        observed, _ = binder.bind_rows(radio['response']['tracks'], official, local_tracks)
        bindings.extend(observed)
    return {'sources': sources, 'bindings': bindings, 'pairs': pairs,
            'receipt_sha256': file_sha(receipt_path),
            'validated_graph_sha256': file_sha(graph_path),
            'stage_graph_sha256': file_sha(stage_graph_path),
            'runtime_manifest_sha256': expected_runtime_sha,
            'reviewed_profile_sha256': (stage_pin['reviewed_profile_sha256']
                                        if stage_pin is not None else None),
            'stage_audit_pin_path': (str(stage_pin_path.resolve())
                                     if stage_pin_path is not None else None),
            'stage_audit_pin_sha256': stage_pin_sha256,
            'http_requests': receipt['http_used']}


def global_ambiguities(binding_sets: list[list[dict]]):
    pid_videos, video_pids = defaultdict(set), defaultdict(set)
    for bindings in binding_sets:
        for row in bindings:
            pid, video = row.get('persistent_id'), row.get('video_id')
            if isinstance(pid, str) and isinstance(video, str):
                pid_videos[pid].add(video)
                video_pids[video].add(pid)
    bad_pids = {pid for pid, vids in pid_videos.items() if len(vids) > 1}
    bad_videos = {video for video, pids in video_pids.items() if len(pids) > 1}
    bad_pids.update(pid for video in bad_videos for pid in video_pids[video])
    return bad_pids, bad_videos


def merge_root_vectors(old_roots: list[dict], new_sources: list[dict], *,
                       known_pids: set[str], ambiguous_pids: set[str],
                       ambiguous_videos: set[str], binding_by_pair: dict):
    """Preserve old nonempty rows and promote only uniquely observed own roots."""
    require(isinstance(old_roots, list) and old_roots,
            'old graph must have a nonempty accepted root list')
    merged = copy.deepcopy(old_roots)
    by_pid = {}
    for index, row in enumerate(merged):
        pid, targets = row.get('root_pid'), row.get('ordered_target_pids')
        require(isinstance(pid, str) and pid not in by_pid
                and isinstance(targets, list) and len(targets) == len(set(targets)),
                'old graph has invalid or duplicate root vectors')
        by_pid[pid] = index
        require(pid in known_pids and all(t in known_pids for t in targets),
                'old graph references a PID outside the pinned Library snapshot')
    candidate_rows = {}
    for source in new_sources:
        pid = source['root_pid']
        require(pid in known_pids, 'new radio source root is not a Library PID')
        targets = source['ordered_target_pids']
        require(targets and len(targets) == len(set(targets))
                and all(t in known_pids for t in targets),
                'new radio source has an empty, duplicate, or unknown target vector')
        eligible = [target for target in targets
                    if target not in ambiguous_pids]
        if pid in ambiguous_pids or source['video_id'] in ambiguous_videos:
            continue
        if not eligible:
            continue
        if pid in candidate_rows:
            require(candidate_rows[pid] == eligible,
                    'repeated own-root sources have conflicting radio order vectors')
            continue
        candidate_rows[pid] = eligible
    promoted_empty_rows, appended_rows = 0, 0
    for pid, targets in candidate_rows.items():
        if pid in by_pid:
            row = merged[by_pid[pid]]
            previous = row['ordered_target_pids']
            require(not previous,
                    'work own-root PID already has a nonempty legacy vector')
            row['ordered_target_pids'] = targets
            promoted_empty_rows += 1
        else:
            by_pid[pid] = len(merged)
            merged.append({'root_pid': pid, 'ordered_target_pids': targets})
            appended_rows += 1
    return merged, candidate_rows, {
        'legacy_empty_rows_promoted': promoted_empty_rows,
        'candidate_root_rows_appended': appended_rows,
    }


def compose_documents(*, old_graph: dict, old_state: dict, native_graph: dict,
                      source_batches: list[dict], matcher, library_sha: str,
                      identity_map_sha: str, input_receipts: dict,
                      expected_native_roots=159, expected_old_yt_roots=711):
    old_roots = old_graph.get('accepted_roots')
    old_active = {row['root_pid'] for row in old_roots if row.get('ordered_target_pids')}
    native_roots = native_graph.get('accepted_roots')
    native_active = {row['root_pid'] for row in native_roots if row.get('ordered_target_pids')}
    require(len(old_active) == expected_native_roots + expected_old_yt_roots
            and len(native_active) == expected_native_roots
            and native_active <= old_active,
            'pinned Native/old source active root counts changed')
    old_yt = old_active - native_active
    require(len(old_yt) == expected_old_yt_roots,
            'old YT source partition differs from the pinned count')
    source_rows = [row for batch in source_batches for row in batch['sources']]
    bindings = [row for batch in source_batches for row in batch['bindings']]
    bad_pids, bad_videos = global_ambiguities([bindings])
    # Root/video conflicts discovered in any incoming audited stage are removed
    # before its vector can be represented in the candidate union.
    known_pids = {track['persistent_id'] for track in matcher.tracks}
    roots, accepted_sources, row_changes = merge_root_vectors(
        old_roots, source_rows, known_pids=known_pids,
        ambiguous_pids=bad_pids, ambiguous_videos=bad_videos,
        binding_by_pair={(row.get('persistent_id'), row.get('video_id')): row
                         for row in bindings})
    new_active = {pid for pid, targets in accepted_sources.items() if targets}
    require(not (new_active & old_active),
            'new own-root PID overlaps an already active old root')
    source_union = old_active | new_active
    origin = {
        'native_roots': len(native_active), 'old_yt_roots': len(old_yt),
        'work_radio_roots': len(new_active),
        'unknown_roots': len(source_union - native_active - old_yt - new_active),
    }
    require(origin == {'native_roots': expected_native_roots,
                       'old_yt_roots': expected_old_yt_roots,
                       'work_radio_roots': len(new_active), 'unknown_roots': 0},
            'candidate source-origin partition includes unknown roots')
    edges = sum(len(row['ordered_target_pids']) for row in roots)
    try:
        planner = __import__('plan_genius_update')
    except ModuleNotFoundError:
        from scripts import plan_genius_update as planner
    plan = planner.plan_genius_update(
        old_state, roots, matcher, library_sha, identity_map_sha)
    next_state = plan['next_state']
    old_assignments = {r['persistent_id']: (r['genius_id'], r['metadata_hex'],
                                             canonical_sha(r['equivalence_labels']))
                       for r in old_state['rows']}
    new_assignments = {r['persistent_id']: (r['genius_id'], r['metadata_hex'],
                                             canonical_sha(r['equivalence_labels']))
                       for r in next_state['rows']}
    require(all(new_assignments.get(pid) == value for pid, value in old_assignments.items()),
            'synthetic planner changed an existing GID or metadata row')
    union_graph = {
        'schema_version': 1,
        'library_sha256': library_sha,
        'identity_map_sha256': identity_map_sha,
        'producer': 'opengenius-candidate-work-video-source-union-v1',
        'status': 'private_candidate_union_not_final_source_audit',
        'source_policy': 'authenticated-watch-radio-only-v1',
        'source_receipts': input_receipts,
        'source_origin_partition': origin,
        'old_nonempty_vectors_preserved': True,
        **row_changes,
        'root_credit': 0, 'incoming_root_credit': 0, 'catalog_root_credit': 0,
        'edge_credit': 0, 'source_credit': 0,
        'recording_identity_verified': False,
        'historical_parent_full_replay_completed': False,
        'legacy_parent_vectors_replayed_in_this_union': False,
        'global_pid_video_check_scope':
            'all raw seed/radio bindings in the independently audited incoming stages',
        'database_written': False,
        'summary': {
            'old_active_outgoing_roots': len(old_active),
            'new_independently_audited_own_roots': len(new_active),
            'candidate_active_outgoing_roots': len(source_union),
            'new_own_roots_rejected_by_global_ambiguity': len(set(
                row['root_pid'] for row in source_rows) - new_active),
            'candidate_ordered_edges': edges,
            'candidate_only_new_edges': edges - sum(len(r['ordered_target_pids'])
                                                    for r in old_roots),
            'ambiguous_source_pids_rejected': sorted(bad_pids),
            'ambiguous_source_videos_rejected': sorted(bad_videos),
            'unverified_incoming_targets_credited': 0,
            'catalog_roots_credited': 0,
        },
        'accepted_roots': roots,
        'own_radio_sources': source_rows,
    }
    return union_graph, plan, origin


def _verify_inputs():
    paths = {
        'old_graph': OLD_GRAPH, 'old_state': OLD_STATE, 'progress': OLD_PROGRESS,
        'terminal': OLD_TERMINAL, 'native_graph': NATIVE_GRAPH,
        'native_state': NATIVE_STATE, 'track_snapshot': TRACK_SNAPSHOT,
        'identity_map': IDENTITY_MAP, 'work_bindings': WORK_BINDINGS,
        'work_report': WORK_REPORT, 'work_html': WORK_HTML,
        'work_recipe': WORK_RECIPE,
        'work_audit_receipt': WORK_AUDIT / 'audit-receipt.json',
        'work_validated_graph': WORK_AUDIT / 'validated-candidate-graphs.json',
    }
    for label, path in paths.items():
        require(path.is_file() and file_sha(path) == PINS[label],
                f'pinned input hash changed: {label}')
    progress, terminal = read_json(OLD_PROGRESS), read_json(OLD_TERMINAL)
    require(terminal.get('status') == 'completed' and terminal.get('returncode') == 0
            and terminal.get('accepted_graphs_sha256') == PINS['old_graph']
            and terminal.get('progress_sha256') == PINS['progress']
            and terminal.get('progress_status') == 'exhausted_candidates'
            and progress.get('successful_outgoing_roots') == 870
            and progress.get('status') == 'exhausted_candidates',
            'old 870 graph is not bound by its completed terminal/progress')
    require(read_json(OLD_GRAPH).get('identity_map_sha256') == PINS['identity_map']
            and read_json(OLD_STATE).get('source', {}).get('identity_map_sha256')
            == PINS['identity_map'],
            'old 870 source map differs from the reviewed f4 map')
    recipe = read_json(WORK_RECIPE)
    require(recipe.get('schema_version') == 1,
            'reviewed work recipe packet has unexpected shape')
    return paths, read_json(OLD_GRAPH), read_json(OLD_STATE), read_json(NATIVE_GRAPH)


def compose(output: Path, stage_paths: list[Path], audit_paths: list[Path],
            runtime: Path = RUNTIME,
            stage_audit_pins: list[tuple[Path, str]] | None = None,
            prefix_audit_pin: tuple[Path, str] | None = None):
    require(not output.exists(), 'output path must be new')
    require(stage_paths and len(stage_paths) == len(audit_paths),
            'provide matching audited stage and audit directory lists')
    pin_paths = stage_audit_pins or []
    pins_by_stage = {}
    for pin_path, pin_sha256 in pin_paths:
        pin = load_reviewed_stage_pin(pin_path, pin_sha256)
        stage_key = str(Path(pin['stage_path']).resolve())
        require(stage_key not in pins_by_stage, 'duplicate per-stage audit pin')
        pins_by_stage[stage_key] = (pin, pin_path, pin_sha256)
    input_paths, old_graph, old_state, native_graph = _verify_inputs()
    tracks, library_sha, _ = __import__('music_identity_map').load_library(
        track_snapshot=TRACK_SNAPSHOT)
    identity_doc = read_json(IDENTITY_MAP)
    matcher = __import__('music_identity_map').IdentityMap(identity_doc, tracks, library_sha)
    require(library_sha == old_state['source']['library_sha256'],
            'pinned local track snapshot does not match old state source')
    base_binder = verify_frozen_runtime(runtime)
    doc, report, official, local_tracks, _ = base_binder.load_binding_inputs(
        WORK_BINDINGS, WORK_REPORT, WORK_HTML, TRACK_SNAPSHOT, repo=ROOT)
    require(report.get('status') == 'review_only_metadata_candidates'
            and report.get('summary', {}).get('root_credit') == 0,
            'work source report is not candidate-only')
    work_batches = []
    for stage, audit in zip(stage_paths, audit_paths):
        stage_key = str(stage.resolve())
        pin_entry = pins_by_stage.pop(stage_key, None)
        if pin_entry is None:
            stage_binder = base_binder
            stage_pin = stage_pin_path = stage_pin_sha256 = None
        else:
            stage_pin, stage_pin_path, stage_pin_sha256 = pin_entry
            stage_profile = read_json(Path(stage_pin['reviewed_profile_path']))
            stage_module_count = stage_profile.get('runtime_module_count', 187)
            stage_binder = verify_frozen_runtime(
                Path(stage_pin['runtime_path']), stage_pin['runtime_manifest_sha256'],
                stage_module_count)
            stage_binder = work_video_audit.adapt_reviewed_binder(
                stage_binder, official, local_tracks)
        work_batches.append(load_audited_work_stage(
            stage, audit, binder=stage_binder, official=official,
            local_tracks=local_tracks, stage_pin=stage_pin,
            stage_pin_path=stage_pin_path, stage_pin_sha256=stage_pin_sha256))
    require(not pins_by_stage, 'provided stage audit pin does not match a composed stage')
    prefix_batch = (load_reviewed_work_prefix(*prefix_audit_pin)
                    if prefix_audit_pin is not None else None)
    source_batches = work_batches + ([prefix_batch] if prefix_batch is not None else [])
    input_receipts = {
        'composer_sha256': file_sha(Path(__file__).resolve()),
        'old_graph_sha256': PINS['old_graph'], 'old_state_sha256': PINS['old_state'],
        'old_progress_sha256': PINS['progress'], 'old_terminal_sha256': PINS['terminal'],
        'native_graph_sha256': PINS['native_graph'], 'native_state_sha256': PINS['native_state'],
        'track_snapshot_sha256': PINS['track_snapshot'],
        'identity_map_sha256': PINS['identity_map'],
        'work_recipe_sha256': PINS['work_recipe'],
        'runtime_manifest_sha256': PINS['runtime_manifest'],
        'audited_stages': [{'stage_graph_sha256': b['stage_graph_sha256'],
            'audit_receipt_sha256': b['receipt_sha256'],
            'validated_graph_sha256': b['validated_graph_sha256'],
            'runtime_manifest_sha256': b['runtime_manifest_sha256'],
            'reviewed_profile_sha256': b['reviewed_profile_sha256'],
            'stage_audit_pin_path': b['stage_audit_pin_path'],
            'stage_audit_pin_sha256': b['stage_audit_pin_sha256'],
            'http_requests': b['http_requests']} for b in work_batches],
        'audited_interrupted_prefixes': ([{
            'prefix_audit_pin_path': prefix_batch['prefix_audit_pin_path'],
            'prefix_audit_pin_sha256': prefix_batch['prefix_audit_pin_sha256'],
            'prefix_receipt_sha256': prefix_batch['prefix_receipt_sha256'],
            'prefix_projection_sha256': prefix_batch['prefix_projection_sha256'],
            'stage_process_sha256': prefix_batch['stage_process_sha256'],
            'stage_progress_sha256': prefix_batch['stage_progress_sha256'],
            'supervisor_terminal_sha256': prefix_batch['supervisor_terminal_sha256'],
            'runtime_manifest_sha256': prefix_batch['runtime_manifest_sha256'],
            'reviewed_profile_sha256': prefix_batch['reviewed_profile_sha256'],
            'prefix_status': prefix_batch['prefix_status'],
            'complete_own_pairs': prefix_batch['complete_own_pair_count'],
            'raw_radio_responses_in_ambiguity_check': prefix_batch['raw_radio_response_count'],
            'partial_pid_root_credit': 0,
        }] if prefix_batch is not None else []),
    }
    union_graph, plan, origin = compose_documents(
        old_graph=old_graph, old_state=old_state, native_graph=native_graph,
        source_batches=source_batches, matcher=matcher, library_sha=library_sha,
        identity_map_sha=PINS['identity_map'], input_receipts=input_receipts)
    output.mkdir(parents=True)
    _atomic_json(output / 'candidate-union-graphs.json', union_graph)
    _atomic_json(output / 'candidate-state.json', plan['next_state'])
    _atomic_json(output / 'candidate-plan.json', plan)
    receipt = {
        'schema_version': 1,
        'status': 'private_candidate_union_not_final_source_audit',
        'producer': 'opengenius-candidate-work-video-source-union-v1',
        'input_receipts': input_receipts,
        'source_origin_partition': origin,
        'candidate_active_outgoing_roots': union_graph['summary']['candidate_active_outgoing_roots'],
        'candidate_ordered_edges': union_graph['summary']['candidate_ordered_edges'],
        'candidate_only_new_edges': union_graph['summary']['candidate_only_new_edges'],
        'ambiguous_source_pids_rejected': union_graph['summary']['ambiguous_source_pids_rejected'],
        'ambiguous_source_videos_rejected': union_graph['summary']['ambiguous_source_videos_rejected'],
        'root_credit': 0, 'incoming_root_credit': 0, 'catalog_root_credit': 0,
        'edge_credit': 0, 'source_credit': 0, 'database_written': False,
        'recording_identity_verified': False,
        'candidate_graph_sha256': file_sha(output / 'candidate-union-graphs.json'),
        'candidate_state_sha256': file_sha(output / 'candidate-state.json'),
        'candidate_plan_sha256': file_sha(output / 'candidate-plan.json'),
        'planned_new_assignments': len(plan['new_pids']),
        'historical_parent_full_replay_completed': False,
        'legacy_parent_full_replay_required_before_acceptance': True,
    }
    _atomic_json(output / 'composition-receipt.json', receipt)
    return receipt


def _atomic_json(path: Path, value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         indent=2).encode('utf-8') + b'\n'
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, action='append', required=True,
                        help='completed work-video stage; repeat with each matching --audit-dir')
    parser.add_argument('--audit-dir', type=Path, action='append', required=True,
                        help='independent audit output directory paired with --stage')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    parser.add_argument('--stage-audit-pin', type=Path, action='append', default=[],
                        help='Root-reviewed later-stage audit pin JSON; repeat for each stage')
    parser.add_argument('--stage-audit-pin-sha256', action='append', default=[],
                        help='Expected SHA256 for each --stage-audit-pin, in the same order')
    parser.add_argument('--prefix-audit-pin', type=Path,
                        help='Root-reviewed exact pin for a replayed interrupted-prefix candidate batch')
    parser.add_argument('--prefix-audit-pin-sha256',
                        help='Expected SHA256 for --prefix-audit-pin')
    args = parser.parse_args(argv)
    try:
        if len(args.stage_audit_pin) != len(args.stage_audit_pin_sha256):
            raise UnionError('each stage-audit-pin requires its expected SHA256')
        if (args.prefix_audit_pin is None) != (args.prefix_audit_pin_sha256 is None):
            raise UnionError('prefix-audit-pin requires its expected SHA256')
        receipt = compose(args.output, args.stage, args.audit_dir, args.runtime,
                          list(zip(args.stage_audit_pin, args.stage_audit_pin_sha256)),
                          ((args.prefix_audit_pin, args.prefix_audit_pin_sha256)
                           if args.prefix_audit_pin is not None else None))
    except (UnionError, OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({'status': 'rejected', 'reason': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': receipt['status'],
                      'candidate_active_outgoing_roots': receipt['candidate_active_outgoing_roots'],
                      'candidate_ordered_edges': receipt['candidate_ordered_edges'],
                      'candidate_only_new_edges': receipt['candidate_only_new_edges'],
                      'output': str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
