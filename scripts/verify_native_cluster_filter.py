"""Audit a prepared cluster experiment and compare pinned Music core selections.

No Music.app process, network requests or live Library writes are performed.
The output records core behavior, not Music.app or iPod acceptance.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from decrypt_genius import derive_key, decrypt_pages, extract_key_header
from emulate_genius import MusicCore, database_rows
from music_clusters import classify_tracks
from music_identity_map import load_library
from prepare_native_cluster_filter import compile_filter, compile_policy
from rewrite_genius import table_snapshot
from update_genius_bundle import read_bundle, sha, state_rows


def compare_core(executable, before_rows, after_rows, ids, audit, seeds, limit=25, *, progress=None):
    if (not 1 <= limit <= 100 or not seeds or len(seeds) != len(set(seeds))
            or any(pid not in ids for pid in seeds)):
        raise ValueError('Need distinct assigned seeds and limit 1..100')
    if before_rows[2] != after_rows[2] or set(before_rows[1]) != set(after_rows[1]):
        raise ValueError('Relations or assigned metadata IDs changed')
    if any(before_rows[1][gid][8:] != after_rows[1][gid][8:] for gid in before_rows[1]):
        raise ValueError('Artist/album/song metadata changed')
    reverse = {gid: pid for pid, gid in ids.items()}
    if set(reverse) != set(after_rows[1]) or len(reverse) != len(ids):
        raise ValueError('Core metadata IDs differ from baseline assignments')
    allowed = dict(audit.get('compatibility_records', [(1, [1]), (2, [1, 2]), (3, [])]))
    buckets = audit['pid_buckets']
    before = MusicCore(executable, *before_rows)
    after = MusicCore(executable, *after_rows)
    results = []
    def run(core, pid):
        result = [reverse[int(gid, 16)] for gid in core.generate(ids[pid], limit)['result_genius_ids']]
        if not result or result[0] != pid or len(result) != len(set(result)) or len(result) > limit:
            raise ValueError('Core result violates seed, uniqueness or limit invariants')
        return result
    for pid in seeds:
        old, new = run(before, pid), run(after, pid)
        if any(buckets[target] not in allowed[buckets[pid]] for target in new[1:]):
            raise ValueError('Core result leaked outside the permitted memberships')
        results.append(dict(root_pid=pid, bucket=buckets[pid], before=old, after=new, order_unchanged=old == new))
        if progress:
            progress(len(results), len(seeds), len(old), len(new))
    return dict(root_count=len(results), unchanged=sum(row['order_unchanged'] for row in results),
                before_count=sum(len(row['before']) for row in results), after_count=sum(len(row['after']) for row in results),
                all_hard_scope_checks_passed=True, results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-bundle', 'prepared-directory', 'baseline-state', 'track-snapshot',
                 'cluster-config', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--scope-policy', type=Path)
    parser.add_argument('--evaluation', type=Path, help='Select its distinct assigned graph roots; otherwise all assigned PIDs')
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--progress', action='store_true', help='Print completed root counts during comparison')
    args = parser.parse_args()
    verifier_bytes = Path(__file__).read_bytes()
    output = args.output.resolve()
    inputs = {name: path for name, path in vars(args).items() if isinstance(path, Path) and name != 'output'}
    if output.exists() or any(output == path.resolve() or output.is_relative_to(path.resolve())
                              or path.resolve().is_relative_to(output) for path in inputs.values()):
        parser.error('Output must be new and separate from all inputs')
    documents = {name: path.read_bytes() for name, path in inputs.items()
                 if name not in ('baseline_bundle', 'prepared_directory')}
    report_path = args.prepared_directory / 'report.json'
    report_bytes = report_path.read_bytes()
    prepared_report = json.loads(report_bytes)
    for name in ('baseline_state', 'track_snapshot', 'cluster_config', 'executable', 'scope_policy'):
        if name in documents and sha(documents[name]) != prepared_report['input_sha256'].get(name):
            raise ValueError('Prepared input hash mismatch: ' + name)
    source = read_bundle(args.baseline_bundle)
    prepared = read_bundle(args.prepared_directory / 'Music Library.musiclibrary')
    for files, field in ((source, 'source_file_sha256'), (prepared, 'output_file_sha256')):
        if {name: sha(blob) for name, blob in files.items()} != prepared_report[field]:
            raise ValueError('Bundle differs from preparation manifest: ' + field)
    if any(source[name] != prepared[name] for name in source if name != 'Genius.itdb'):
        raise ValueError('Library, preferences or support files changed')
    baseline = json.loads(documents['baseline_state'])
    ids, _, _ = state_rows(baseline)
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    if library_hash != baseline['source']['library_sha256']:
        raise ValueError('Snapshot belongs to another baseline')
    labels = classify_tracks(json.loads(documents['cluster_config']), tracks, library_hash)
    key, _ = derive_key(args.executable, extract_key_header(source['Library Preferences.musicdb']))
    old_clear = decrypt_pages(source['Genius.itdb'], key)
    new_clear = decrypt_pages(prepared['Genius.itdb'], key)
    if prepared_report['policy'] == 'native_all_matching_scopes':
        if not args.scope_policy:
            parser.error('This experiment requires its scope policy')
        _, expected, audit = compile_policy(old_clear, baseline, labels, json.loads(documents['scope_policy']))
    elif prepared_report['policy'] == 'native_hard_scope_for_matching_seeds':
        if args.scope_policy:
            parser.error('Single-scope experiment does not use a scope policy')
        scope = prepared_report['scope']
        _, expected, audit = compile_filter(old_clear, baseline, labels, tags=scope['tags'], kind=scope['kind'],
                                             excluded_kinds=scope['excluded_kinds'])
    else:
        raise ValueError('Unsupported native policy')
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(old_clear)
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(new_clear)
        if (table_snapshot(connection) != expected
                or connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema
                or connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]):
            raise ValueError('Prepared SQL differs from independently recompiled policy')
    seeds = sorted(ids)
    if args.evaluation:
        evaluation = json.loads(documents['evaluation'])
        if evaluation.get('library_sha256') != library_hash:
            raise ValueError('Evaluation belongs to another Library')
        seeds = [graph['root_pid'] for graph in evaluation['relation_rows']]
    def progress(done, total, before, after):
        print(json.dumps(dict(processed=done, total=total, before=before, after=after)), flush=True)
    result = compare_core(args.executable, database_rows(old_clear), database_rows(new_clear), ids, audit, seeds,
                          args.limit, progress=progress if args.progress else None)
    titles = {track['persistent_id']: track['title'] for track in tracks}
    for row in result['results']:
        row.update(title=titles[row['root_pid']], after_titles=[titles[pid] for pid in row['after']])
    result.update(schema_version=1, policy=audit['policy'], limit=args.limit, recompiled_sql_verified=True,
                  source_support_files_unchanged=True, executable_sha256=sha(documents['executable']),
                  prepared_report_sha256=sha(report_bytes), input_sha256={name: sha(blob) for name, blob in documents.items()},
                  verifier_sha256=sha(verifier_bytes),
                  network_requests=0, music_app_acceptance_verified=False, ipod_acceptance_verified=False)
    if (read_bundle(args.baseline_bundle) != source
            or Path(__file__).read_bytes() != verifier_bytes
            or read_bundle(args.prepared_directory / 'Music Library.musiclibrary') != prepared
            or report_path.read_bytes() != report_bytes
            or any(path.read_bytes() != documents[name] for name, path in inputs.items()
                   if name not in ('baseline_bundle', 'prepared_directory'))):
        raise ValueError('Inputs changed during core verification')
    with output.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({name: result[name] for name in ('root_count', 'unchanged', 'before_count', 'after_count')}))


if __name__ == '__main__':
    main()
