"""Build a local experimental Library copy from real YTMusic radio/related edges.

Requires an empty target Genius DB and zero source Genius IDs. Never opts into
Genius, edits source files, uploads data, or claims native Music/iPod acceptance.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
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


def build(bundle, identity_map, directories, extras, reference, executable, profile, limit):
    if not 1 <= limit <= 100:
        raise ValueError('Playlist limit must be 1..100')
    source = {name: (bundle / name).read_bytes() for name in CORE_FILES}
    tracks, library_hash, provenance = load_library(bundle=bundle)
    if any(int(t['genius_id'], 16) for t in tracks):
        raise ValueError('This experiment requires all source Genius IDs to be zero')
    matcher = IdentityMap(json.loads(identity_map.read_bytes()), tracks, library_hash)
    paths, manifests = [], []
    for directory in directories:
        selected, digest = observation_paths(directory, [], library_hash)
        paths.extend(selected)
        manifests.append({'path': str(directory), 'sha256': digest})
    paths.extend(extras)
    paths = list(dict.fromkeys(paths))
    graphs, skipped, inputs = collect_graphs(paths, matcher, library_hash)
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
              'source_core_sha256': {name: sha(blob) for name, blob in source.items()},
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
    a = p.parse_args()
    source, output = a.bundle.resolve(), a.output_directory.resolve()
    inputs = [source, a.identity_map.resolve(), a.genius_reference.resolve(), a.executable.resolve(),
              *(x.resolve() for x in a.observations_directory), *(x.resolve() for x in a.extra_observation)]
    if output.exists() or any(output == x or output.is_relative_to(x) or x.is_relative_to(output) for x in inputs):
        p.error('Output must be new and separate from every input')
    files, report, assignments, changes, edges = build(source, a.identity_map, a.observations_directory,
                                                      a.extra_observation, a.genius_reference, a.executable, a.profile, a.limit)
    # Check inputs again before writing the independent experimental bundle.
    if any(sha((source / name).read_bytes()) != report['source_core_sha256'][name] for name in CORE_FILES):
        raise ValueError('Source Library changed during the experiment')
    output.mkdir(mode=0o700)
    target = output / 'Music Library.musiclibrary'
    target.mkdir()
    for name, blob in files.items():
        (target / name).write_bytes(blob)
    for name in SUPPORT_FILES:
        src = source / name
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
