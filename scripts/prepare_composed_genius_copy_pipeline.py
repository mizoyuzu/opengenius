"""Gate a composed-source final-copy pipeline and print its exact next steps.

Default mode is read-only: it validates the independently produced composed
source audit and prints a package plan. ``--stage-copy`` is the separate,
explicit operation that writes a candidate bundle under a new output directory;
it never edits the original bundle or a live database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINIMUM_ROOTS = 1000
SOURCE_AUDIT_PRODUCER = 'opengenius-independent-composed-radio-source-audit-v1'
NATIVE_CHECKER = ROOT / 'scripts/preflight_genius_package_v22.py'
UPDATER = ROOT / 'scripts/update_genius_bundle.py'
INDEX_AUDITOR = ROOT / 'scripts/audit_index_expansion.py'
TOPOLOGY_AUDITOR = ROOT / 'scripts/audit_genius_graph_topology_v01.py'
SAMPLE_SELECTOR = ROOT / 'scripts/select_final_musiccore_samples_v01.py'
MANIFEST_WRITER = ROOT / 'scripts/write_genius_package_manifest_v02.py'


class GateError(ValueError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def hash_input(path: Path) -> str:
    """Hash a file or a deterministic, symlink-free directory inventory."""
    path = Path(path)
    if path.is_file():
        return sha(path)
    require(path.is_dir() and not path.is_symlink(), 'Missing or invalid input: ' + str(path))
    rows = []
    for child in sorted(path.rglob('*')):
        require(not child.is_symlink(), 'Input directory contains a symlink: ' + str(child))
        if child.is_file():
            rows.append([child.relative_to(path).as_posix(), sha(child)])
    payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise GateError('Expected a JSON object: ' + str(path))
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def require_disjoint(left: Path, right: Path, message: str) -> None:
    """Require two resolved paths to have no ancestor/descendant overlap."""
    left, right = Path(left).resolve(), Path(right).resolve()
    require(left != right and not left.is_relative_to(right)
            and not right.is_relative_to(left), message)


def validate_source_audit(*, union_dir: Path, audit_path: Path,
                          expected_audit_sha256: str) -> dict:
    """Bind a passed whole-source report to this exact composed graph/state."""
    union_dir, audit_path = Path(union_dir).resolve(), Path(audit_path).resolve()
    require(re.fullmatch(r'[0-9a-f]{64}', expected_audit_sha256) is not None,
            'source audit SHA-256 must be lowercase hexadecimal')
    require(union_dir.is_dir() and audit_path.is_file(),
            'composed union directory or source audit is missing')
    observed_audit_sha = sha(audit_path)
    require(observed_audit_sha == expected_audit_sha256,
            'source audit bytes do not match the supplied SHA-256')
    audit = read_json(audit_path)
    require(audit.get('producer') == SOURCE_AUDIT_PRODUCER
            and audit.get('status') == 'passed'
            and audit.get('goal_target_reached') is True,
            'independent composed-source audit did not pass its target gate')
    origin = audit.get('source_root_origin')
    require(isinstance(origin, dict) and origin.get('goal_metric') == 'total-genuine',
            'source audit does not use the total-genuine metric')
    counts = origin.get('counts')
    require(isinstance(counts, dict)
            and type(counts.get('total_active')) is int
            and counts['total_active'] >= MINIMUM_ROOTS
            and counts.get('unknown') == 0,
            'source audit is below 1000 total roots or has unknown origins')
    replay = audit.get('independent_full_chain_replay')
    require(isinstance(replay, dict) and replay.get('status') == 'passed'
            and replay.get('graph_receipt_matches') is True,
            'source audit lacks a passing independent full-chain replay')

    graph_path = union_dir / 'candidate-union-graphs.json'
    state_path = union_dir / 'candidate-state.json'
    composition_path = union_dir / 'composition-receipt.json'
    require(all(path.is_file() for path in (graph_path, state_path, composition_path)),
            'composed candidate graph/state/receipt set is incomplete')
    graph_sha, state_sha = sha(graph_path), sha(state_path)
    expected_files = audit.get('final_files_sha256')
    require(isinstance(expected_files, dict)
            and expected_files.get('accepted_graphs') == graph_sha
            and expected_files.get('state') == state_sha,
            'audited graph/state hashes do not match the selected union directory')
    require(replay.get('checkpoint') == str(union_dir)
            and replay.get('accepted_graphs_sha256') == graph_sha
            and replay.get('state_sha256') == state_sha,
            'full-chain replay does not bind the exact selected union graph/state')
    graph = read_json(graph_path)
    state = read_json(state_path)
    rows = graph.get('accepted_roots')
    require(isinstance(rows, list), 'candidate union graph has no accepted root vectors')
    active = [row for row in rows if isinstance(row, dict)
              and isinstance(row.get('ordered_target_pids'), list)
              and row['ordered_target_pids']]
    require(len(active) == counts['total_active'],
            'source-audit total differs from the selected active graph rows')
    require(isinstance(state.get('state_sha256'), str)
            and replay.get('replayed_state_sha256') == state['state_sha256'],
            'source-audit replay semantic state seal differs from candidate state')
    closure = audit.get('source_closure_sha256')
    require(isinstance(closure, dict) and closure,
            'source audit has no source closure')
    physical = {name: digest for name, digest in closure.items()
                if not name.startswith('receipt-original:')}
    for name, digest in physical.items():
        path = Path(name)
        require(path.is_file() and sha(path) == digest,
                'source-audit closure changed or is missing: ' + name)
    for name, digest in closure.items():
        if name.startswith('receipt-original:'):
            original = Path(name.removeprefix('receipt-original:'))
            require(original.suffix == '.py' and any(
                Path(saved).name == original.name and saved_hash == digest
                for saved, saved_hash in physical.items()),
                'historical code receipt lacks a matching archived source file')
    return {
        'audit_path': str(audit_path), 'audit_sha256': observed_audit_sha,
        'union_dir': str(union_dir), 'graph_path': str(graph_path),
        'graph_sha256': graph_sha, 'state_path': str(state_path),
        'state_sha256': state_sha, 'composition_receipt_path': str(composition_path),
        'composition_receipt_sha256': sha(composition_path),
        'active_roots': len(active), 'unknown_roots': counts['unknown'],
        'ordered_edges': sum(len(row['ordered_target_pids']) for row in active),
        'source_audit_producer': audit['producer'],
    }


def build_plan(*, verified: dict, output_dir: Path, bundle: Path,
               baseline_state: Path, track_snapshot: Path, identity_map: Path,
               music_executable: Path) -> dict:
    """Describe exact package stages; no bundle or database is written here."""
    output_dir = Path(output_dir).resolve()
    paths = {name: str(Path(value).resolve()) for name, value in {
        'bundle': bundle, 'baseline_state': baseline_state,
        'track_snapshot': track_snapshot, 'identity_map': identity_map,
        'music_executable': music_executable,
    }.items()}
    for name, value in paths.items():
        path = Path(value)
        require(path.exists(), f'{name} input is missing: {path}')
    require(not output_dir.exists(), 'package output must be a new directory')
    plan = {
        'schema_version': 1,
        'producer': 'opengenius-composed-final-copy-pipeline-plan-v1',
        'mode': 'read_only_plan_no_bundle_or_database_write',
        'source_audit': verified,
        'inputs': {name: {'path': value, 'sha256': hash_input(Path(value))}
                   for name, value in paths.items()},
        'native_baseline_gate': {
            'checker': str(NATIVE_CHECKER),
            'argv': [sys.executable, str(NATIVE_CHECKER), '--baseline-only'],
            'expected_assignments': 515, 'expected_ordered_edges': 3341,
            'expected_library_tracks': 3004,
        },
        'planned_stages': [
            'recheck the pinned Native 515-row baseline with preflight v22',
            'run update_genius_bundle against the audited full PID graph and Native baseline state into a new package directory',
            'audit the copied bundle, SQL/index/sync revisions, and five MusicCore samples',
            'audit ordered graph topology against copied SQL and package state',
            'seal final source, bundle, audit, and code hashes',
        ],
        'tools': {key: str(path) for key, path in {
            'updater': UPDATER, 'index_auditor': INDEX_AUDITOR,
            'topology_auditor': TOPOLOGY_AUDITOR, 'sample_selector': SAMPLE_SELECTOR,
            'manifest_writer': MANIFEST_WRITER,
        }.items()},
        'write_gate': {
            'minimum_audited_total_roots': MINIMUM_ROOTS,
            'unknown_root_origins': 0,
            'source_audit_sha256_required': True,
            'this_plan_writes_bundle_or_database': False,
        },
    }
    return plan


def _run_json(argv: list[str], stage: str) -> dict:
    result = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                            shell=False, check=False)
    if result.returncode != 0:
        raise GateError(f'{stage} failed (rc={result.returncode}): {result.stderr[-1600:]}')
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GateError(f'{stage} did not return JSON') from error
    if not isinstance(value, dict):
        raise GateError(f'{stage} returned a non-object JSON value')
    return value


def validate_index_report(index_doc: dict) -> None:
    """Validate the exact report schema returned by audit_index_expansion."""
    require(index_doc.get('sqlite_integrity') == {'baseline': 'ok', 'output': 'ok'}
            and index_doc.get('baseline_library_track_count') == 3004
            and index_doc.get('output_library_track_count') == 3004
            and index_doc.get('existing_pid_count') == 515
            and index_doc.get('existing_pid_gid_and_metadata_preserved') is True
            and index_doc.get('sync_revision_mismatch_count') == 0
            and len(index_doc.get('music_core_sample') or []) >= 5,
            'copied SQL/index/sync/MusicCore checks failed')


def validate_topology_report(topology_doc: dict, expected_roots: int) -> None:
    """Validate the report file, not the smaller JSON summary printed by CLI."""
    require(topology_doc.get('producer') == 'opengenius-copied-genius-graph-topology-audit-v1'
            and topology_doc.get('status') == 'passed'
            and topology_doc.get('accepted_pid_vectors_match_output_state_and_sql') is True
            and topology_doc.get('state_only_assignments_are_empty') is True
            and topology_doc.get('active_graph_roots') == expected_roots,
            'copied SQL relation topology differs from the audited PID graph')


def verify_unchanged_inputs(inputs: dict[str, tuple[Path, str]]) -> None:
    for name, (path, expected) in inputs.items():
        require(hash_input(path) == expected, 'Input changed during staging: ' + name)


def stage_copy(*, verified: dict, union_dir: Path, output_dir: Path,
               bundle: Path, baseline_state: Path, track_snapshot: Path,
               identity_map: Path, music_executable: Path) -> dict:
    """Stage a separate candidate bundle after all source/baseline gates pass."""
    output_dir = Path(output_dir).resolve()
    bundle, baseline_state = Path(bundle).resolve(), Path(baseline_state).resolve()
    track_snapshot, identity_map = Path(track_snapshot).resolve(), Path(identity_map).resolve()
    music_executable = Path(music_executable).resolve()
    require(not output_dir.exists() and not output_dir.is_relative_to(bundle)
            and not bundle.is_relative_to(output_dir),
            'candidate output must be new and separate from the original bundle')
    require_disjoint(output_dir, union_dir,
                     'candidate output must be separate from the audited source checkpoint')
    from update_genius_bundle import read_bundle
    original_bundle_before = read_bundle(bundle)
    monitored = {
        'source audit': (Path(verified['audit_path']), verified['audit_sha256']),
        'union graph': (Path(verified['graph_path']), verified['graph_sha256']),
        'union state': (Path(verified['state_path']), verified['state_sha256']),
        'identity map': (identity_map, hash_input(identity_map)),
        'track snapshot': (track_snapshot, hash_input(track_snapshot)),
        'baseline state': (baseline_state, hash_input(baseline_state)),
        'original bundle': (bundle, hash_input(bundle)),
        'Music executable': (music_executable, hash_input(music_executable)),
    }

    # This invokes only the already-reviewed read-only baseline gate.
    native = _run_json([sys.executable, str(NATIVE_CHECKER), '--baseline-only'],
                       'Native 515-row baseline check')
    require(native.get('baseline_assignments') == 515
            and native.get('baseline_ordered_edges') == 3341
            and native.get('library_track_count') == 3004,
            'Native baseline check did not reproduce the original 515 assignments')

    graph_path = Path(verified['graph_path'])
    graph = read_json(graph_path)
    # The evaluation is a direct, hash-bound serialization of the already
    # audited accepted PID vectors; it carries no new source or root claims.
    with tempfile.TemporaryDirectory(prefix='opengenius-final-evaluation-') as temporary:
        from music_identity_map import load_library
        tracks, library_hash, _ = load_library(track_snapshot=track_snapshot)
        eval_path = Path(temporary) / 'accepted-evaluation.json'
        evaluation = {
            'schema_version': 1, 'library_sha256': library_hash,
            'relation_rows': graph['accepted_roots'], 'network_requests': 0,
            'evaluation_scope': 'audited_composed_pid_graph_copy_only',
            'source_graph_sha256': verified['graph_sha256'],
            'track_count': len(tracks),
        }
        eval_path.write_text(json.dumps(evaluation, ensure_ascii=False, sort_keys=True) + '\n',
                             encoding='utf-8')
        updater = _run_json([
            sys.executable, str(UPDATER), '--bundle', str(bundle),
            '--track-snapshot', str(track_snapshot), '--identity-map', str(identity_map),
            '--evaluation', str(eval_path), '--baseline-state', str(baseline_state),
            '--executable', str(music_executable), '--output', str(output_dir),
            '--allow-unverified-profile'], 'separate candidate bundle update')
    package_bundle = output_dir / 'Music Library.musiclibrary'
    output_state = output_dir / 'state.json'
    require(package_bundle.is_dir() and output_state.is_file(),
            'bundle updater omitted the candidate bundle or output state')

    from update_genius_bundle import read_bundle
    source_state = read_json(baseline_state)
    native_pids = {row['persistent_id'] for row in source_state.get('rows', [])}
    audit_doc = read_json(Path(verified['audit_path']))
    yt_pids = set(audit_doc['source_root_origin']['ytmusic_source_backed_pids'])
    samples = sorted(yt_pids - native_pids)[:5]
    require(len(samples) == 5, 'fewer than five audited YT source roots outside Native assignments')

    index_path = output_dir / 'index-audit.json'
    index_summary = _run_json([
        sys.executable, str(INDEX_AUDITOR), '--baseline-bundle', str(bundle),
        '--output-bundle', str(package_bundle), '--baseline-state', str(baseline_state),
        '--output-state', str(output_state), '--track-snapshot', str(track_snapshot),
        '--identity-map', str(identity_map), '--executable', str(music_executable),
        '--output', str(index_path), '--require-sync-revisions',
        *[arg for pid in samples for arg in ('--sample-root', pid)],
    ], 'copied SQL/index/sync/MusicCore audit')
    index_doc = read_json(index_path)
    validate_index_report(index_doc)

    topology_path = output_dir / 'graph-topology-audit.json'
    topology_summary = _run_json([
        sys.executable, str(TOPOLOGY_AUDITOR), '--graph', str(graph_path),
        '--state', str(output_state), '--output-bundle', str(package_bundle),
        '--music-executable', str(music_executable), '--minimum-active-roots', '1000',
        '--output', str(topology_path),
    ], 'accepted graph to copied SQL topology audit')
    topology = read_json(topology_path)
    validate_topology_report(topology, verified['active_roots'])

    # Reconfirm the original bundle inventory and all primary inputs after use.
    package_files = read_bundle(package_bundle)
    physical_files = read_bundle(bundle)
    require(physical_files == original_bundle_before,
            'original bundle bytes or membership changed during separate-copy staging')
    verify_unchanged_inputs(monitored)
    # Re-run the selected audit closure against its original trust anchor at
    # commit time; the report bytes themselves must remain exactly pinned.
    validate_source_audit(union_dir=union_dir,
                          audit_path=Path(verified['audit_path']),
                          expected_audit_sha256=verified['audit_sha256'])
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-composed-final-copy-stage-v1',
        'status': 'passed_separate_candidate_copy',
        'source_audit': {'path': verified['audit_path'], 'sha256': verified['audit_sha256']},
        'audited_graph': {'path': str(graph_path), 'sha256': sha(graph_path)},
        'inputs_sha256': {name: digest for name, (_, digest) in monitored.items()},
        'native_baseline_check': native,
        'original_bundle_sha256': {name: hashlib.sha256(blob).hexdigest()
                                   for name, blob in physical_files.items()},
        'candidate_bundle_sha256': {name: hashlib.sha256(blob).hexdigest()
                                    for name, blob in package_files.items()},
        'code_sha256': {str(path): sha(path) for path in (
            Path(__file__).resolve(), NATIVE_CHECKER, UPDATER, INDEX_AUDITOR,
            TOPOLOGY_AUDITOR, SAMPLE_SELECTOR, MANIFEST_WRITER)},
        'output_state_sha256': sha(output_state),
        'updater_summary': updater,
        'index_audit': {'path': str(index_path), 'sha256': sha(index_path)},
        'topology_audit': {'path': str(topology_path), 'sha256': sha(topology_path)},
        'musiccore_sample_pids': samples,
        'original_database_written': False,
        'candidate_database_written': True,
        'bundle_staged': True,
        'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
    }
    receipt_path = output_dir / 'final-copy-stage-receipt.json'
    with receipt_path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    return {'receipt_path': str(receipt_path), 'receipt_sha256': sha(receipt_path),
            'index_audit': index_summary, 'topology_audit': topology,
            'original_database_written': False,
            'candidate_database_written': True, 'bundle_staged': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--union-dir', type=Path, required=True)
    parser.add_argument('--source-audit', type=Path, required=True)
    parser.add_argument('--source-audit-sha256', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--baseline-state', type=Path, required=True)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--music-executable', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='new output location reserved for later separate-copy staging')
    parser.add_argument('--output-plan', type=Path, required=True)
    parser.add_argument('--stage-copy', action='store_true',
                        help='after source-audit gating, create a separate candidate bundle and audits')
    args = parser.parse_args(argv)
    try:
        verified = validate_source_audit(union_dir=args.union_dir,
                                         audit_path=args.source_audit,
                                         expected_audit_sha256=args.source_audit_sha256)
        plan = build_plan(verified=verified, output_dir=args.output_dir,
                          bundle=args.bundle, baseline_state=args.baseline_state,
                          track_snapshot=args.track_snapshot, identity_map=args.identity_map,
                          music_executable=args.music_executable)
        output = args.output_plan.resolve()
        require(not output.exists(), 'plan output already exists; refusing overwrite')
        package_dir = args.output_dir.resolve()
        require(not output.is_relative_to(package_dir)
                and not package_dir.is_relative_to(output),
                'plan output and candidate package directory must be separate')
        require(not output.is_relative_to(args.union_dir.resolve()),
                'plan output must be outside the audited source checkpoint')
        require_disjoint(package_dir, args.union_dir,
                         'candidate output must be separate from the audited source checkpoint')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(plan, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
        staged = None
        if args.stage_copy:
            staged = stage_copy(verified=verified, union_dir=args.union_dir,
                                output_dir=args.output_dir, bundle=args.bundle,
                                baseline_state=args.baseline_state,
                                track_snapshot=args.track_snapshot,
                                identity_map=args.identity_map,
                                music_executable=args.music_executable)
        print(json.dumps({'status': 'ready_staged_copy' if staged else 'ready_read_only_plan',
                          'stage': staged, 'plan': str(output),
                          'plan_sha256': sha(output), 'active_roots': verified['active_roots'],
                          'original_database_written': False,
                          'candidate_database_written': bool(staged),
                          'bundle_staged': bool(staged)}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({'status': 'refused', 'error_type': type(error).__name__,
                          'message': str(error), 'original_database_written': False,
                          'candidate_database_written': 'unknown_or_partial' if args.stage_copy else False,
                          'bundle_staged': 'unknown_or_partial' if args.stage_copy else False},
                         sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
