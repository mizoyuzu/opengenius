"""Seal inputs and outputs of a completed offline Genius copy package."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def write_manifest(*, package_dir, growth_dir, progress, source_audit,
                   source_audit_receipt, source_preflight, index_audit,
                   baseline_projection, baseline_state, original_bundle,
                   identity_map, track_snapshot, music_executable, code_paths,
                   output):
    package_dir = Path(package_dir).resolve()
    growth_dir = Path(growth_dir).resolve()
    output = Path(output).resolve()
    require(package_dir.is_dir() and output.parent == package_dir,
            'manifest output must be inside the completed package directory')
    require(not output.exists(), 'manifest already exists; preserve prior receipt')
    audit_doc = json.loads(Path(source_audit).read_text(encoding='utf-8'))
    receipt_doc = json.loads(Path(source_audit_receipt).read_text(encoding='utf-8'))
    preflight_doc = json.loads(Path(source_preflight).read_text(encoding='utf-8'))
    index_doc = json.loads(Path(index_audit).read_text(encoding='utf-8'))
    origins = audit_doc.get('source_root_origin', {})
    counts = origins.get('counts', {})
    require(audit_doc.get('status') == 'passed'
            and audit_doc.get('independent_full_chain_replay', {}).get('status') == 'passed'
            and origins.get('goal_metric') == 'total-genuine'
            and type(counts.get('total_active')) is int and counts['total_active'] >= 1000
            and counts.get('unknown') == 0,
            'whole-source audit has not passed the total-genuine target')
    require(receipt_doc.get('source_audit_path') == str(Path(source_audit).resolve())
            and receipt_doc.get('source_audit_sha256') == sha(source_audit)
            and receipt_doc.get('checkpoint_path') == str(growth_dir),
            'detached source-audit receipt does not bind the final audit/checkpoint')
    require(preflight_doc.get('status') == 'ready_for_separate_copy_packaging'
            and preflight_doc.get('goal_metric') == 'total-genuine'
            and preflight_doc.get('qualifying_roots', 0) >= 1000
            and preflight_doc.get('unknown_root_origins') == 0,
            'package preflight did not pass the selected goal')
    require(index_doc.get('sqlite_integrity') == {'baseline': 'ok', 'output': 'ok'}
            and index_doc.get('baseline_library_track_count') == 3004
            and index_doc.get('output_library_track_count') == 3004
            and index_doc.get('existing_pid_count') == 515
            and index_doc.get('existing_pid_gid_and_metadata_preserved') is True
            and index_doc.get('sync_revision_mismatch_count') == 0
            and index_doc.get('music_core_sample')
            and len(index_doc['music_core_sample']) >= 5,
            'independent copied-bundle SQL/revision/MusicCore audit did not pass')

    receipts = {}

    def add(path):
        path = Path(path).resolve()
        require(path.is_file(), 'missing package-closure input: ' + str(path))
        receipts[str(path)] = sha(path)

    for path in (progress, source_audit, source_audit_receipt, source_preflight, index_audit,
                 baseline_projection, baseline_state, identity_map, track_snapshot, music_executable):
        add(path)
    graph_receipts = audit_doc.get('final_files_sha256', {})
    expected_names = {'accepted_graphs': 'accepted-graphs.json',
                      'accepted_evaluation': 'accepted-evaluation.json',
                      'state': 'state.json', 'merge_audit': 'merge-audit.json', 'progress': 'progress.json'}
    for key, name in expected_names.items():
        path = Path(progress).resolve() if key == 'progress' else growth_dir / name
        add(path)
        require(receipts[str(path.resolve())] == graph_receipts.get(key),
                'source audit final file hash differs: ' + key)
    for source_path, expected_hash in audit_doc.get('source_closure_sha256', {}).items():
        path = Path(source_path).resolve()
        add(path)
        require(receipts[str(path)] == expected_hash,
                'source-audit closure changed: ' + str(path))
    original_bundle = Path(original_bundle).resolve()
    for path in sorted(original_bundle.rglob('*')):
        if path.is_file():
            add(path)
    for path in code_paths:
        add(path)
    package_files = []
    for path in sorted(package_dir.rglob('*')):
        if path.is_file() and path.resolve() != output:
            package_files.append({'path': str(path.resolve()), 'sha256': sha(path)})
    require(any(Path(item['path']).name == 'report.json' for item in package_files)
            and any(Path(item['path']).name == 'plan.json' for item in package_files)
            and any(Path(item['path']).name == 'state.json' for item in package_files)
            and any('Music Library.musiclibrary' in Path(item['path']).parts for item in package_files),
            'package updater outputs are incomplete')
    manifest = {
        'schema_version': 1,
        'producer': 'opengenius-final-genius-package-hash-manifest-v1',
        'scope': 'separate-offline-copy; no Music.app or iPod acceptance implied',
        'source_audit': {'path': str(Path(source_audit).resolve()), 'sha256': sha(source_audit),
                         'metric': 'total-genuine', 'active_roots': counts['total_active'],
                         'native_only': counts.get('native_only'),
                         'ytmusic_source_backed': counts.get('ytmusic_source_backed'),
                         'overlap': counts.get('native_ytmusic_overlap'), 'unknown': 0},
        'accepted_growth_directory': str(growth_dir),
        'source_inputs_sha256': dict(sorted(receipts.items())),
        'package_outputs_sha256': package_files,
        'source_closure_sha256': audit_doc['source_closure_sha256'],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    return {'status': 'sealed', 'manifest': str(output), 'sha256': sha(output),
            'input_count': len(receipts), 'package_file_count': len(package_files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package-dir', 'growth-dir', 'progress', 'source-audit', 'source-audit-receipt',
                 'source-preflight', 'index-audit', 'baseline-projection', 'baseline-state',
                 'original-bundle', 'identity-map', 'track-snapshot', 'music-executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--code', action='append', type=Path, required=True)
    args = parser.parse_args()
    result = write_manifest(
        package_dir=args.package_dir, growth_dir=args.growth_dir, progress=args.progress,
        source_audit=args.source_audit, source_audit_receipt=args.source_audit_receipt,
        source_preflight=args.source_preflight, index_audit=args.index_audit,
        baseline_projection=args.baseline_projection, baseline_state=args.baseline_state,
        original_bundle=args.original_bundle, identity_map=args.identity_map,
        track_snapshot=args.track_snapshot, music_executable=args.music_executable,
        code_paths=args.code, output=args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
