"""Run the v06 source-union producer and frozen normal consumer in one process."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v06-c6-cache-rematch-v01')
V10 = Path('/tmp/opengenius-final-campaign-runtime-v10-native-index-compat-v01')
MANIFEST_SHA256 = 'd2e005f3b711b2bc20877b9e5353d00c3af6ff4e2aa67f1bcdaf10ecddc7584f'
MERGE_SHA256 = '445d37d72ba65e07c0b7bdf4eafacfa897afe3d45405cf9831012723970f24ad'
ADAPTER_SHA256 = '3ec7bfb934551301f2bcf412e926a79f995e56e616d48954eef4f801335a36dc'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    os.replace(temp, path)


def load_pinned(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load pinned module: ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != path.resolve():
        raise RuntimeError('Pinned module resolved to an unexpected path: ' + name)
    return module


def verify_runtime():
    manifest_path = RUNTIME / 'runtime-manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    if sha(manifest_bytes) != MANIFEST_SHA256:
        raise ValueError('v06 runtime manifest differs from this runner pin')
    manifest = json.loads(manifest_bytes)
    if manifest.get('code_root') != str(RUNTIME):
        raise ValueError('v06 runtime manifest code_root is not canonical')
    rows = manifest.get('modules')
    if not isinstance(rows, list) or len(rows) != 175:
        raise ValueError('v06 runtime manifest module inventory changed')
    for row in rows:
        module_path = (RUNTIME / row['path']).resolve()
        if module_path != RUNTIME / row['path'] or sha(module_path.read_bytes()) != row['sha256']:
            raise ValueError('A v06 runtime module differs from its manifest: ' + row['path'])
    merge_path = RUNTIME / 'scripts/merge_rebased_radio_growth.py'
    adapter_path = RUNTIME / 'scripts/project_radio_source_union_v05.py'
    if (sha(merge_path.read_bytes()) != MERGE_SHA256
            or sha(adapter_path.read_bytes()) != ADAPTER_SHA256):
        raise ValueError('v06 merge or adapter code differs from this runner pin')
    return manifest


def run(candidate: Path, run_dir: Path):
    started = time.time()
    os.environ['OPENGENIUS_SOURCE_UNION_RUN_STARTED_EPOCH'] = str(started)
    os.environ['OPENGENIUS_SOURCE_UNION_PHASE_PATH'] = str(run_dir / 'progress.json')
    run_dir.mkdir(parents=True, exist_ok=False)
    atomic_json(run_dir / 'process.json', {
        'pid': os.getpid(), 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'candidate_dir': str(candidate.resolve()), 'run_dir': str(run_dir.resolve()),
        'runtime': str(RUNTIME), 'manifest_sha256': MANIFEST_SHA256,
        'adapter_sha256': ADAPTER_SHA256, 'merge_sha256': MERGE_SHA256,
        'runner': {'path': str(Path(__file__).resolve()),
                   'sha256': sha(Path(__file__).read_bytes())},
    })
    atomic_json(run_dir / 'progress.json', {
        'stage': 'runtime_integrity_started', 'elapsed_seconds': 0.0,
        'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    candidate = candidate.resolve()
    if candidate.exists():
        raise FileExistsError('Candidate output path must not already exist')
    manifest = verify_runtime()
    trusted_data_root = str(Path(manifest['trusted_data_root']).resolve(strict=True))
    os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = trusted_data_root
    v10_scripts = str((V10 / 'scripts').resolve())
    v11_scripts = str((RUNTIME / 'scripts').resolve())
    sys.path = [p for p in sys.path if p not in (v10_scripts, v11_scripts)]
    sys.path.insert(0, v10_scripts)
    sys.path.insert(1, v11_scripts)

    atomic_json(run_dir / 'progress.json', {
        'stage': 'trusted_imports_started', 'elapsed_seconds': time.time() - started,
        'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    merger = load_pinned('merge_rebased_radio_growth',
                         RUNTIME / 'scripts/merge_rebased_radio_growth.py')
    adapter = load_pinned('_source_union_runner_adapter_v06',
                          RUNTIME / 'scripts/project_radio_source_union_v05.py')
    adapter._ACTIVE_MERGER = merger
    proof = __import__('radio_snapshot_proof')
    selector = __import__('seed_video_evidence')
    identity = __import__('music_identity_map')
    module_ids_before = {
        name: id(sys.modules[name]) for name in (
            'merge_rebased_radio_growth', 'radio_snapshot_proof',
            'seed_video_evidence', 'music_identity_map', 'plan_genius_update',
            'update_genius_bundle')
    }

    proof_stats = selector_stats = pass_stats = None
    atomic_json(run_dir / 'progress.json', {
        'stage': 'shared_proof_cache_scopes_started', 'elapsed_seconds': time.time() - started,
        'trusted_module_ids': module_ids_before,
        'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    with proof.radio_snapshot_proof_cache_scope() as proof_stats, \
            selector.seed_video_selector_cache_scope() as selector_stats, \
            proof.radio_snapshot_proof_validation_pass() as pass_stats:
        # Builder and normal dispatcher must share these exact cache-owning modules.
        atomic_json(run_dir / 'progress.json', {
            'stage': 'builder_started', 'elapsed_seconds': time.time() - started,
            'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        build_receipt = adapter.create_projection_candidate(candidate)
        accepted_path = candidate / 'accepted-graphs.json'
        accepted = json.loads(accepted_path.read_bytes())
        baseline_state = json.loads((candidate / 'state.json').read_bytes())
        native_state = json.loads((adapter.NATIVE_BASELINE / 'baseline-state.json').read_bytes())
        native_graph = json.loads((adapter.NATIVE_BASELINE / 'accepted-baseline-graphs.json').read_bytes())
        native_report = json.loads((adapter.NATIVE_BASELINE / 'baseline-rebase-report.json').read_bytes())
        identity_hash = sha(adapter.IDENTITY_MAP.read_bytes())
        tracks, library_hash, _ = identity.load_library(track_snapshot=adapter.TRACK_SNAPSHOT)
        matcher = identity.IdentityMap(json.loads(adapter.IDENTITY_MAP.read_bytes()), tracks, library_hash)
        atomic_json(run_dir / 'progress.json', {
            'stage': 'normal_dispatch_replay_started', 'elapsed_seconds': time.time() - started,
            'builder_output_hashes': build_receipt['output_hashes'],
            'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        actual_native = merger._verify_rebase_receipt(
            adapter.NATIVE_BASELINE / 'accepted-baseline-graphs.json',
            adapter.NATIVE_BASELINE / 'baseline-rebase-report.json',
            adapter.NATIVE_BASELINE / 'baseline-state.json', library_hash, identity_hash)
        actual_graph, actual_report, actual_state, baseline_provenance = actual_native
        if (actual_graph != native_graph or actual_report != native_report
                or actual_state != native_state
                or accepted.get('baseline_state_sha256') != actual_state.get('state_sha256')):
            raise ValueError('Current native proof result differs from builder and pinned baseline docs')
        dispatch_result = merger._replay_previous_growth(
            candidate, actual_state, actual_graph['accepted_roots'], baseline_provenance,
            matcher, library_hash, identity_hash)
        module_ids_after = {name: id(sys.modules[name]) for name in module_ids_before}
        if module_ids_after != module_ids_before:
            raise ValueError('Trusted module objects changed during shared-cache builder/dispatch')
        (state, roots, runs, accepted_hash) = dispatch_result
        canonical_json = lambda value: json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if (accepted_hash != build_receipt['output_hashes'].get('accepted-graphs.json')
                or roots != accepted.get('accepted_roots')
                or runs != accepted.get('growth_runs')
                or canonical_json(state) != canonical_json(baseline_state)
                or state.get('state_sha256') != baseline_state.get('state_sha256')):
            raise ValueError('Normal dispatcher outputs differ from exact producer graph, runs, or state docs')
        if (len([row for row in roots if row['ordered_target_pids']]) != 768
                or sum(len(row['ordered_target_pids']) for row in roots) != 18047
                or len(runs) != 2):
            raise ValueError('Normal dispatcher returned unexpected source-union counts')

    consumer = {
        'status': 'normal_dispatch_consumer_pass',
        'producer': 'opengenius-radio-native-source-union-checkpoint-v1',
        'candidate_dir': str(candidate),
        'accepted_graph_sha256': accepted_hash,
        'state_sha256': state.get('state_sha256'),
        'accepted_run_count': len(runs),
        'nonempty_root_count': sum(bool(row['ordered_target_pids']) for row in roots),
        'ordered_edge_count': sum(len(row['ordered_target_pids']) for row in roots),
        'shared_proof_cache_stats': dict(proof_stats),
        'shared_selector_cache_stats': dict(selector_stats),
        'validation_pass_stats': {
            'closures_count': len(pass_stats.get('closures', {})),
            'keys_count': len(pass_stats.get('keys', ())),
            'nested': pass_stats.get('nested', 0),
        },
        'trusted_module_ids': module_ids_after,
        'manifest_sha256': MANIFEST_SHA256,
        'adapter_sha256': ADAPTER_SHA256,
        'merge_sha256': MERGE_SHA256,
        'runner': {'path': str(Path(__file__).resolve()),
                   'sha256': sha(Path(__file__).read_bytes())},
        'elapsed_seconds': time.time() - started,
    }
    atomic_json(run_dir / 'normal-dispatch-consumer-return.json', consumer)
    atomic_json(run_dir / 'terminal-status.json', {
        'status': 'completed', 'child_returncode': 0,
        'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'elapsed_seconds': time.time() - started,
        'normal_dispatch_consumer_return_sha256': sha(
            (run_dir / 'normal-dispatch-consumer-return.json').read_bytes()),
    })
    print(json.dumps(consumer, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    args = parser.parse_args()
    started = time.time()
    try:
        run(args.candidate, args.run_dir)
    except BaseException as error:
        run_dir = args.run_dir.resolve()
        process_path = run_dir / 'process.json'
        terminal_path = run_dir / 'terminal-status.json'
        if process_path.is_file() and not terminal_path.exists():
            atomic_json(terminal_path, {
                'status': 'failed', 'child_returncode': 1,
                'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'elapsed_seconds': time.time() - started,
                'error_type': type(error).__name__,
                'error_message': str(error),
            })
            atomic_json(run_dir / 'progress.json', {
                'stage': 'failed', 'elapsed_seconds': time.time() - started,
                'error_type': type(error).__name__,
                'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
        raise


if __name__ == '__main__':
    main()
