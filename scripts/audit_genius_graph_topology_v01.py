"""Compare the accepted PID graph with copied Genius SQL/state relations.

This is a read-only semantic bridge: the accepted graph is expressed in PID
order, while the copied database stores ordered Genius IDs. It allows extra
empty state assignments but rejects missing, reordered, or changed graph
vectors. It does not write a bundle or claim application acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path

from audit_index_expansion import _decrypt_bundle
from genius_format import parse_similarities
from update_genius_bundle import read_bundle, state_rows


def sha(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise ValueError('Cannot hash missing topology input: ' + str(path))
    rows = []
    for child in sorted(path.rglob('*')):
        if child.is_symlink():
            raise ValueError('Bundle tree contains a symlink: ' + str(child))
        if child.is_file():
            rows.append([child.relative_to(path).as_posix(),
                         hashlib.sha256(child.read_bytes()).hexdigest()])
    payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def verify_topology(graph, state, sql_relations, *, minimum_active_roots=1000):
    if not isinstance(graph, dict) or not isinstance(state, dict):
        raise ValueError('Accepted graph or output state is malformed')
    graph_rows = graph.get('accepted_roots')
    if not isinstance(graph_rows, list):
        raise ValueError('Accepted graph has no PID relation rows')
    ids, metadata, state_relations = state_rows(state)
    if not isinstance(sql_relations, dict) or sql_relations != state_relations:
        raise ValueError('Copied SQL relation rows differ from the output state')
    graph_by_pid = {}
    for row in graph_rows:
        pid, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if (not isinstance(pid, str) or not isinstance(targets, list)
                or pid in graph_by_pid
                or any(not isinstance(target, str) for target in targets)
                or len(targets) != len(set(targets))):
            raise ValueError('Accepted graph contains malformed or duplicate PID vectors')
        graph_by_pid[pid] = targets
    if not set(graph_by_pid) <= set(ids):
        raise ValueError('Output state omitted an accepted graph PID')
    active = {pid for pid, targets in graph_by_pid.items() if targets}
    edge_count = sum(len(targets) for targets in graph_by_pid.values())
    if len(active) < minimum_active_roots:
        raise ValueError('Accepted graph is below the minimum active-root count')
    if any(target not in ids for targets in graph_by_pid.values() for target in targets):
        raise ValueError('Accepted PID graph targets a PID without an output assignment')
    empty_state_only = set(ids) - set(graph_by_pid)
    for pid, gid in ids.items():
        actual_blob = state_relations.get(gid)
        if not isinstance(actual_blob, bytes):
            raise ValueError('Output state has no SQL relation blob for an assigned GID')
        word, actual_gids = parse_similarities(actual_blob)
        if word != 0:
            raise ValueError('Output SQL relation row has an unsupported header')
        expected_pids = graph_by_pid.get(pid, [])
        expected_gids = [ids[target] for target in expected_pids]
        if actual_gids != expected_gids:
            raise ValueError('Copied SQL ordered relations differ from the accepted PID graph')
        if pid in empty_state_only and actual_gids:
            raise ValueError('State-only assignment has outgoing SQL relations')
    return {
        'producer': 'opengenius-copied-genius-graph-topology-audit-v1',
        'status': 'passed',
        'active_graph_roots': len(active),
        'graph_pid_rows': len(graph_by_pid),
        'output_state_assignments': len(ids),
        'empty_state_only_assignments': len(empty_state_only),
        'ordered_edges': edge_count,
        'accepted_pid_vectors_match_output_state_and_sql': True,
        'state_only_assignments_are_empty': True,
    }


def audit_files(*, graph_path, state_path, output_bundle, music_executable,
                minimum_active_roots=1000):
    graph_path = Path(graph_path).resolve()
    state_path = Path(state_path).resolve()
    output_bundle = Path(output_bundle).resolve()
    music_executable = Path(music_executable).resolve()
    graph = json.loads(graph_path.read_bytes())
    state = json.loads(state_path.read_bytes())
    files = read_bundle(output_bundle)
    _, _, _, sql_relations, database_counts, key_steps = _decrypt_bundle(
        files, music_executable)
    result = verify_topology(graph, state, sql_relations,
                             minimum_active_roots=minimum_active_roots)
    return {
        **result,
        'inputs_sha256': {
            str(graph_path): sha(graph_path),
            str(state_path): sha(state_path),
            str(output_bundle): sha(output_bundle),
            str(music_executable): sha(music_executable),
        },
        'genius_sql_relation_row_count': len(sql_relations),
        'genius_sql_integrity_checked_by_decryptor': True,
        'genius_sql_table_counts': database_counts,
        'key_derivation_steps': key_steps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output-bundle', type=Path, required=True)
    parser.add_argument('--music-executable', type=Path, required=True)
    parser.add_argument('--minimum-active-roots', type=int, default=1000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit_files(
        graph_path=args.graph, state_path=args.state,
        output_bundle=args.output_bundle, music_executable=args.music_executable,
        minimum_active_roots=args.minimum_active_roots)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(output),
                      'sha256': sha(output)}, sort_keys=True))


if __name__ == '__main__':
    main()
