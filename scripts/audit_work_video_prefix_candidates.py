"""Validate a completed prefix from an interrupted contextual-radio capture.

Unlike the completed-stage auditor, this tool never calls a partial capture a
finished stage. It replays the saved own-radio and own-seed receipts for each
complete pair, uses a later partial radio response only in the global
ambiguity registry, and emits uncredited prefix candidate artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

import audit_work_video_radio as audit


class PrefixAuditError(ValueError):
    pass


def _require(ok: bool, message: str):
    if not ok:
        raise PrefixAuditError(message)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _flag(command: list[str], name: str) -> Path:
    _require(name in command and command.index(name) + 1 < len(command),
             f'wrapper command lacks {name}')
    return Path(command[command.index(name) + 1])


def _profile(profile_path: Path, expected_sha256: str) -> dict:
    _require(_sha(profile_path) == expected_sha256, 'reviewed profile hash differs')
    profile = _load(profile_path)
    _require(profile.get('kind') == 'root-reviewed-work-radio-capture-v1'
             and profile.get('root_credit') == 0
             and profile.get('database_written') is False,
             'profile is not a reviewed, candidate-only capture')
    _require(profile.get('http_before') == 5130 and profile.get('http_cap') == 6500,
             'profile cumulative budget differs from the pinned v01 attempt')
    return profile


def _manifest(stage: Path, process: dict) -> dict[str, tuple[str, int]]:
    rows = process.get('request_cache_manifest')
    _require(isinstance(rows, list), 'process lacks its request cache manifest')
    _require(process.get('request_cache_manifest_sha256') == audit._canonical_sha(rows),
             'request cache manifest hash differs')
    cache_dir = stage / 'requests'
    _require(cache_dir.is_dir(), 'request cache directory is absent')
    entries: dict[str, tuple[str, int]] = {}
    for row in rows:
        rel = Path(row.get('path', ''))
        _require(not rel.is_absolute() and len(rel.parts) == 2
                 and rel.parts[0] == 'requests' and rel.suffix == '.json',
                 'request manifest contains an unsafe path')
        path = stage / rel
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        _require(len(raw) == row.get('bytes') and digest == row.get('sha256'),
                 'saved request bytes differ from manifest')
        _require(rel.name not in entries, 'request manifest repeats a cache path')
        entries[rel.name] = (digest, len(raw))
    actual = {path.name for path in cache_dir.glob('*.json')}
    _require(actual == set(entries), 'request directory differs from manifest')
    return entries


def _verify_events(process: dict, terminal: dict) -> dict:
    events = process.get('request_events')
    count = process.get('http_requests')
    _require(type(count) is int and count == 26 and isinstance(events, list)
             and len(events) == count,
             'failed-prefix HTTP count is not exactly 26')
    _require([event.get('request_number') for event in events] == list(range(1, 27)),
             'HTTP request event sequence is incomplete')
    _require(all(event.get('status') == 'completed'
                 and event.get('status_code') == 200 for event in events),
             'an HTTP event was not recorded as completed with status 200')
    counts = dict(sorted(Counter(event.get('category') for event in events).items()))
    _require(dict(sorted(process.get('http_request_categories', {}).items())) == counts,
             'HTTP category counts do not recompute')
    _require(counts == {
        'account_preflight': 1, 'radio_continuation': 12, 'radio_page': 6,
        'visitor_id_bootstrap': 1, 'watch_seed_page': 6,
    }, 'HTTP request categories differ from five full pairs plus one partial pair')
    account = [event for event in events if event.get('category') == 'account_preflight']
    _require(len(account) == 1 and account[0].get('method') == 'POST'
             and account[0].get('path') == '/youtubei/v1/account/account_menu'
             and account[0].get('status_code') == 200
             and process.get('account_authentication_verified') is True,
             'authenticated account preflight does not verify')
    _require(terminal.get('http_requests') == count
             and terminal.get('cumulative_http') == 5156
             and terminal.get('status') == 'failed'
             and terminal.get('returncode') == 1
             and terminal.get('accepted_root_credit') == 0
             and terminal.get('database_written') is False,
             'supervisor terminal does not bind the actual failed 26-request session')
    _require(process.get('error', {}).get('exception_type') == 'YTMusicServerError'
             and process.get('error', {}).get('location') == {
                 'file': 'watch.py', 'function': 'get_watch_playlist', 'line': 163},
             'partial pair failure differs from the observed YTMusic response error')
    return counts


def replay_prefix_for_composer(profile_path: Path, profile_sha256: str,
                               *, stage_override: Path | None = None):
    """Replay a prefix and expose raw bindings for a separate typed composer."""
    profile = _profile(profile_path, profile_sha256)
    root = Path(profile['root']).resolve()
    runtime = Path(profile['runtime']).resolve()
    wrapper = Path(profile['wrapper']).resolve()
    supervisor = Path(profile['supervisor']).resolve()
    stage = (stage_override or Path(profile['stage'])).resolve()

    config = audit._wrapper_config(wrapper)
    audit.EXPECTED_RUNTIME_MANIFEST_SHA256 = profile['runtime_manifest_sha256']
    audit.EXPECTED_WRAPPER_SHA256 = profile['wrapper_sha256']
    audit.EXPECTED_HTTP_BEFORE = profile['http_before']
    audit.EXPECTED_HTTP_CAP = profile['http_cap']
    audit.EXPECTED_STAGE_HTTP_MAX = 200
    audit._verify_frozen_runtime(runtime, wrapper, config)
    pins = audit._verify_wrapper_inputs(config, root)
    _require(profile.get('capture_input_pins') == config.get('input_pins'),
             'reviewed profile does not bind the wrapper input pins')
    _require(Path(config['stage']).resolve() == stage
             and Path(config['supervisor']).resolve() == supervisor,
             'profile and wrapper paths differ')

    invocation = _load(supervisor / 'invocation.json')
    sup_process = _load(supervisor / 'process.json')
    terminal = _load(supervisor / 'terminal.json')
    process = _load(stage / 'process.json')
    progress = _load(stage / 'progress.json')
    _require(invocation.get('wrapper_sha256') == profile['wrapper_sha256']
             and sup_process.get('wrapper_sha256') == profile['wrapper_sha256']
             and invocation.get('manifest_sha256') == profile['runtime_manifest_sha256']
             and sup_process.get('manifest_sha256') == profile['runtime_manifest_sha256']
             and invocation.get('input_pins') == config.get('input_pins')
             and invocation.get('command') == config.get('command')
             and type(sup_process.get('child_pid')) is int
             and sup_process.get('child_pid') != sup_process.get('wrapper_pid'),
             'supervisor invocation does not match frozen wrapper/runtime')
    _require(process.get('status') == 'stopped'
             and process.get('account_authentication_verified') is True
             and progress.get('status') == 'stopped_nonterminal_provisional'
             and progress.get('completed_seed_pairs') == 5
             and len(progress.get('results_so_far', [])) == 5
             and progress.get('root_credit') == 0
             and progress.get('edge_credit') == 0
             and progress.get('source_credit') == 0,
             'saved progress is not the expected five-pair interrupted prefix')
    _require(process.get('partial_persistent_id') == 'C25E18E3D7DEEA7D'
             and progress.get('terminal_error', {}).get('exception_type') == 'YTMusicServerError',
             'partial sixth seed/failure receipt differs from reviewed failure')
    counts = _verify_events(process, terminal)
    listed = _manifest(stage, process)

    command = config['command']
    binding_path = _flag(command, '--bindings')
    binding_report_path = _flag(command, '--binding-report')
    html_path = _flag(command, '--official-html')
    snapshot_path = _flag(command, '--track-snapshot')
    doc, binding_report, official, tracks, base_inputs = (
        audit._load_pinned_binder(runtime).load_binding_inputs(
            binding_path, binding_report_path, html_path, snapshot_path, repo=root))
    binder = audit._load_pinned_binder(runtime)
    frontier_path = _flag(command, '--frontier-playlist-cache')
    frontier, frontier_receipt = binder.load_frontier_playlist_cache(
        frontier_path, official, tracks)
    skip_path = _flag(command, '--skip-seed-pids')
    skip_pids, skip_receipt = binder.load_skip_seed_pids(skip_path, tracks)
    _require(len(skip_pids) == 914 and skip_receipt is not None,
             'prefix skip ledger differs from its reviewed 914-PID input')
    for key, value in base_inputs.items():
        _require(process.get('inputs', {}).get(key) == value,
                 f'process source input receipt differs: {key}')
    _require(process.get('inputs', {}).get('skip_seed_pids') == skip_receipt,
             'process skip ledger receipt differs from its exact file')
    saved_frontier = process.get('inputs', {}).get('frontier_playlist_cache_hints')
    _require(isinstance(saved_frontier, list) and len(saved_frontier) == 1,
             'process lacks the sole pinned playlist metadata hint')
    for key in ('path', 'sha256', 'request_key', 'endpoint', 'arguments',
                'observed_at', 'returned_track_count',
                'strict_metadata_candidate_count', 'metadata_hint_only',
                'credit', 'binding_counts'):
        _require(saved_frontier[0].get(key) == frontier_receipt.get(key),
                 f'process playlist-hint receipt differs: {key}')
    _require(saved_frontier[0].get('path') == str(frontier_path.resolve())
             and saved_frontier[0].get('authentication_carried_forward') is False,
             'playlist hint path/authentication marker differs')

    # Build an exact metadata registry from the reviewed packet and the saved
    # playlist hint, then resolve the sixth radio request by its actual videoId.
    pairs: dict[tuple[str, str], dict] = {}
    pid_videos: dict[str, set[str]] = defaultdict(set)
    video_pids: dict[str, set[str]] = defaultdict(set)

    def register(row: dict):
        pid, video = row.get('persistent_id'), row.get('video_id')
        _require(isinstance(pid, str) and isinstance(video, str),
                 'metadata binding is missing PID or video ID')
        pid_videos[pid].add(video)
        video_pids[video].add(pid)
        pair = (pid, video)
        old = pairs.get(pair)
        _require(old is None or audit._binding_signature(old) == audit._binding_signature(row),
                 'same PID/video pair has inconsistent metadata signatures')
        pairs.setdefault(pair, row)

    for row in audit._initial_bindings(doc, official, tracks, binder):
        register(row)
    for row in frontier:
        register(row)

    results = progress['results_so_far']
    _require([row.get('persistent_id') for row in results] == [
        '10BA44A3CB9930D4', '56B4A566DD45DFFE', '7B32B4423915965A',
        'B1CC6C880DC8160B', '781E34EABC6F6ACA'],
        'five saved prefix seed rows differ from the actual completed order')
    for result in results:
        pid, video = result.get('persistent_id'), result.get('seed_video_id')
        _require(pid not in skip_pids, 'a skipped PID was used as a seed')
        seed_binding = pairs.get((pid, video))
        _require(seed_binding is not None and audit._binding_signature(seed_binding)
                 == audit._binding_signature(result.get('seed_binding', {})),
                 'saved root seed is not reachable from the exact reviewed metadata')

    # Replay all six saved radio responses. The sixth is a partial attempt and
    # is never emitted as a seed/root, but contributes to global ambiguity.
    radio_docs = {}
    radio_rows_by_pid = {}
    for result in results:
        pid, video = result['persistent_id'], result['seed_video_id']
        radio = result.get('watch_radio', {})
        doc_radio = audit._verify_request(stage, radio.get('request_key'),
            radio.get('receipt_sha256'), video, True, 100, listed)
        rows = doc_radio.get('response', {}).get('tracks')
        _require(isinstance(rows, list) and len(rows) == radio.get('returned_track_count'),
                 'complete-pair radio row count differs from its raw cache')
        targets, target_counts = binder.ordered_radio_targets(rows, pid, official, tracks)
        _require(radio.get('binding_counts') == target_counts,
                 'complete-pair radio binding counts do not replay')
        radio_docs[radio['request_key']] = doc_radio
        radio_rows_by_pid[pid] = rows
        for binding in binder.bind_rows(rows, official, tracks)[0]:
            register(binding)

    partial_pid = process['partial_persistent_id']
    radio_entries = []
    for filename, (digest, _) in listed.items():
        path = stage / 'requests' / filename
        request = json.loads(path.read_bytes())
        if request.get('endpoint') == 'get_watch_playlist' and request.get('arguments', {}).get('radio') is True:
            radio_entries.append((filename[:-5], digest, request))
    _require(len(radio_entries) == 6,
             'saved prefix does not contain exactly six own-radio responses')
    referenced = {result['watch_radio']['request_key'] for result in results}
    partial_entries = [entry for entry in radio_entries if entry[0] not in referenced]
    _require(len(partial_entries) == 1,
             'cannot isolate the sixth partial radio response')
    partial_key, partial_sha, partial_cache = partial_entries[0]
    partial_video = partial_cache.get('arguments', {}).get('videoId')
    _require(partial_cache.get('arguments') == {
        'videoId': partial_video, 'radio': True, 'limit': 100},
        'partial radio request arguments differ from the reviewed profile')
    partial_radio = audit._verify_request(stage, partial_key, partial_sha,
        partial_video, True, 100, listed)
    partial_binding = pairs.get((partial_pid, partial_video))
    _require(partial_binding is not None,
             'partial PID/video is not supported by the metadata candidate registry')
    partial_rows = partial_radio.get('response', {}).get('tracks')
    _require(isinstance(partial_rows, list), 'partial radio cache has no track rows')
    for binding in binder.bind_rows(partial_rows, official, tracks)[0]:
        register(binding)

    # Recompute the producer's five-prefix result statuses (without the later
    # partial radio), then produce a stricter global vector across all six
    # actual radio responses. This prevents any late collision from restoring
    # an unsafe edge.
    prefix_registry_pids: dict[str, set[str]] = defaultdict(set)
    prefix_registry_vids: dict[str, set[str]] = defaultdict(set)
    for row in audit._initial_bindings(doc, official, tracks, binder) + frontier:
        prefix_registry_pids[row['persistent_id']].add(row['video_id'])
        prefix_registry_vids[row['video_id']].add(row['persistent_id'])
    complete_rows_all = []
    pair_evidence = []
    for result in results:
        pid, video = result['persistent_id'], result['seed_video_id']
        radio = result['watch_radio']
        radio_doc = radio_docs[radio['request_key']]
        radio_rows = radio_doc['response']['tracks']
        seed = result['watch_seed']
        seed_doc = audit._verify_request(stage, seed.get('request_key'),
            seed.get('receipt_sha256'), video, False, 25, listed)
        seed_rows = seed_doc.get('response', {}).get('tracks')
        _require(isinstance(seed_rows, list)
                 and len(seed_rows) == seed.get('returned_track_count'),
                 'watch-seed row count differs from its saved raw cache')
        returned = [row for row in seed_rows if row.get('videoId') == video]
        returned_bindings, _ = binder.bind_rows(returned, official, tracks)
        chosen = pairs[(pid, video)]
        seed_ok = (len(returned_bindings) == 1
                   and returned_bindings[0]['persistent_id'] == pid
                   and returned_bindings[0]['video_id'] == video
                   and audit._binding_signature(returned_bindings[0])
                   == audit._binding_signature(chosen))
        _require(seed.get('same_video_returned') is bool(returned)
                 and seed.get('same_binding_valid') is seed_ok
                 and seed.get('same_video_binding') == (returned_bindings[0] if seed_ok else None),
                 'watch-seed same-video proof differs from its raw cache')
        prefix_targets, prefix_counts = binder.ordered_radio_targets(
            radio_rows, pid, official, tracks)
        _require(radio.get('binding_counts') == prefix_counts,
                 'radio receipt counts differ from recomputation')
        for target in prefix_targets:
            prefix_registry_pids[target['persistent_id']].add(target['video_id'])
            prefix_registry_vids[target['video_id']].add(target['persistent_id'])
        complete_rows_all.append((result, chosen, radio_doc, seed_doc,
                                  radio_rows, prefix_targets, seed_ok))
        pair_evidence.append({
            'persistent_id': pid, 'seed_video_id': video,
            'watch_radio_request_key': radio['request_key'],
            'watch_radio_receipt_sha256': radio['receipt_sha256'],
            'watch_seed_request_key': seed['request_key'],
            'watch_seed_receipt_sha256': seed['receipt_sha256'],
            'candidate_status_at_prefix': result.get('status'),
            'seed_binding_verified': True, 'own_radio_verified': True,
            'own_watch_seed_verified': True,
        })

    prefix_ambig_pids = {pid for pid, vids in prefix_registry_pids.items() if len(vids) > 1}
    prefix_ambig_vids = {vid for vid, ps in prefix_registry_vids.items() if len(ps) > 1}
    final_ambig_pids = {pid for pid, vids in pid_videos.items() if len(vids) > 1}
    final_ambig_vids = {vid for vid, pids_set in video_pids.items() if len(pids_set) > 1}

    projection_graphs = []
    for result, seed_binding, radio_doc, seed_doc, radio_rows, prefix_targets, seed_ok in complete_rows_all:
        pid, video = result['persistent_id'], result['seed_video_id']
        prefix_seed_conflict = pid in prefix_ambig_pids or video in prefix_ambig_vids
        prefix_safe_targets = [target for target in prefix_targets
            if target['persistent_id'] not in prefix_ambig_pids
            and target['video_id'] not in prefix_ambig_vids]
        prefix_status = ('candidate_ready' if seed_ok and prefix_targets
                         else 'seed_not_returned_or_binding_invalid' if not seed_ok
                         else 'no_nonempty_bound_radio_targets')
        if prefix_seed_conflict:
            prefix_status = 'globally_ambiguous_video_pid_binding'
        elif prefix_status == 'candidate_ready' and not prefix_safe_targets:
            prefix_status = 'no_targets_after_global_ambiguity_filter'
        _require(result.get('status') == prefix_status,
                 'saved prefix result status does not replay from its five completed pairs')
        _require(result['watch_radio'].get('target_bindings') == prefix_safe_targets
                 and result['watch_radio'].get('ordered_target_pid_vector')
                 == [target['persistent_id'] for target in prefix_safe_targets],
                 'saved prefix ordered targets do not replay')
        for key in ('root_credit', 'edge_credit', 'source_credit'):
            _require(result.get(key) == 0,
                     'saved provisional result claims source or graph credit')
        _require(result.get('recording_identity_verified') is False,
                 'metadata-policy result claims recording identity')

        final_safe = [target for target in prefix_targets
            if target['persistent_id'] not in final_ambig_pids
            and target['video_id'] not in final_ambig_vids]
        if seed_ok and pid not in final_ambig_pids and video not in final_ambig_vids and final_safe:
            projection_graphs.append({
                'persistent_id': pid, 'seed_video_id': video,
                'seed_binding': seed_binding,
                'watch_radio_request_key': result['watch_radio']['request_key'],
                'watch_radio_receipt_sha256': result['watch_radio']['receipt_sha256'],
                'watch_seed_request_key': result['watch_seed']['request_key'],
                'watch_seed_receipt_sha256': result['watch_seed']['receipt_sha256'],
                'fresh_seed_return_binding': result['watch_seed']['same_video_binding'],
                'ordered_target_pids': [target['persistent_id'] for target in final_safe],
                'targets': final_safe, 'status': 'review_candidate_only',
                'policy': binder.POLICY, 'recording_identity_verified': False,
                'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            })

    source_hashes = {
        str(path.resolve()): _sha(path)
        for path in (profile_path, wrapper, runtime / 'runtime-manifest.json',
                     supervisor / 'invocation.json', supervisor / 'process.json',
                     supervisor / 'stdout-stderr.log',
                     supervisor / 'terminal.json', stage / 'process.json',
                     stage / 'progress.json')
    }
    source_hashes.update(pins)
    source_hashes[str(Path(__file__).resolve())] = _sha(Path(__file__))
    audit_module_path = Path(audit.__file__).resolve()
    source_hashes[str(audit_module_path)] = _sha(audit_module_path)
    for filename, (digest, _) in listed.items():
        source_hashes[str((stage / 'requests' / filename).resolve())] = digest
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-independent-work-video-prefix-audit-v1',
        'status': 'interrupted_prefix_validated_candidate_only',
        'reviewed_profile_sha256': profile_sha256,
        'wrapper_sha256': _sha(wrapper),
        'runtime_manifest_sha256': _sha(runtime / 'runtime-manifest.json'),
        'actual_terminal_status': terminal['status'],
        'actual_terminal_returncode': terminal['returncode'],
        'http_requests': 26,
        'cumulative_http': 5156,
        'http_event_categories': counts,
        'authenticated_account_preflight': True,
        'validated_complete_pairs': 5,
        'validated_candidate_graphs': len(projection_graphs),
        'partial_seed_persistent_id': partial_pid,
        'partial_radio_request_key': partial_key,
        'partial_radio_receipt_sha256': partial_sha,
        'partial_radio_in_global_ambiguity_registry': True,
        'partial_pair_root_credit': 0,
        'prefix_ambiguities': {'pids': sorted(prefix_ambig_pids), 'videos': sorted(prefix_ambig_vids)},
        'all_six_radio_ambiguities': {'pids': sorted(final_ambig_pids), 'videos': sorted(final_ambig_vids)},
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'database_written': False,
        'pair_evidence': pair_evidence,
        'source_hashes': dict(sorted(source_hashes.items())),
    }
    projection = {
        'schema_version': 1,
        'producer': 'opengenius-interrupted-work-video-prefix-candidate-projection-v1',
        'status': 'prefix_candidates_only_not_a_completed_stage',
        'source_prefix_status': receipt['status'],
        'candidate_graphs': projection_graphs,
        'candidate_graph_count': len(projection_graphs),
        'complete_pairs': 5,
        'partial_pair_included_only_for_global_ambiguity': True,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'recording_identity_verified': False,
        'database_written': False,
    }
    # Only the five fully authenticated own pairs become sources. Every one of
    # the six observed radio responses, including the partial pair's response,
    # contributes its strict metadata bindings to cross-stage ambiguity checks.
    sources = []
    for graph_row in projection_graphs:
        sources.append({
            'root_pid': graph_row['persistent_id'],
            'video_id': graph_row['seed_video_id'],
            'ordered_target_pids': list(graph_row['ordered_target_pids']),
            'watch_radio_request_key': graph_row['watch_radio_request_key'],
            'watch_radio_receipt_sha256': graph_row['watch_radio_receipt_sha256'],
            'watch_seed_request_key': graph_row['watch_seed_request_key'],
            'watch_seed_receipt_sha256': graph_row['watch_seed_receipt_sha256'],
            'prefix_source_status': receipt['status'],
        })
    composer_bindings = []
    for result in results:
        composer_bindings.append(result['seed_binding'])
        composer_bindings.extend(binder.bind_rows(
            radio_rows_by_pid[result['persistent_id']], official, tracks)[0])
    composer_bindings.append(partial_binding)
    composer_bindings.extend(binder.bind_rows(partial_rows, official, tracks)[0])
    batch = {
        'prefix_status': receipt['status'],
        'sources': sources,
        'bindings': composer_bindings,
        'pairs': [(item['persistent_id'], item['seed_video_id'])
                  for item in projection_graphs],
        'partial_pid': partial_pid,
        'partial_pair_has_source': False,
        'raw_radio_response_count': 6,
        'complete_own_pair_count': 5,
        'receipt_sha256': audit._canonical_sha(receipt),
        'projection_sha256': audit._canonical_sha(projection),
        'source_hashes': receipt['source_hashes'],
    }
    return receipt, projection, batch


def replay_prefix(profile_path: Path, profile_sha256: str,
                  *, stage_override: Path | None = None) -> tuple[dict, dict]:
    """Return an uncredited prefix projection and its source receipt."""
    receipt, projection, _ = replay_prefix_for_composer(
        profile_path, profile_sha256, stage_override=stage_override)
    return receipt, projection


def write_prefix(profile_path: Path, profile_sha256: str, output_dir: Path) -> dict:
    _require(not output_dir.exists(), 'prefix audit output directory must be new')
    receipt, projection = replay_prefix(profile_path, profile_sha256)
    output_dir.mkdir(parents=True)
    receipt_path = output_dir / 'prefix-audit-receipt.json'
    projection_path = output_dir / 'independently-validated-prefix-candidates.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    projection['source_prefix_receipt'] = {
        'path': str(receipt_path), 'sha256': _sha(receipt_path)}
    projection_path.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {
        'status': receipt['status'],
        'validated_complete_pairs': receipt['validated_complete_pairs'],
        'validated_candidate_graphs': receipt['validated_candidate_graphs'],
        'http_requests': receipt['http_requests'],
        'cumulative_http': receipt['cumulative_http'],
        'root_credit': 0,
        'receipt_path': str(receipt_path), 'receipt_sha256': _sha(receipt_path),
        'projection_path': str(projection_path), 'projection_sha256': _sha(projection_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-profile', required=True, type=Path)
    parser.add_argument('--profile-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(write_prefix(args.reviewed_profile, args.profile_sha256, args.output),
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
