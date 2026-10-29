"""Choose five authenticated YT roots absent from the original 515 assignments."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select(graph_path, audit_path, native_state_path, output_path):
    graph_path, audit_path = map(lambda p: Path(p).resolve(), (graph_path, audit_path))
    native_state_path, output_path = map(lambda p: Path(p).resolve(), (native_state_path, output_path))
    if output_path.exists():
        raise FileExistsError('Sample manifest already exists')
    graph, audit, native_doc = (json.loads(path.read_text(encoding='utf-8'))
                                for path in (graph_path, audit_path, native_state_path))
    require(audit.get('producer') == 'opengenius-independent-ytmusic-source-audit-v1'
            and audit.get('status') == 'passed'
            and audit.get('independent_full_chain_replay', {}).get('status') == 'passed',
            'sample selection requires a passed whole-source audit')
    origins = audit.get('source_root_origin', {})
    counts = origins.get('counts', {})
    require(origins.get('goal_metric') == 'total-genuine'
            and counts.get('total_active', 0) >= 1000
            and counts.get('unknown') == 0,
            'sample selection requires 1000 total genuine roots and zero unknowns')
    graph_hash = sha(graph_path)
    require(audit.get('final_files_sha256', {}).get('accepted_graphs') == graph_hash,
            'sample graph differs from audited final graph')
    active_rows = graph.get('accepted_roots')
    require(isinstance(active_rows, list), 'accepted graph has no root rows')
    active = {row.get('root_pid') for row in active_rows
              if isinstance(row, dict) and isinstance(row.get('ordered_target_pids'), list)
              and row['ordered_target_pids']}
    yt = set(origins.get('ytmusic_source_backed_pids', []))
    native_origins = set(origins.get('native_only_pids', [])) | set(origins.get('native_ytmusic_overlap_pids', []))
    unknown = set(origins.get('unknown_pids', []))
    require(not unknown and active == yt | native_origins,
            'source audit root sets do not exactly partition the active graph')
    native_assigned = {row.get('persistent_id') for row in native_doc.get('rows', [])
                       if isinstance(row, dict)}
    candidates = sorted((yt & active) - native_assigned)
    require(len(candidates) >= 5, 'fewer than five YT-backed roots are absent from Native 515 assignments')
    chosen = candidates[:5]
    document = {
        'schema_version': 1,
        'producer': 'opengenius-final-musiccore-sample-selection-v1',
        'source_audit': {'path': str(audit_path), 'sha256': sha(audit_path)},
        'accepted_graph': {'path': str(graph_path), 'sha256': graph_hash},
        'native_baseline_state': {'path': str(native_state_path), 'sha256': sha(native_state_path)},
        'sample_pids': chosen,
        'sample_count': len(chosen),
        'selection': 'first five sorted active YTMusic-backed PIDs absent all original 515 Native assignments',
        'no_root_credit_or_db_write': True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    return document


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('graph', 'source-audit', 'native-state', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    doc = select(args.graph, args.source_audit, args.native_state, args.output)
    print(json.dumps({'output': str(args.output.resolve()), 'sample_pids': doc['sample_pids']}, sort_keys=True))


if __name__ == '__main__':
    main()
