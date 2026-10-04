"""Build a local experimental Library copy from real YTMusic radio/related edges.

Requires an empty target Genius DB and zero source Genius IDs. Never opts into
Genius, edits source files, uploads data, or claims native Music/iPod acceptance.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
from collections import Counter
import re
import shutil
import sqlite3

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from emulate_genius import MusicCore, database_rows
from evaluate_ytmusic_batch import collect_graphs, merge_graphs, observation_paths, relation_distances, shared_rows
from genius_dataset import append_dataset
from genius_profiles import distance_profiles
from inspect_music_library import decode_musicdb, parse_tracks
from music_identity_map import IdentityMap, load_library
from rewrite_genius import encrypt_pages, table_snapshot
from rewrite_music_ids import encode_library, patch_ids

CORE_FILES = ('Library.musicdb', 'Genius.itdb', 'Library Preferences.musicdb')
SUPPORT_FILES = ('Application.musicdb', 'Extras.itdb', 'Preferences.plist', 'sentinel')


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def load_native_template(directory, source_tracks):
    """Bind a native snapshot to its complete PID/metadata set and saved report.

    This checks local evidence and file integrity; the unsigned report is not an
    independent attestation of an Actions run. A newly edited output remains
    unverified in Music even when its input snapshot passed these checks.
    """
    directory = Path(directory).resolve(strict=True)
    report_path = directory.parent / 'report.json'
    if report_path.is_symlink() or report_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Invalid native template report')
    report_bytes = report_path.read_bytes()
    from music_clusters import _unique_keys
    report = json.loads(report_bytes, object_pairs_hook=_unique_keys)
    if (not isinstance(report, dict) or type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('private_real_library_probe') is not True or report.get('status') != 'library_loaded'
            or report.get('music_exited') is not True or report.get('after_copy_consistent_exit') is not True):
        raise ValueError('Native template needs a loaded Library and verified normal exit')
    native = report.get('native_library')
    if (not isinstance(native, dict) or native.get('expected_library_count_loaded') is not True
            or native.get('selected_tracks_loaded') is not True or type(native.get('library_count')) is not int
            or native['library_count'] != len(source_tracks) or type(report.get('expected_library_track_count')) is not int
            or report['expected_library_track_count'] != len(source_tracks)):
        raise ValueError('Native template Library load evidence differs from source')
    expected = {t['persistent_id']: t for t in source_tracks}
    if len(expected) != len(source_tracks):
        raise ValueError('Duplicate source Library PID')
    pids = native.get('library_pids')
    if (not isinstance(pids, list) or any(not isinstance(p, str) or not re.fullmatch('[0-9A-F]{16}', p) for p in pids)
            or len(pids) != len(set(pids)) or set(pids) != set(expected)):
        raise ValueError('Native template reported PID set differs from source')
    inventory = report.get('after_files')
    if not isinstance(inventory, list) or not inventory:
        raise ValueError('Native template needs after-file hashes')
    evidence, seen = {}, set()
    for entry in inventory:
        if not isinstance(entry, dict):
            raise ValueError('Invalid native template file evidence')
        name, digest, size = entry.get('path'), entry.get('sha256'), entry.get('size')
        if (not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or Path(name) == Path('.')
                or name in seen or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                or type(size) is not int or not 0 <= size <= 32 * 1024 * 1024):
            raise ValueError('Invalid native template file hash or path')
        path = directory / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(directory)):
            raise ValueError('Native template symlinks are unsupported')
        if not path.resolve(strict=True).is_relative_to(directory) or not path.is_file() or path.stat().st_size != size or sha(path.read_bytes()) != digest:
            raise ValueError('Native template file hash or size mismatch')
        seen.add(name)
        evidence[name] = digest
    if not set(CORE_FILES) <= seen or any((directory / name).is_file() and name not in seen for name in SUPPORT_FILES):
        raise ValueError('Native template core/support files missing hash evidence')
    files = {name: (directory / name).read_bytes() for name in CORE_FILES}
    expanded = decode_musicdb(files['Library.musicdb'])
    tracks, _ = parse_tracks(expanded)
    by_pid = {t['persistent_id']: t for t in tracks}
    if len(by_pid) != len(tracks) or by_pid != expected:
        raise ValueError('Native template PID or parsed track metadata differs from source')
    if any(t.get('genius_id') != '0000000000000000' for t in tracks):
        raise ValueError('Native template Genius IDs must all be zero')
    if encode_library(expanded, files['Library.musicdb']) != files['Library.musicdb']:
        raise ValueError('Native template Library codec does not reproduce its saved bytes')
    return files, {'directory': str(directory), 'report_path': str(report_path), 'report_sha256': sha(report_bytes),
                   'library_sha256': sha(files['Library.musicdb']), 'core_sha256': {n: sha(b) for n, b in files.items()},
                   'reported_after_file_sha256': evidence, 'native_load_and_normal_exit_evidence_checked': True,
                   'complete_pid_and_parsed_metadata_match': True, 'all_genius_ids_zero': True,
                   'evidence_limit': 'Unsigned local native report and snapshot integrity; edited output acceptance not established'}


def scope_recommendation_graphs(graphs, tracks, library_hash, config_path=None, tags=(), kind=None, excluded_kinds=()):
    if (tags or kind is not None or excluded_kinds) and config_path is None:
        raise ValueError('Cluster filtering requires a cluster config')
    if config_path is None:
        return graphs, None
    from music_clusters import classify_tracks, scope_graphs, _unique_keys
    raw = Path(config_path).read_bytes()
    classifications = classify_tracks(json.loads(raw, object_pairs_hook=_unique_keys), tracks, library_hash)
    result, scope = graphs, None
    if tags or kind is not None or excluded_kinds:
        result, scope = scope_graphs(graphs, classifications, list(tags), kind, excluded_kinds=excluded_kinds)
    return result, {'config_sha256': sha(raw), 'scope': scope,
                    'input_snapshot_graph_count': len(graphs), 'scoped_snapshot_graph_count': len(result),
                    'input_snapshot_edge_count': sum(len(g['ordered_target_pids']) for g in graphs),
                    'scoped_snapshot_edge_count': sum(len(g['ordered_target_pids']) for g in result),
                    'classification_kind_counts': dict(Counter(c['kind'] for c in classifications.values())),
                    'policy': 'Library-bound explicit tags/kinds; special Off Vocal markers then PID overrides; scope all directed edges before merging and metadata generation; unknown retained unless explicitly filtered'}


def initialize_empty_database(clear, config, metadata, similarities, limit):
    """Bootstrap only a completely empty schema; preserve nonempty DBs by refusal."""
    validate_database(clear)
    with sqlite3.connect(':memory:') as c:
        c.deserialize(clear)
        before = table_snapshot(c)
        if any(before.values()):
            raise ValueError('Target Genius DB must be entirely empty')
        schema = c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        c.execute('INSERT INTO genius_config(id,version,default_num_results,min_num_results,data) VALUES(1,2,?,10,?)', (limit, config))
        append_dataset(c, metadata, similarities, config)
        c.commit()
        if c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema:
            raise ValueError('Target Genius schema changed')
        return c.serialize(), table_snapshot(c)


def build(bundle, identity_map, directories, extras, reference, executable, profile, limit, *,
          native_template=None, cluster_config=None, cluster_tags=(), track_kind=None, excluded_kinds=()):
    if not 1 <= limit <= 100:
        raise ValueError('Playlist limit must be 1..100')
    source_core = {name: (bundle / name).read_bytes() for name in CORE_FILES}
    tracks, library_hash, provenance = load_library(bundle=bundle)
    if any(int(t['genius_id'], 16) for t in tracks):
        raise ValueError('This experiment requires all source Genius IDs to be zero')
    template_provenance = None
    source = source_core
    if native_template is not None:
        source, template_provenance = load_native_template(native_template, tracks)
    matcher = IdentityMap(json.loads(identity_map.read_bytes()), tracks, library_hash)
    paths, manifests = [], []
    for directory in directories:
        selected, digest = observation_paths(directory, [], library_hash)
        paths.extend(selected)
        manifests.append({'path': str(directory), 'sha256': digest})
    paths.extend(extras)
    paths = list(dict.fromkeys(paths))
    graphs, skipped, inputs = collect_graphs(paths, matcher, library_hash)
    graphs, cluster_provenance = scope_recommendation_graphs(graphs, tracks, library_hash, cluster_config, cluster_tags, track_kind, excluded_kinds)
    roots = merge_graphs(graphs)
    ids, metadata, similarities, mapping = shared_rows(roots, matcher)
    config, _, _ = database_rows(reference.read_bytes())
    variants = distance_profiles(config)
    if profile not in variants:
        raise ValueError('Unsupported distance profile')
    config = variants[profile]
    header = extract_key_header(source['Library Preferences.musicdb'])
    key, _ = derive_key(executable, header)
    clear = decrypt_pages(source['Genius.itdb'], key)
    clear, expected_tables = initialize_empty_database(clear, config, metadata, similarities, limit)
    encrypted = encrypt_pages(clear, key)
    recovered = decrypt_pages(encrypted, key)
    counts = validate_database(recovered)
    with sqlite3.connect(':memory:') as c:
        c.deserialize(recovered)
        if table_snapshot(c) != expected_tables:
            raise ValueError('Encryption roundtrip changed Genius rows')
    assignments = [{'persistent_id': pid, 'genius_id': f'{gid:016X}'} for pid, gid in sorted(ids.items())]
    expanded = decode_musicdb(source['Library.musicdb'])
    patched, changes = patch_ids(expanded, assignments)
    encoded = encode_library(patched, source['Library.musicdb'])
    saved_tracks, _ = parse_tracks(decode_musicdb(encoded))
    by_pid = {t['persistent_id']: t for t in tracks}
    for t in saved_tracks:
        expected = {**by_pid[t['persistent_id']]}
        if t['persistent_id'] in ids:
            expected['genius_id'] = f"{ids[t['persistent_id']]:016X}"
        if t != expected:
            raise ValueError('Saved Library changed track metadata outside assigned IDs')
    persisted_config, persisted_metadata, persisted_similarities = database_rows(recovered)
    core = MusicCore(executable, persisted_config, persisted_metadata, persisted_similarities)
    baseline = MusicCore(executable, variants['baseline'], metadata, similarities)
    by_gid = {value: pid for pid, value in ids.items()}
    edges = {r['root_pid']: r['ordered_target_pids'] for r in roots}
    results = []
    for root in roots:
        seed = root['root_pid']
        generated = core.generate(ids[seed], limit)
        selected = [by_gid[int(g, 16)] for g in generated['result_genius_ids']]
        distances = relation_distances(seed, edges)
        if not selected or selected[0] != seed or any(pid not in distances for pid in selected):
            raise ValueError('Playlist contains an unexpected or unreachable track')
        ordinary = baseline.generate(ids[seed], limit)
        results.append({'root_pid': seed, 'title': by_pid[seed].get('title'),
                        'artist': by_pid[seed].get('artist'), 'direct_candidate_count': len(root['ordered_target_pids']),
                        'unique_nonseed_video_count': root['unique_nonseed_video_count'],
                        'preferred_metadata_video_count': root['preferred_metadata_video_count'],
                        'preferred_metadata_match_rate': root['preferred_metadata_match_rate'],
                        'generated_count': len(selected), 'baseline_count': len(ordinary['result_genius_ids']),
                        'meets_ten_nonseed_tracks': len(selected) - 1 >= 10,
                        'playlist': [{'persistent_id': pid, 'genius_id': f'{ids[pid]:016X}',
                                      'title': by_pid[pid].get('title'), 'artist': by_pid[pid].get('artist'),
                                      'duration_ms': by_pid[pid].get('duration_ms'),
                                      'observed_relation_hops': distances[pid], 'identity_status': 'metadata_match_unverified_recording'} for pid in selected]})
    report = {'schema_version': 1, 'source_library_sha256': library_hash, 'library_input': provenance,
              'source_core_sha256': {name: sha(blob) for name, blob in source_core.items()},
              'native_template_provenance': template_provenance, 'cluster_classification': cluster_provenance,
              'template_core_sha256': {name: sha(blob) for name, blob in source.items()},
              'genius_preferences_reference': 'native_template' if native_template is not None else 'source_library',
              'identity_map_sha256': sha(identity_map.read_bytes()), 'observation_inputs': inputs,
              'seed_manifests': manifests, 'skipped_snapshots': skipped, 'library_track_count': len(tracks),
              'related_track_count': len(ids), 'seed_count': len(roots), 'directed_relation_count': sum(len(v) for v in edges.values()),
              'profile': profile, 'genius_table_counts': counts, 'codec_roundtrip_verified': True,
              'library_track_metadata_preserved': True, 'genius_preferences_unchanged': True,
              'genius_opt_in_attempted': False, 'network_requests': 0, 'uploaded_personal_data': False,
              'native_music_app_acceptance_verified': False, 'ipod_acceptance_verified': False,
              'identity_status': 'metadata_match_unverified_recording', 'results': results}
    provenance_edges = [{'root_pid': g['root_pid'], 'ordered_target_pids': g['ordered_target_pids'],
                         'input': g['input'], 'observations': g['observations'], 'candidate_groups': g['candidate_groups']} for g in graphs]
    return {**source, 'Library.musicdb': encoded, 'Genius.itdb': encrypted}, report, assignments, changes, provenance_edges


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'identity-map', 'genius-reference', 'executable', 'output-directory'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--observations-directory', type=Path, action='append', required=True)
    p.add_argument('--extra-observation', type=Path, action='append', default=[])
    p.add_argument('--profile', default='artist-album-minimum-one', choices=('baseline', 'artist-album-minimum-one', 'relations-only'))
    p.add_argument('--limit', type=int, default=25)
    p.add_argument('--native-template', type=Path)
    p.add_argument('--cluster-config', type=Path)
    p.add_argument('--cluster-tag', action='append', default=[])
    p.add_argument('--track-kind', choices=('vocal','bgm','off_vocal','unknown'))
    p.add_argument('--exclude-kind', choices=('vocal','bgm','off_vocal','unknown'), action='append', default=[])
    a = p.parse_args()
    source, output = a.bundle.resolve(), a.output_directory.resolve()
    if (a.cluster_tag or a.track_kind or a.exclude_kind) and not a.cluster_config:
        p.error('Cluster filtering requires --cluster-config')
    inputs = [source, a.identity_map.resolve(), a.genius_reference.resolve(), a.executable.resolve(),
              *(x.resolve() for x in a.observations_directory), *(x.resolve() for x in a.extra_observation),
              *([a.native_template.resolve(), a.native_template.resolve().parent / 'report.json'] if a.native_template else []),
              *([a.cluster_config.resolve()] if a.cluster_config else [])]
    if output.exists() or any(output == x or output.is_relative_to(x) or x.is_relative_to(output) for x in inputs):
        p.error('Output must be new and separate from every input')
    files, report, assignments, changes, edges = build(source, a.identity_map, a.observations_directory,
                                                      a.extra_observation, a.genius_reference, a.executable, a.profile, a.limit,
                                                      native_template=a.native_template, cluster_config=a.cluster_config,
                                                      cluster_tags=a.cluster_tag, track_kind=a.track_kind, excluded_kinds=a.exclude_kind)
    # Check inputs again before writing the independent experimental bundle.
    if any(sha((source / name).read_bytes()) != report['source_core_sha256'][name] for name in CORE_FILES):
        raise ValueError('Source Library changed during the experiment')
    support_source = a.native_template.resolve() if a.native_template else source
    if any(sha((support_source / name).read_bytes()) != report['template_core_sha256'][name] for name in CORE_FILES):
        raise ValueError('Output template changed during the experiment')
    if report['native_template_provenance']:
        proof = report['native_template_provenance']
        if sha(Path(proof['report_path']).read_bytes()) != proof['report_sha256']:
            raise ValueError('Native template report changed during the experiment')
        if any(sha((support_source / name).read_bytes()) != digest for name, digest in proof['reported_after_file_sha256'].items()):
            raise ValueError('Native template support evidence changed during the experiment')
    output.mkdir(mode=0o700)
    target = output / 'Music Library.musiclibrary'
    target.mkdir()
    for name, blob in files.items():
        (target / name).write_bytes(blob)
    for name in SUPPORT_FILES:
        src = support_source / name
        if src.is_file() and not src.is_symlink() and src.stat().st_size <= 1024 * 1024:
            shutil.copy2(src, target / name)
    for name, document in [('report.json', report), ('assignments.json', assignments), ('changed-slots.json', changes), ('relation-provenance.json', edges)]:
        (output / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    text = ['# 実曲とYTMusic関係によるローカル選曲', '', 'Music選曲コアのエミュレーション。アプリ全体・iPod生成は未検証。', '']
    for result in report['results']:
        text += [f"## {html.escape(result['title'] or '')} / {html.escape(result['artist'] or '')}", '', f"通常条件 {result['baseline_count']}曲 → 今回 {result['generated_count']}曲（起点を含む）", f"推薦video {result['unique_nonseed_video_count']}件中、優先metadata照合 {result['preferred_metadata_video_count']}件", '']
        text += [f"{i}. {html.escape(t['title'] or '')} / {html.escape(t['artist'] or '')}（関係{t['observed_relation_hops']}段）" for i,t in enumerate(result['playlist'],1)] + ['']
    (output / 'playlists.md').write_text('\n'.join(text))
    print(json.dumps({k: report[k] for k in ('library_track_count','related_track_count','seed_count','directed_relation_count')}))


if __name__ == '__main__':
    main()
