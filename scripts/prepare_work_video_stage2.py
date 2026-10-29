"""Prepare or explicitly run stage 2 from a completed stage-1 receipt.

Preparation performs no network I/O. The run subcommand requires --execute and
rechecks the terminal, every parent receipt, the frozen runtime, and all saved
radio request bytes before creating a fresh collector output namespace.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01'
STAGE1 = CAMPAIGN / 'stage-0001'
SUPERVISOR1 = CAMPAIGN / 'stage-0001-supervisor-v02'
STAGE2 = CAMPAIGN / 'stage-0002'
SUPERVISOR2 = CAMPAIGN / 'stage-0002-supervisor-v01'
PLAN_DIR = CAMPAIGN / 'stage-0002-plan-v01'
RUNTIME_V02 = Path('/tmp/opengenius-work-video-capture-runtime-v02')
RUNTIME_V02_MANIFEST_SHA = '46f45b9a2b3fd3190467541dd5301cdeb5f31d9969025869f920b634f1bc94c1'
WRAPPER1_SHA = 'd292cccf6b3d929ad7746e90626155b6b68a80ba8c535ab7e8098610e94acae6'
HTTP_BEFORE_STAGE1 = 4937
HTTP_CAP = 6500
STAGE_HTTP_CAP = 200
SEED_CAP = 44
RADIO_LIMIT = 100
WATCH_SEED_LIMIT = 25
INTERVAL = 3


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def request_key(video_id: str, radio: bool, limit: int) -> str:
    # Match CachedClient's persisted key bytes exactly, including default JSON spaces.
    return sha_bytes(json.dumps(
        ['get_watch_playlist', {'videoId': video_id, 'radio': radio, 'limit': limit}],
        ensure_ascii=False, sort_keys=True).encode())


def verify_runtime(runtime: Path, expected_manifest_sha: str | None = None) -> dict:
    manifest_path = runtime / 'runtime-manifest.json'
    manifest_sha = sha(manifest_path)
    if expected_manifest_sha is not None and manifest_sha != expected_manifest_sha:
        raise ValueError('frozen capture runtime manifest changed')
    manifest = load(manifest_path)
    rows = manifest.get('modules')
    if not isinstance(rows, list) or len(rows) != 187:
        raise ValueError('frozen capture runtime module list differs')
    seen = set()
    for row in rows:
        rel = row.get('path')
        if not isinstance(rel, str) or rel in seen or Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError('invalid frozen runtime manifest entry')
        seen.add(rel)
        if sha(runtime / rel) != row.get('sha256'):
            raise ValueError(f'frozen runtime module changed: {rel}')
    return {'path': str(manifest_path), 'sha256': manifest_sha,
            'module_count': len(rows)}


def verify_stage1() -> tuple[dict, dict, dict, dict, dict]:
    terminal_path = SUPERVISOR1 / 'terminal.json'
    invocation_path = SUPERVISOR1 / 'invocation.json'
    process_path = STAGE1 / 'process.json'
    progress_path = STAGE1 / 'progress.json'
    graph_path = STAGE1 / 'candidate-graphs.json'
    required = [terminal_path, invocation_path, process_path, progress_path, graph_path]
    if any(not path.is_file() for path in required):
        raise ValueError('stage 1 has no complete terminal/output receipt yet')
    terminal, invocation = load(terminal_path), load(invocation_path)
    process, progress, graph = load(process_path), load(progress_path), load(graph_path)
    if terminal.get('status') != 'completed_candidates_only':
        raise ValueError('stage 1 terminal is not completed_candidates_only')
    if invocation.get('wrapper_sha256') != WRAPPER1_SHA:
        raise ValueError('stage 1 wrapper receipt is not the approved v02 wrapper')
    if invocation.get('manifest_sha256') != RUNTIME_V02_MANIFEST_SHA:
        raise ValueError('stage 1 used a different frozen runtime')
    if process.get('status') != 'completed' or process.get('account_authentication_verified') is not True:
        raise ValueError('stage 1 collector process did not complete authenticated preflight')
    if graph.get('account_authentication_verified') is not True:
        raise ValueError('stage 1 candidate document lacks successful account preflight')
    if terminal.get('candidate_graph_sha256') != sha(graph_path):
        raise ValueError('stage 1 terminal candidate graph seal differs')
    if process.get('candidate_graphs_sha256') != sha(graph_path):
        raise ValueError('stage 1 process candidate graph seal differs')
    used = terminal.get('http_requests')
    if (type(used) is not int or not 0 <= used <= STAGE_HTTP_CAP
            or process.get('http_requests') != used
            or graph.get('summary', {}).get('http_requests') != used):
        raise ValueError('stage 1 HTTP accounting differs across terminal documents')
    if terminal.get('cumulative_http') != HTTP_BEFORE_STAGE1 + used:
        raise ValueError('stage 1 cumulative HTTP accounting differs')
    if HTTP_BEFORE_STAGE1 + used + STAGE_HTTP_CAP > HTTP_CAP:
        raise ValueError('stage 2 full request allowance would exceed the campaign cap')
    results = graph.get('results')
    if (not isinstance(results, list) or len(results) != graph.get('summary', {}).get('seeds_attempted')
            or not results or len(results) > SEED_CAP):
        raise ValueError('stage 1 result list/count is incomplete or out of bounds')
    if progress.get('status') != 'completed_finalized_candidate_only':
        raise ValueError('stage 1 progress is not finalized candidate-only')
    if progress.get('candidate_graphs_sha256') != sha(graph_path):
        raise ValueError('stage 1 progress graph seal differs')
    cache_rows = process.get('request_cache_manifest')
    if (not isinstance(cache_rows, list)
            or process.get('request_cache_manifest_sha256') != sha_bytes(canonical(cache_rows))
            or len(cache_rows) != 2 * len(results)):
        raise ValueError('stage 1 request-cache manifest is missing or inconsistent')
    seen_cache_paths = set()
    for item in cache_rows:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'bytes'}:
            raise ValueError('malformed stage-1 request-cache manifest row')
        relative = Path(item['path'])
        if (relative.is_absolute() or relative.parts[:1] != ('requests',)
                or len(relative.parts) != 2 or '..' in relative.parts
                or item['path'] in seen_cache_paths):
            raise ValueError('unsafe or duplicate stage-1 request-cache path')
        seen_cache_paths.add(item['path'])
        request_path = STAGE1 / relative
        if (not request_path.is_file() or request_path.stat().st_size != item['bytes']
                or sha(request_path) != item['sha256']):
            raise ValueError('stage-1 request-cache member differs from sealed manifest')
    return terminal, invocation, process, graph, progress


def collect_inputs(terminal, invocation, process, graph, progress) -> tuple[dict, list[str], list[str]]:
    pins = dict(invocation.get('input_pins', {}))
    if not pins:
        raise ValueError('stage 1 invocation has no source input pins')
    for path_text, digest in pins.items():
        path = Path(path_text)
        if not path.is_file() or sha(path) != digest:
            raise ValueError(f'stage 1 source input changed: {path_text}')
    artifact_paths = [
        SUPERVISOR1 / 'invocation.json', SUPERVISOR1 / 'process.json',
        SUPERVISOR1 / 'terminal.json', STAGE1 / 'process.json',
        STAGE1 / 'progress.json', STAGE1 / 'candidate-graphs.json',
        RUNTIME_V02 / 'runtime-manifest.json', Path(__file__).resolve(),
    ]
    manifest = load(RUNTIME_V02 / 'runtime-manifest.json')
    artifact_paths.extend(RUNTIME_V02 / row['path'] for row in manifest['modules'])
    attempted = []
    frontier_paths = []
    seen_pids = set()
    seen_keys = set()
    cache_manifest = process.get('request_cache_manifest', [])
    cache_path_by_name = {row['path'].split('/', 1)[1]: STAGE1 / row['path']
                          for row in cache_manifest}
    artifact_paths.extend(STAGE1 / row['path'] for row in cache_manifest)
    for result in graph['results']:
        pid = result.get('persistent_id')
        video_id = result.get('seed_video_id')
        if not isinstance(pid, str) or pid in seen_pids:
            raise ValueError('stage 1 attempted PID list is missing or duplicated')
        seen_pids.add(pid)
        attempted.append(pid)
        for kind, radio, limit in (('watch_radio', True, RADIO_LIMIT),
                                   ('watch_seed', False, WATCH_SEED_LIMIT)):
            receipt = result.get(kind, {})
            key = receipt.get('request_key')
            receipt_sha = receipt.get('receipt_sha256')
            if (not isinstance(key, str) or key in seen_keys
                    or key != request_key(video_id, radio, limit)):
                raise ValueError(f'stage 1 {kind} request key is duplicated or malformed')
            seen_keys.add(key)
            path = cache_path_by_name.get(key + '.json')
            if path is None or not path.is_file() or sha(path) != receipt_sha:
                raise ValueError(f'stage 1 {kind} bytes differ from candidate receipt')
            request = load(path)
            args = request.get('arguments')
            if (request.get('endpoint') != 'get_watch_playlist'
                    or not isinstance(args, dict) or args.get('videoId') != video_id
                    or args.get('radio') is not radio or args.get('limit') != limit
                    or not isinstance(request.get('response', {}).get('tracks'), list)):
                raise ValueError(f'stage 1 {kind} cache arguments/payload differ')
            if radio:
                frontier_paths.append(str(path.resolve()))
    if len(frontier_paths) != len(graph['results']):
        raise ValueError('stage 1 radio cache count does not match completed seed pairs')
    for path, digest in pins.items():
        artifact_paths.append(Path(path))
    # Canonical set of all parent and source artifacts, retaining exact paths.
    artifact_pins = {}
    for path in artifact_paths:
        resolved = path.resolve()
        artifact_pins[str(resolved)] = sha(resolved)
    return artifact_pins, attempted, frontier_paths


def prepare() -> Path:
    verify_runtime(RUNTIME_V02, RUNTIME_V02_MANIFEST_SHA)
    terminal, invocation, process, graph, progress = verify_stage1()
    runtime = verify_runtime(RUNTIME_V02, RUNTIME_V02_MANIFEST_SHA)
    parent_pins, attempted, frontier_paths = collect_inputs(
        terminal, invocation, process, graph, progress)
    if PLAN_DIR.exists() or STAGE2.exists() or SUPERVISOR2.exists():
        raise ValueError('stage-2 namespace already exists; refusing reuse')
    max_seed_pairs = SEED_CAP
    PLAN_DIR.mkdir(parents=True)
    skip_path = PLAN_DIR / 'skip-seed-pids.json'
    skip_bytes = json.dumps(attempted, ensure_ascii=False, indent=2).encode() + b'\n'
    with skip_path.open('xb') as stream:
        stream.write(skip_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    parent_pins[str(skip_path.resolve())] = sha(skip_path)
    command = [str(ROOT / '.venv/bin/python'), str(RUNTIME_V02 / 'scripts/collect_work_video_radio.py'),
               '--bindings', str(ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01/candidate-bindings.json'),
               '--binding-report', str(ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01/report.json'),
               '--official-html', str(ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01/hallab-official-page.html'),
               '--track-snapshot', str(ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'),
               '--auth', str(ROOT / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'),
               '--output', str(STAGE2), '--seed-limit', str(max_seed_pairs),
               '--radio-candidate-limit', str(RADIO_LIMIT), '--stage-http-limit', str(STAGE_HTTP_CAP),
               '--interval', str(INTERVAL), '--skip-seed-pids', str(skip_path)]
    for path in frontier_paths:
        command.extend(['--frontier-watch-cache', path])
    command.append('--execute')
    frontier_cache_rows = []
    for path_text in frontier_paths:
        cache_path = Path(path_text)
        cache_doc = load(cache_path)
        tracks = cache_doc['response']['tracks']
        frontier_cache_rows.append({
            'path': str(cache_path), 'sha256': sha(cache_path),
            'requested_limit': cache_doc['arguments']['limit'],
            'actual_returned_rows': len(tracks),
            'metadata_hint_only': True, 'credit': 0,
        })
    plan = {
        'schema_version': 1,
        'producer': 'opengenius-work-video-radio-stage2-continuation-plan-v1',
        'status': 'prepared_no_network_not_launched',
        'parent_stage': str(STAGE1), 'parent_terminal': str(SUPERVISOR1 / 'terminal.json'),
        'parent_terminal_sha256': sha(SUPERVISOR1 / 'terminal.json'),
        'parent_candidate_graph': str(STAGE1 / 'candidate-graphs.json'),
        'parent_candidate_graph_sha256': sha(STAGE1 / 'candidate-graphs.json'),
        'parent_runtime': runtime,
        'parent_wrapper_sha256': WRAPPER1_SHA,
        'source_and_parent_input_pins': parent_pins,
        'source_and_parent_input_pin_count': len(parent_pins),
        'frontier_radio_cache_paths': frontier_paths,
        'frontier_radio_cache_count': len(frontier_paths),
        'frontier_radio_cache_receipts': frontier_cache_rows,
        'frontier_semantics': 'saved stage-1 radio responses are metadata hints only; each stage-2 seed requires its own fresh authenticated radio and watch_seed requests',
        'skip_seed_pids': {'path': str(skip_path), 'sha256': sha(skip_path),
                           'count': len(attempted), 'pids': attempted,
                           'applies_to': 'seed selection only; these PIDs remain valid targets'},
        'http_accounting': {
            'campaign_http_before_stage1': HTTP_BEFORE_STAGE1,
            'stage1_actual_http': terminal['http_requests'],
            'cumulative_before_stage2': HTTP_BEFORE_STAGE1 + terminal['http_requests'],
            'stage2_http_max': STAGE_HTTP_CAP,
            'cumulative_max_after_stage2': HTTP_BEFORE_STAGE1 + terminal['http_requests'] + STAGE_HTTP_CAP,
            'campaign_http_cap': HTTP_CAP},
        'limits': {'seed_max': max_seed_pairs, 'radio_candidate_limit_per_request': RADIO_LIMIT,
                   'watch_seed_limit': WATCH_SEED_LIMIT, 'minimum_interval_seconds': INTERVAL,
                   'stage_http_max': STAGE_HTTP_CAP, 'retries': 0},
        'stage2_output': str(STAGE2), 'stage2_supervisor': str(SUPERVISOR2),
        'command': command,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'database_written': False,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'preparer_path': str(Path(__file__).resolve()),
        'preparer_sha256': sha(Path(__file__).resolve()),
    }
    plan_path = PLAN_DIR / 'plan.json'
    with plan_path.open('xb') as stream:
        stream.write(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    return plan_path


def run(plan_path: Path, expected_plan_sha256: str) -> int:
    if not plan_path.is_file():
        raise ValueError('stage-2 plan file is missing')
    plan = load(plan_path)
    if plan.get('status') != 'prepared_no_network_not_launched':
        raise ValueError('stage-2 plan status is not launchable')
    if plan.get('preparer_sha256') != sha(Path(__file__).resolve()):
        raise ValueError('stage-2 plan was prepared by different script bytes')
    if sha(plan_path) != expected_plan_sha256:
        raise ValueError('stage-2 plan differs from the reviewed digest')
    runtime = plan.get('parent_runtime', {})
    runtime_path = Path(runtime.get('path', ''))
    verify_runtime(runtime_path, runtime.get('sha256'))
    for path_text, digest in plan.get('source_and_parent_input_pins', {}).items():
        path = Path(path_text)
        if not path.is_file() or sha(path) != digest:
            raise ValueError(f'stage-2 parent/source input changed: {path_text}')
    # Re-run every stage-1 terminal guard immediately before the child process.
    verify_stage1()
    if STAGE2.exists() or SUPERVISOR2.exists():
        raise ValueError('stage-2 output or supervisor already exists; refusing restart')
    command = plan.get('command')
    if not isinstance(command, list) or '--execute' not in command:
        raise ValueError('stage-2 command is malformed or lacks explicit execute flag')
    actual_before = plan['http_accounting']['cumulative_before_stage2']
    if actual_before + STAGE_HTTP_CAP > HTTP_CAP:
        raise ValueError('stage-2 HTTP allowance exceeds campaign cap')
    SUPERVISOR2.mkdir(parents=True)
    started = time.time()
    invocation = {'plan_path': str(plan_path.resolve()), 'plan_sha256': sha(plan_path),
                  'preparer_path': str(Path(__file__).resolve()),
                  'preparer_sha256': sha(Path(__file__).resolve()),
                  'runtime_manifest_sha256': runtime['sha256'],
                  'source_and_parent_input_pins': plan['source_and_parent_input_pins'],
                  'command': command, 'http_before': actual_before,
                  'stage_http_max': STAGE_HTTP_CAP, 'http_cap': HTTP_CAP,
                  'worker_pid': os.getpid(), 'started_unix': started,
                  'accepted_root_credit': 0, 'database_written': False}
    invocation_path = SUPERVISOR2 / 'invocation.json'
    invocation_path.write_text(json.dumps(invocation, ensure_ascii=False, indent=2) + '\n')
    outcome = {'status': 'failed', 'root_credit': 0, 'edge_credit': 0,
               'source_credit': 0, 'database_written': False}
    env = dict(os.environ)
    env['OPENGENIUS_TRUSTED_DATA_ROOT'] = str(ROOT)
    try:
        with (SUPERVISOR2 / 'stdout-stderr.log').open('xb') as log:
            child = subprocess.Popen(command, cwd=ROOT, env=env,
                                     stdout=log, stderr=subprocess.STDOUT)
            (SUPERVISOR2 / 'process.json').write_text(json.dumps({
                'wrapper_pid': os.getpid(), 'child_pid': child.pid,
                'started_unix': started, 'plan_sha256': sha(plan_path)}, indent=2) + '\n')
            rc = child.wait()
        process = load(STAGE2 / 'process.json')
        process_path = STAGE2 / 'process.json'
        used = process.get('http_requests')
        if type(used) is not int or not 0 <= used <= STAGE_HTTP_CAP:
            raise ValueError('stage-2 collector HTTP count outside bound')
        outcome.update(returncode=rc, http_requests=used,
                       cumulative_http=actual_before + used,
                       collector_process_sha256=sha(process_path),
                       process_status=process.get('status'),
                       request_cache_manifest_sha256=process.get('request_cache_manifest_sha256'))
        if actual_before + used > HTTP_CAP:
            raise ValueError('actual stage-2 requests exceeded cumulative HTTP cap')
        if rc == 0 and process.get('status') == 'completed':
            graph_path = STAGE2 / 'candidate-graphs.json'
            graph = load(graph_path)
            if graph.get('account_authentication_verified') is not True:
                raise ValueError('stage-2 collector lacks authenticated account preflight')
            outcome.update(status='completed_candidates_only',
                           candidate_graph_sha256=sha(graph_path),
                           candidate_graph_count=len(graph.get('candidate_graphs', [])))
    except Exception as error:
        outcome.update(error_type=type(error).__name__)
    finally:
        outcome.update(finished_unix=time.time(), elapsed_seconds=time.time() - started,
                       plan_sha256=sha(plan_path), runtime_manifest_sha256=runtime['sha256'])
        log_path = SUPERVISOR2 / 'stdout-stderr.log'
        if log_path.is_file():
            outcome['stdout_stderr_sha256'] = sha(log_path)
        (SUPERVISOR2 / 'terminal.json').write_text(
            json.dumps(outcome, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get('status') == 'completed_candidates_only' else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('prepare', help='No-network; only after stage 1 has a successful terminal')
    run_parser = sub.add_parser('run', help='Launch the prepared stage after explicit review')
    run_parser.add_argument('--plan', type=Path, required=True)
    run_parser.add_argument('--plan-sha256', required=True)
    run_parser.add_argument('--execute', action='store_true', help='Required for network collection')
    args = parser.parse_args(argv)
    if args.mode == 'prepare':
        try:
            plan_path = prepare()
        except ValueError as error:
            print(json.dumps({'status': 'not_prepared', 'reason': str(error),
                              'network_requests': 0}, ensure_ascii=False))
            return 2
        print(json.dumps({'status': 'prepared_no_network_not_launched',
                          'plan': str(plan_path), 'plan_sha256': sha(plan_path),
                          'network_requests': 0}, ensure_ascii=False))
        return 0
    if not args.execute:
        parser.error('run requires --execute; prepare never performs network requests')
    return run(args.plan, args.plan_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
