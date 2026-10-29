"""Independently validate a completed, sealed contextual-radio capture.

This audit is read-only with respect to the capture. It emits only an audit
receipt and a validated candidate-only graph projection after replaying every
saved seed/radio receipt. It never authenticates to YouTube or writes a Library
database or identity map.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace


EXPECTED_RUNTIME_MANIFEST_SHA256 = (
    '46f45b9a2b3fd3190467541dd5301cdeb5f31d9969025869f920b634f1bc94c1')
EXPECTED_WRAPPER_SHA256 = (
    'd292cccf6b3d929ad7746e90626155b6b68a80ba8c535ab7e8098610e94acae6')
EXPECTED_RECIPE_SHA256 = (
    'f07f420cde92b95f0e4098b4095ce91e057c366be1d8a6ff3f0b07efd02a388c')
EXPECTED_HTTP_BEFORE = 4937
EXPECTED_HTTP_CAP = 6500
EXPECTED_STAGE_HTTP_MAX = 200
EXPECTED_SEED_MAX = 44
EXPECTED_RADIO_LIMIT = 100
EXPECTED_INTERVAL = 3
EXPECTED_RUNTIME_MODULE_COUNT = 187
WORK_LABEL = 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'
LOCAL_ALBUM = '星のカービィ スターアライズ オリジナル・サウンドトラック'


class AuditError(ValueError):
    """Raised when saved evidence does not replay to the claimed graph."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return sha256(path.read_bytes())


def _canonical_sha(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8'))


def _fail(condition: bool, message: str):
    if not condition:
        raise AuditError(message)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f'cannot read valid JSON: {path.name}') from error


def _wrapper_config(wrapper_path: Path) -> dict:
    tree = ast.parse(wrapper_path.read_text(encoding='utf-8'), filename=str(wrapper_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'CONFIG'
                for target in node.targets):
            call = node.value
            _fail(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                  and call.func.attr == 'loads' and call.args
                  and isinstance(call.args[0], ast.Constant)
                  and isinstance(call.args[0].value, str),
                  'capture wrapper CONFIG is not a literal JSON value')
            try:
                config = json.loads(call.args[0].value)
            except json.JSONDecodeError as error:
                raise AuditError('capture wrapper CONFIG is malformed') from error
            _fail(isinstance(config, dict), 'capture wrapper CONFIG must be an object')
            return config
    raise AuditError('capture wrapper has no literal CONFIG assignment')


def _verify_frozen_runtime(runtime: Path, wrapper: Path, config: dict) -> dict:
    manifest_path = runtime / 'runtime-manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    _fail(sha256(manifest_bytes) == EXPECTED_RUNTIME_MANIFEST_SHA256,
          'frozen runtime manifest digest differs from reviewed pin')
    manifest = json.loads(manifest_bytes)
    rows = manifest.get('modules')
    _fail(isinstance(rows, list) and len(rows) == EXPECTED_RUNTIME_MODULE_COUNT,
          'frozen runtime module list differs from reviewed size')
    paths = set()
    for item in rows:
        rel = Path(item.get('path', ''))
        _fail(not rel.is_absolute() and '..' not in rel.parts,
              'runtime manifest has an unsafe module path')
        _fail(rel.as_posix() not in paths, 'runtime manifest repeats a module')
        paths.add(rel.as_posix())
        module_path = runtime / rel
        _fail(module_path.is_file() and _file_sha(module_path) == item.get('sha256'),
              f'frozen runtime module digest mismatch: {rel.name}')
    _fail(_file_sha(wrapper) == EXPECTED_WRAPPER_SHA256,
          'capture wrapper digest differs from reviewed pin')
    _fail(config.get('manifest_sha256') == EXPECTED_RUNTIME_MANIFEST_SHA256
          and Path(config.get('runtime', '')).resolve() == runtime.resolve(),
          'wrapper runtime manifest/runtime path differs from reviewed pin')
    _fail(config.get('http_before') == EXPECTED_HTTP_BEFORE
          and config.get('http_cap') == EXPECTED_HTTP_CAP,
          'wrapper cumulative HTTP limits differ from reviewed pin')
    command = config.get('command')
    _fail(isinstance(command, list) and len(command) > 1,
          'wrapper command is missing')
    script = runtime / 'scripts/collect_work_video_radio.py'
    _fail(str(script) in command and '--execute' in command,
          'wrapper does not run the pinned collector with execution enabled')
    for flag, expected in (('--seed-limit', '44'),
                           ('--radio-candidate-limit', '100'),
                           ('--stage-http-limit', '200'), ('--interval', '3')):
        _fail(flag in command and command.index(flag) + 1 < len(command)
              and command[command.index(flag) + 1] == expected,
              f'wrapper command does not pin the reviewed limit: {flag}')
    _fail(config.get('wrapper_sha256') in (None, EXPECTED_WRAPPER_SHA256),
          'wrapper self-pin is malformed')
    return manifest


def _verify_wrapper_inputs(config: dict, root: Path) -> dict:
    pins = config.get('input_pins')
    _fail(isinstance(pins, dict), 'wrapper has no input hash pins')
    resolved = {}
    for raw_path, digest in pins.items():
        path = Path(raw_path)
        _fail(path.is_absolute(), 'wrapper input pin must use absolute paths')
        _fail(path.is_file() and _file_sha(path) == digest,
              f'wrapper-pinned source digest mismatch: {path.name}')
        resolved[str(path)] = digest
    required = {
        'root-metadata-recipe-review.json': EXPECTED_RECIPE_SHA256,
        'candidate-bindings.json': '7356f7efa579eb574a7df78b3d4ee1f080f0a79c9ceab2b32baf847d859aea62',
        'report.json': 'cbb4d07ccbd2ad8792a85eb7b1714370fafcd488e684a8776bb35859a50d7505',
        'hallab-official-page.html': '2e66073594e9f3c5d450ac4508d576f09da88cb369b66fafb26be309bb3193d0',
        'track-snapshot.json': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    }
    for name, digest in required.items():
        _fail(any(Path(path).name == name and value == digest
                  for path, value in resolved.items()),
              f'wrapper is missing reviewed source pin: {name}')
    _fail(Path(config.get('root', '')).resolve() == root.resolve(),
          'wrapper trusted root differs from supplied repository')
    return resolved


def _load_pinned_binder(runtime: Path):
    module_path = runtime / 'scripts/work_track_video_bindings.py'
    spec = importlib.util.spec_from_file_location(
        '_frozen_work_track_video_bindings_audit', module_path)
    _fail(spec is not None and spec.loader is not None,
          'cannot load pinned pure binding module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def adapt_reviewed_binder(binder, official, tracks):
    """Expose a separately pinned, explicit metadata recipe to raw-row replay.

    The original packet loader and single-row legacy matcher stay unchanged.
    A later recipe must itself reload its exact approved source packet.
    """
    if not hasattr(binder, 'load_reviewed_recipe_context'):
        return binder
    context = binder.load_reviewed_recipe_context(official, tracks)
    proxy = SimpleNamespace(**vars(binder))

    def check_inputs(given_official, given_tracks):
        _fail(given_official == official and given_tracks == tracks,
              'reviewed recipe context received different source metadata')

    def bind_rows(rows, given_official, given_tracks):
        check_inputs(given_official, given_tracks)
        return context.bind_rows(rows)

    def targets(rows, pid, given_official, given_tracks):
        check_inputs(given_official, given_tracks)
        return context.ordered_radio_targets(rows, pid)

    def playlist(path, given_official, given_tracks):
        check_inputs(given_official, given_tracks)
        _fail(Path(path).resolve() == Path(context.receipt['path']).resolve()
              and _file_sha(Path(path)) == context.receipt['sha256'],
              'reviewed recipe received a different playlist source')
        return list(context.frontier_bindings), dict(context.receipt)

    proxy.bind_rows = bind_rows
    proxy.ordered_radio_targets = targets
    proxy.load_frontier_playlist_cache = playlist
    return proxy


def _candidate_row(binding: dict) -> dict:
    return {
        'videoId': binding.get('video_id'), 'title': binding.get('remote_title'),
        'artists': binding.get('remote_artists', binding.get('uploader_artists')),
        'album': binding.get('remote_album', binding.get('remote_album_field')),
        'length': binding.get('remote_length', binding.get('remote_duration')),
    }


def _initial_bindings(doc: dict, official: dict, local_tracks: list[dict], binder):
    rows = doc.get('bindings')
    _fail(isinstance(rows, list), 'pinned source packet has no binding list')
    out = []
    pids, videos = set(), set()
    for original in rows:
        item = binder.bind_video_row(_candidate_row(original), official, local_tracks)
        _fail(item is not None, 'source packet row fails the pinned contextual recipe')
        _fail(item['persistent_id'] == original.get('local_track', {}).get('persistent_id')
              and item['video_id'] == original.get('video_id')
              and item['disc_track'] == original.get('disc_track'),
              'source packet identity tuple differs from recomputed binding')
        item['initial_candidate_response_position'] = original.get('response_position')
        item['initial_candidate_packet_only'] = True
        _fail(item['persistent_id'] not in pids and item['video_id'] not in videos,
              'source packet repeats a PID or video ID')
        pids.add(item['persistent_id']); videos.add(item['video_id'])
        out.append(item)
    return out


def _binding_signature(item: dict) -> tuple:
    return tuple(item.get(key) for key in (
        'persistent_id', 'video_id', 'disc_track', 'official_japanese_title',
        'local_title', 'local_artist', 'local_album', 'local_duration_ms',
        'remote_title', 'remote_artists', 'remote_album', 'remote_length',
        'duration_delta_ms'))


def _register(item: dict, pairs: dict, pids: dict, videos: dict):
    pid, video = item['persistent_id'], item['video_id']
    if pid in pids and video not in pids[pid]:
        pids[pid].add(video)
        videos[video].add(pid)
    else:
        pids.setdefault(pid, set()).add(video)
        videos.setdefault(video, set()).add(pid)
    pairs.setdefault((pid, video), item)


def _request_path(stage: Path, key: str) -> Path:
    _fail(isinstance(key, str) and re.fullmatch(r'[0-9a-f]{64}', key) is not None,
          'request cache key is malformed')
    path = stage / 'requests' / (key + '.json')
    _fail(path.is_file(), f'missing request cache receipt: {key[:12]}')
    return path


def _verify_request(stage: Path, key: str, digest: str, video_id: str,
                    radio: bool, limit: int, manifest_entries: dict) -> dict:
    path = _request_path(stage, key)
    raw = path.read_bytes()
    _fail(sha256(raw) == digest, 'request receipt digest differs from per-root reference')
    _fail(manifest_entries.get(path.name) == (digest, len(raw)),
          'request receipt is absent from the process cache manifest')
    request = json.loads(raw)
    _fail(set(request) == {'endpoint', 'arguments', 'observed_at', 'response'}
          and request.get('endpoint') == 'get_watch_playlist',
          'own-root request receipt is malformed or cached')
    expected_args = {'videoId': video_id, 'radio': radio, 'limit': limit}
    encoded = json.dumps(['get_watch_playlist', expected_args], ensure_ascii=False,
                         sort_keys=True).encode('utf-8')
    _fail(sha256(encoded) == key and request.get('arguments') == expected_args,
        'own-root request arguments do not bind the selected seed and limits')
    observed = request.get('observed_at')
    _fail(isinstance(observed, str), 'own-root request timestamp is missing')
    try:
        timestamp = datetime.fromisoformat(observed.replace('Z', '+00:00'))
    except ValueError as error:
        raise AuditError('own-root request timestamp is malformed') from error
    _fail(timestamp.tzinfo is not None and timestamp.utcoffset() is not None,
          'own-root request timestamp lacks timezone')
    return request


def _verify_stage_shell(stage: Path, supervisor: Path, graph: dict,
                        process: dict, progress: dict, terminal: dict,
                        supervisor_process: dict, invocation: dict,
                        config: dict, wrapper_sha: str,
                        allow_completed_child_without_supervisor_terminal: bool = False):
    _fail(process.get('status') == 'completed'
          and process.get('account_authentication_verified') is True,
          'stage process is not completed with authenticated account preflight')
    _fail(graph.get('status') == 'review_candidates_only'
          and graph.get('account_authentication_verified') is True,
          'stage graph is not an authenticated candidate-only result')
    _fail(progress.get('status') == 'completed_finalized_candidate_only'
          and progress.get('candidate_graphs') == [],
          'stage progress is not finalized candidate-only')
    if not allow_completed_child_without_supervisor_terminal:
        _fail(terminal is not None
              and terminal.get('status') == 'completed_candidates_only'
              and terminal.get('returncode') == 0,
              'supervisor terminal report is not a successful completed capture')
    _fail(invocation.get('wrapper_sha256') == wrapper_sha
          and supervisor_process.get('wrapper_sha256') == wrapper_sha
          and supervisor_process.get('manifest_sha256') == EXPECTED_RUNTIME_MANIFEST_SHA256
          and invocation.get('manifest_sha256') == EXPECTED_RUNTIME_MANIFEST_SHA256
          and invocation.get('command') == config.get('command')
          and invocation.get('input_pins') == config.get('input_pins')
          and invocation.get('worker_pid') == supervisor_process.get('wrapper_pid')
          and type(supervisor_process.get('child_pid')) is int
          and supervisor_process.get('child_pid') != supervisor_process.get('wrapper_pid'),
          'supervisor did not record the pinned wrapper/runtime')
    _fail(Path(config['stage']).resolve() == stage.resolve()
          and Path(config['supervisor']).resolve() == supervisor.resolve(),
          'stage/supervisor paths differ from pinned wrapper configuration')
    used = process.get('http_requests')
    _fail(type(used) is int and 0 <= used <= EXPECTED_STAGE_HTTP_MAX
          and EXPECTED_HTTP_BEFORE + used <= EXPECTED_HTTP_CAP,
          'actual HTTP counts exceed the reviewed stage budget')
    if not allow_completed_child_without_supervisor_terminal:
        _fail(terminal.get('http_requests') == used
              and terminal.get('cumulative_http') == EXPECTED_HTTP_BEFORE + used
              and terminal.get('cumulative_http') <= EXPECTED_HTTP_CAP
              and terminal.get('manifest_sha256') == EXPECTED_RUNTIME_MANIFEST_SHA256
              and terminal.get('accepted_root_credit') == 0
              and terminal.get('database_written') is False,
              'actual HTTP counts exceed or differ from the reviewed stage budget')
    graph_bytes = (stage / 'candidate-graphs.json').read_bytes()
    graph_sha = sha256(graph_bytes)
    _fail(process.get('candidate_graphs_sha256') == graph_sha
          and progress.get('candidate_graphs_sha256') == graph_sha,
          'candidate graph bytes are not bound by worker receipts')
    if not allow_completed_child_without_supervisor_terminal:
        _fail(terminal.get('candidate_graph_sha256') == graph_sha,
              'candidate graph bytes differ from supervisor terminal receipt')
        _fail(terminal.get('candidate_graph_count') == len(graph.get('candidate_graphs', [])),
              'supervisor candidate count differs from final graph')


def validate_stage_replay(stage: Path, *, graph: dict, process: dict,
                          doc: dict, official: dict, local_tracks: list[dict],
                          frontier_bindings: list[dict], skip_seed_pids: set[str],
                          base_inputs: dict, binder,
                          expected_seed_limit=44, expected_radio_limit=100) -> dict:
    """Replay cache rows and prove every emitted root and target from its own receipts."""
    _fail(graph.get('recording_identity_verified') in (None, False),
          'graph claims recording identity')
    _fail(graph.get('status') == 'review_candidates_only'
          and graph.get('account_authentication_verified') is True
          and process.get('account_authentication_verified') is True,
          'stage is unauthenticated or not candidate-only')
    _fail(process.get('status') == 'completed',
          'stage process is not in its completed terminal state')
    _fail(graph.get('policy') == binder.POLICY
          and graph.get('official_work', {}).get('label') == WORK_LABEL
          and graph.get('official_work', {}).get('local_album') == LOCAL_ALBUM,
          'graph work/policy identity differs from pinned recipe')
    _fail(graph.get('root_credit', 0) == 0 and graph.get('edge_credit', 0) == 0
          and graph.get('source_credit', 0) == 0,
          'graph claims recommendation credit')
    limits = graph.get('request_limits', {})
    _fail(limits.get('stage_http_max') == 200
          and limits.get('minimum_interval_seconds') == 3
          and limits.get('seed_max') == expected_seed_limit
          and limits.get('radio_candidate_limit_per_watch_radio') == expected_radio_limit
          and limits.get('watch_seed_limit') == 25
          and limits.get('retries') == 0,
          'graph request limits differ from the approved bounded profile')
    inputs = graph.get('inputs')
    _fail(isinstance(inputs, dict) and process.get('inputs') == inputs,
          'process and graph input hash receipts differ')
    for key, value in base_inputs.items():
        _fail(inputs.get(key) == value, f'graph input receipt differs: {key}')

    manifest_rows = process.get('request_cache_manifest')
    _fail(isinstance(manifest_rows, list), 'process lacks the request cache manifest')
    expected_manifest_hash = _canonical_sha(manifest_rows)
    _fail(process.get('request_cache_manifest_sha256') == expected_manifest_hash,
          'process request cache manifest digest is invalid')
    cache_dir = stage / 'requests'
    _fail(cache_dir.is_dir(), 'stage request cache directory is missing')
    actual_paths = sorted(cache_dir.glob('*.json'))
    listed = {}
    for row in manifest_rows:
        rel = Path(row.get('path', ''))
        _fail(not rel.is_absolute() and len(rel.parts) == 2 and rel.parts[0] == 'requests'
              and rel.suffix == '.json', 'request manifest contains an unsafe path')
        path = stage / rel
        raw = path.read_bytes()
        _fail(len(raw) == row.get('bytes') and sha256(raw) == row.get('sha256'),
              'request cache file differs from process manifest')
        _fail(rel.name not in listed, 'process request manifest has duplicate file')
        listed[rel.name] = (row['sha256'], len(raw))
    _fail(set(path.name for path in actual_paths) == set(listed),
          'request cache directory contains undeclared or missing receipts')

    events = process.get('request_events')
    http_count = process.get('http_requests')
    _fail(isinstance(events, list) and len(events) == http_count,
          'HTTP event ledger count differs from actual session count')
    _fail(type(http_count) is int and 0 <= http_count <= EXPECTED_STAGE_HTTP_MAX,
          'actual session HTTP count exceeds the stage cap')
    event_numbers = [event.get('request_number') for event in events]
    _fail(all(type(number) is int for number in event_numbers)
          and sorted(event_numbers) == list(range(1, http_count + 1))
          and all(event.get('status') == 'completed' for event in events),
          'HTTP event ledger is incomplete or includes failed requests')
    category_counts = dict(sorted(Counter(e.get('category') for e in events).items()))
    _fail(dict(sorted(process.get('http_request_categories', {}).items())) == category_counts,
          'process HTTP category summary does not replay from request events')
    preflight = [event for event in events if event.get('category') == 'account_preflight']
    _fail(preflight and all(event.get('status') == 'completed'
          and event.get('method') == 'POST'
          and event.get('path') == '/youtubei/v1/account/account_menu'
          and event.get('status_code') == 200 for event in preflight),
          'authenticated account-menu preflight lacks a successful HTTP receipt')

    # Recompute metadata bindings from the pinned initial packet and optional
    # untrusted continuation caches. Those caches seed discovery only.
    pairs, pids, videos = {}, defaultdict(set), defaultdict(set)
    for item in _initial_bindings(doc, official, local_tracks, binder):
        _register(item, pairs, pids, videos)
    for item in frontier_bindings:
        _register(item, pairs, pids, videos)

    results = graph.get('results')
    _fail(isinstance(results, list) and len(results) <= expected_seed_limit,
          'result count exceeds the seed cap')
    _fail(graph.get('summary', {}).get('seeds_attempted') == len(results),
          'claimed seed count differs from saved own-root results')
    per_result = []
    radios = []
    seen_pairs = set()
    radio_limit = limits['radio_candidate_limit_per_watch_radio']
    for result in results:
        pid, video_id = result.get('persistent_id'), result.get('seed_video_id')
        _fail(isinstance(pid, str) and isinstance(video_id, str),
              'own-root result lacks persistent ID or selected video')
        pair = (pid, video_id)
        _fail(pair not in seen_pairs, 'own seed PID/video pair is duplicated')
        seen_pairs.add(pair)
        _fail(pid not in skip_seed_pids, 'skip list PID was probed as a seed')
        seed_binding = pairs.get(pair)
        _fail(seed_binding is not None
              and _binding_signature(seed_binding) == _binding_signature(result.get('seed_binding', {})),
              'seed binding is not reachable from the approved packet or saved radio frontier')

        radio_doc = _verify_request(stage,
            result.get('watch_radio', {}).get('request_key'),
            result.get('watch_radio', {}).get('receipt_sha256'),
            video_id, True, radio_limit, listed)
        watch_doc = _verify_request(stage,
            result.get('watch_seed', {}).get('request_key'),
            result.get('watch_seed', {}).get('receipt_sha256'),
            video_id, False, 25, listed)
        radio_rows = radio_doc.get('response', {}).get('tracks')
        seed_rows = watch_doc.get('response', {}).get('tracks')
        _fail(isinstance(radio_rows, list)
              and len(radio_rows) == result['watch_radio'].get('returned_track_count'),
              'radio returned-row count differs from its raw receipt')
        _fail(isinstance(seed_rows, list)
              and len(seed_rows) == result['watch_seed'].get('returned_track_count'),
              'watch-seed returned-row count differs from its raw receipt')
        returned = [row for row in seed_rows if row.get('videoId') == video_id]
        returned_bindings, _ = binder.bind_rows(returned, official, local_tracks)
        seed_return_ok = (len(returned_bindings) == 1
                          and returned_bindings[0]['persistent_id'] == pid
                          and returned_bindings[0]['video_id'] == video_id
                          and _binding_signature(returned_bindings[0])
                          == _binding_signature(seed_binding))
        _fail(result['watch_seed'].get('same_video_returned') is bool(returned)
              and result['watch_seed'].get('same_binding_valid') is seed_return_ok,
              'watch-seed status does not replay from its returned same-video row')
        expected_seed_binding = returned_bindings[0] if seed_return_ok else None
        _fail(result['watch_seed'].get('same_video_binding') == expected_seed_binding,
              'watch-seed binding differs from its raw selected-video result')
        targets, target_counts = binder.ordered_radio_targets(
            radio_rows, pid, official, local_tracks)
        _fail(result['watch_radio'].get('binding_counts') == target_counts,
              'radio binding counters differ from recomputed raw response')
        per_result.append({
            'result': result, 'pair': pair, 'radio_doc': radio_doc,
            'targets': targets, 'seed_return_ok': seed_return_ok,
            'radio_rows': radio_rows, 'seed_binding': seed_binding,
        })
        radios.append(radio_rows)
        # The producer adds discovered pairs after processing each seed result.
        discovered, _ = binder.bind_rows(radio_rows, official, local_tracks)
        for item in discovered:
            _register(item, pairs, pids, videos)

    disqualified_pids = {pid for pid, vids in pids.items() if len(vids) > 1}
    disqualified_videos = {vid for vid, ps in videos.items() if len(ps) > 1}
    expected_graphs = []
    for item in per_result:
        result = item['result']
        pid, video_id = item['pair']
        seed_conflict = pid in disqualified_pids or video_id in disqualified_videos
        safe_targets = [target for target in item['targets']
                        if target['persistent_id'] not in disqualified_pids
                        and target['video_id'] not in disqualified_videos]
        expected_status = ('candidate_ready' if item['seed_return_ok'] and item['targets']
                           else 'seed_not_returned_or_binding_invalid'
                           if not item['seed_return_ok'] else 'no_nonempty_bound_radio_targets')
        if seed_conflict:
            expected_status = 'globally_ambiguous_video_pid_binding'
        elif expected_status == 'candidate_ready' and not safe_targets:
            expected_status = 'no_targets_after_global_ambiguity_filter'
        _fail(result.get('status') == expected_status
              and result.get('globally_unambiguous') is (not seed_conflict),
              'final per-seed status differs from independent global ambiguity replay')
        _fail(result['watch_radio'].get('target_bindings') == safe_targets
              and result['watch_radio'].get('ordered_target_pid_vector')
              == [target['persistent_id'] for target in safe_targets],
              'ordered target vector does not equal eligible rows returned in own radio response')
        for key in ('root_credit', 'edge_credit', 'source_credit'):
            _fail(result.get(key) == 0, 'per-result row claims graph/source credit')
        _fail(result.get('recording_identity_verified') is False,
              'per-result row claims recording identity')
        if expected_status == 'candidate_ready':
            expected_graphs.append({
                'persistent_id': pid, 'seed_video_id': video_id,
                'seed_binding': item['seed_binding'],
                'watch_radio_request_key': result['watch_radio']['request_key'],
                'watch_radio_receipt_sha256': result['watch_radio']['receipt_sha256'],
                'watch_seed_request_key': result['watch_seed']['request_key'],
                'watch_seed_receipt_sha256': result['watch_seed']['receipt_sha256'],
                'fresh_seed_return_binding': result['watch_seed']['same_video_binding'],
                'ordered_target_pids': [target['persistent_id'] for target in safe_targets],
                'targets': safe_targets, 'status': 'review_candidate_only',
                # Reviewed recipe roots carry their approved policy in the
                # exact seed binding; the graph-level policy remains the
                # position recipe used for the surrounding radio run.
                'policy': item['seed_binding'].get('policy', graph.get('policy')),
                'recording_identity_verified': False,
                'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            })

    actual_graphs = graph.get('candidate_graphs')
    _fail(isinstance(actual_graphs, list) and len(actual_graphs) == len(expected_graphs),
          'candidate graph count does not equal own eligible root count')
    for actual, expected in zip(actual_graphs, expected_graphs):
        # compare only signed/source-bearing fields; optional diagnostics do
        # not affect eligibility but must not replace the actual own receipt.
        for key, value in expected.items():
            _fail(actual.get(key) == value, f'candidate graph field fails replay: {key}')
    summary = graph.get('summary', {})
    _fail(summary.get('candidate_graphs_ready') == len(expected_graphs)
          and summary.get('metadata_candidate_pairs_seen') == len(pairs)
          and summary.get('skipped_seed_count') == sum(
              item['persistent_id'] in skip_seed_pids for item in pairs.values())
          and summary.get('http_requests') == http_count
          and summary.get('http_request_categories') == category_counts
          and summary.get('request_event_count') == len(events)
          and summary.get('request_cache_hits') == 0
          and summary.get('ambiguous_pids') == sorted(disqualified_pids)
          and summary.get('ambiguous_videos') == sorted(disqualified_videos)
          and summary.get('root_credit') == 0 and summary.get('edge_credit') == 0
          and summary.get('source_credit') == 0
          and summary.get('database_written') is False,
          'stage summary does not replay from actual receipts')
    _fail(category_counts.get('radio_page', 0) >= len(results)
          and category_counts.get('watch_seed_page', 0) >= len(results),
          'HTTP ledger lacks a live radio and seed-watch request for every result')
    _fail(len(list((stage / 'requests').glob('*.json'))) == 2 * len(results)
          and len(seen_pairs) == len(results),
          'stage contains extra/cached requests or a result lacks its own request pair')
    return {
        'validated_roots': len(expected_graphs),
        'validated_seed_pairs': len(results),
        'radio_rows_replayed': sum(len(rows) for rows in radios),
        'radio_rows_per_pair': [len(rows) for rows in radios],
        'new_candidate_graphs': expected_graphs,
        'ambiguous_pids': sorted(disqualified_pids),
        'ambiguous_videos': sorted(disqualified_videos),
        'http_requests': http_count,
        'request_cache_manifest_sha256': process.get('request_cache_manifest_sha256'),
    }


def run_audit(stage: Path, supervisor: Path, runtime: Path, wrapper: Path,
              root: Path, output: Path,
              allow_completed_child_without_supervisor_terminal: bool = False) -> dict:
    _fail(not output.exists(), 'audit output path must be new')
    wrapper_sha = _file_sha(wrapper)
    config = _wrapper_config(wrapper)
    _verify_frozen_runtime(runtime, wrapper, config)
    pins = _verify_wrapper_inputs(config, root)
    _fail(Path(config.get('stage', '')).resolve() == stage.resolve()
          and Path(config.get('supervisor', '')).resolve() == supervisor.resolve(),
          'requested stage paths do not match the sealed wrapper')

    supervisor_invocation = _load_json(supervisor / 'invocation.json')
    supervisor_process = _load_json(supervisor / 'process.json')
    terminal_path = supervisor / 'terminal.json'
    if allow_completed_child_without_supervisor_terminal:
        _fail(not terminal_path.exists(),
              'recovery mode requires the original supervisor terminal to be absent')
        terminal = None
    else:
        terminal = _load_json(terminal_path)
    stage_process = _load_json(stage / 'process.json')
    graph_path = stage / 'candidate-graphs.json'
    graph_bytes = graph_path.read_bytes()
    graph = json.loads(graph_bytes)
    progress = _load_json(stage / 'progress.json')
    _fail(supervisor_invocation.get('wrapper_sha256') == wrapper_sha,
          'supervisor invocation does not pin wrapper bytes')
    _verify_stage_shell(stage, supervisor, graph, stage_process, progress,
                        terminal, supervisor_process, supervisor_invocation,
                        config, wrapper_sha,
                        allow_completed_child_without_supervisor_terminal)

    binder = _load_pinned_binder(runtime)
    def argument_path(flag):
        command = config.get('command', [])
        _fail(flag in command and command.index(flag) + 1 < len(command),
              f'wrapper command lacks source argument {flag}')
        raw_path = command[command.index(flag) + 1]
        _fail(raw_path in pins, f'wrapper command source is not hash-pinned: {flag}')
        return Path(raw_path)

    bindings_path = argument_path('--bindings')
    report_path = argument_path('--binding-report')
    html_path = argument_path('--official-html')
    snapshot_path = argument_path('--track-snapshot')
    doc, report, official, tracks, base_inputs = binder.load_binding_inputs(
        bindings_path, report_path, html_path, snapshot_path, repo=root)
    _fail(report.get('status') == 'review_only_metadata_candidates'
          and report.get('summary', {}).get('root_credit') == 0,
          'parent source report is not a zero-credit review packet')
    binder = adapt_reviewed_binder(binder, official, tracks)
    inputs = graph.get('inputs', {})
    frontier_receipts = inputs.get('frontier_watch_cache_hints', [])
    _fail(isinstance(frontier_receipts, list) and len(frontier_receipts) <= 200,
          'frontier hint receipt list exceeds its bound')
    frontier = []
    for receipt in frontier_receipts:
        path = Path(receipt.get('path', ''))
        found, actual_receipt = binder.load_frontier_watch_cache(path, official, tracks)
        _fail(actual_receipt == receipt, 'frontier hint receipt differs from raw cache bytes')
        frontier.extend(found)
    playlist_receipts = inputs.get('frontier_playlist_cache_hints', [])
    _fail(isinstance(playlist_receipts, list) and len(playlist_receipts) <= 20,
          'playlist metadata hint receipt list exceeds its bound')
    for receipt in playlist_receipts:
        path = Path(receipt.get('path', ''))
        _fail(str(path) in pins and _file_sha(path) == pins[str(path)],
              'playlist metadata hint was not pinned before capture')
        found, actual_receipt = binder.load_frontier_playlist_cache(path, official, tracks)
        _fail(actual_receipt == receipt, 'playlist metadata hint differs from raw cache bytes')
        frontier.extend(found)
    skip_receipt = inputs.get('skip_seed_pids')
    if skip_receipt is None:
        skipped = set()
    else:
        skip_path = Path(skip_receipt.get('path', ''))
        skipped, actual_skip = binder.load_skip_seed_pids(skip_path, tracks)
        _fail(actual_skip == skip_receipt, 'skip PID receipt differs from raw list')
    replay = validate_stage_replay(stage, graph=graph, process=stage_process,
        doc=doc, official=official, local_tracks=tracks,
        frontier_bindings=frontier, skip_seed_pids=skipped,
        base_inputs=base_inputs, binder=binder,
        expected_seed_limit=EXPECTED_SEED_MAX,
        expected_radio_limit=EXPECTED_RADIO_LIMIT)
    recovery_evidence = None
    if allow_completed_child_without_supervisor_terminal:
        def pid_is_live(pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True

        wrapper_pid = supervisor_process.get('wrapper_pid')
        child_pid = supervisor_process.get('child_pid')
        _fail(type(wrapper_pid) is int and type(child_pid) is int
              and not pid_is_live(wrapper_pid) and not pid_is_live(child_pid),
              'capture wrapper or child PID is still live')
        recovery_evidence = {
            'kind': 'completed_child_supervisor_terminal_missing_recovery_v1',
            'original_supervisor_terminal_present': False,
            'original_terminal_missing_recorded_as_fact': True,
            'worker_status': stage_process.get('status'),
            'worker_account_authentication_verified': stage_process.get(
                'account_authentication_verified'),
            'completed_seed_pairs': progress.get('completed_seed_pairs'),
            'actual_http_requests': replay['http_requests'],
            'http_request_categories': stage_process.get('http_request_categories'),
            'http_cumulative': EXPECTED_HTTP_BEFORE + replay['http_requests'],
            'candidate_graph_sha256': sha256(graph_bytes),
            'candidate_graph_count': len(graph.get('candidate_graphs', [])),
            'request_cache_manifest_sha256': replay.get(
                'request_cache_manifest_sha256'),
            'validated_roots': replay['validated_roots'],
            'validated_seed_pairs': replay['validated_seed_pairs'],
            'ambiguous_pids': replay['ambiguous_pids'],
            'ambiguous_videos': replay['ambiguous_videos'],
            'wrapper_pid': wrapper_pid,
            'wrapper_pid_live_now': False,
            'child_pid': child_pid,
            'child_pid_live_now': False,
            'artifact_sha256': {
                'supervisor_invocation': _file_sha(supervisor / 'invocation.json'),
                'supervisor_process': _file_sha(supervisor / 'process.json'),
                'supervisor_stdout_stderr': _file_sha(supervisor / 'stdout-stderr.log'),
                'worker_process': _file_sha(stage / 'process.json'),
                'worker_progress': _file_sha(stage / 'progress.json'),
                'candidate_graph': _file_sha(graph_path),
                'wrapper': wrapper_sha,
            },
        }
    output.mkdir(parents=True)
    graph_receipt = {
        'schema_version': 1,
        'status': 'independently_validated_candidate_only',
        'producer': 'opengenius-independent-work-video-radio-audit-v1',
        'stage_graph_sha256': sha256(graph_bytes),
        'runtime_manifest_sha256': EXPECTED_RUNTIME_MANIFEST_SHA256,
        'capture_wrapper_sha256': wrapper_sha,
        'recipe_sha256': EXPECTED_RECIPE_SHA256,
        'input_pins': dict(sorted(pins.items())),
        'http_before': EXPECTED_HTTP_BEFORE,
        'http_used': replay['http_requests'],
        'http_cumulative': EXPECTED_HTTP_BEFORE + replay['http_requests'],
        'http_cap': EXPECTED_HTTP_CAP,
        'summary': {key: value for key, value in replay.items()
                    if key != 'new_candidate_graphs'},
        'recording_identity_verified': False,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'database_written': False,
    }
    if recovery_evidence is not None:
        graph_receipt['supervisor_recovery'] = recovery_evidence
    validated_graph = {
        'schema_version': 1,
        'status': 'validated_candidate_only',
        'source_stage_graph_sha256': sha256(graph_bytes),
        'policy': graph.get('policy'),
        'recording_identity_verified': False,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'candidate_graphs': replay['new_candidate_graphs'],
    }
    _atomic_json(output / 'validated-candidate-graphs.json', validated_graph)
    graph_receipt['validated_graph_sha256'] = _file_sha(
        output / 'validated-candidate-graphs.json')
    _atomic_json(output / 'audit-receipt.json', graph_receipt)
    return graph_receipt


def _atomic_json(path: Path, value: dict):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         indent=2).encode('utf-8') + b'\n'
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--supervisor', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--wrapper', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-completed-child-without-supervisor-terminal',
                        action='store_true',
                        help='audit finalized worker receipts when the original supervisor terminal is absent')
    args = parser.parse_args(argv)
    try:
        receipt = run_audit(args.stage, args.supervisor, args.runtime,
                            args.wrapper, args.root, args.output,
                            args.allow_completed_child_without_supervisor_terminal)
    except (AuditError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({'status': 'rejected', 'reason': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': receipt['status'],
                      'validated_roots': receipt['summary']['validated_roots'],
                      'http_used': receipt['http_used'],
                      'http_cumulative': receipt['http_cumulative'],
                      'output': str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
