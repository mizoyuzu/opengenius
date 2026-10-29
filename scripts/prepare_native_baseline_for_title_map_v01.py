"""Prepare a hash-only map transition for the original physical Native baseline.

The output retains the exact 515 Native PID/GID, metadata, and ordered relation
rows. It is a planning artifact only; it never writes to the source bundle.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library
from plan_genius_update import _hash, _source
from genius_format import parse_similarities
from update_genius_bundle import read_bundle, state_rows, update_bundle


ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
TITLE_CHILD = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'
SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
OLD_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
NEW_MAP = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/reviewed-shiny-ost-title-aliases-v01/identity-map.json'
BUNDLE = ROOT / 'data/new-library-rebased-20261009-v03/Music Library.musiclibrary'
MUSIC = Path('/mnt/data/mizoyuzu/Music.app/Contents/MacOS/Music')
PIN = {
    'snapshot': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    'old_map': '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
    'new_map': 'f4ed5d23c1a6b2909defa135387867937f0133e550fe1db0c30a278e5fda4734',
    'native_state': '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
    'native_state_seal': '188e671a4d3d6743fd33d025a1684c831d1ddd9f87d72bae08250340eee6bd89',
    'native_graph': '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
    'native_report': 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
    'native_receipt': '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
    'title_state': '477265d8936f6befa585a93b4826772815eea092872ff9c06d5571538a2eb4d7',
    'library': '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
    'bundle_library': '3869b162bf7be71fe0502dcdb175ca9899c123643170e6c47be702b737c3140c',
    'bundle_genius': '1fd5019e6eee66d5d2b0dba1f74d9563ce2cfd50d64b417c11d4e1d34275a190',
    'bundle_preferences': 'ecb3b4619800d6b5b0db08d6ae0470e9281afeb3c9f7c55b90399e9458b6c804',
    'music': 'a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a',
}


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    return sha_bytes(Path(path).read_bytes())


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def build(output):
    output = Path(output).resolve()
    state_output = output.with_name(output.stem + '-state.json')
    if output.exists() or state_output.exists():
        raise FileExistsError('Output or projected state already exists; preserve prior baseline artifacts')
    files = {
        'snapshot': SNAPSHOT, 'old_map': OLD_MAP, 'new_map': NEW_MAP,
        'native_state': NATIVE / 'baseline-state.json',
        'native_graph': NATIVE / 'accepted-baseline-graphs.json',
        'native_report': NATIVE / 'baseline-rebase-report.json',
        'native_receipt': NATIVE / 'native-baseline-alias-child-receipt.json',
        'title_state': TITLE_CHILD / 'state.json', 'bundle_library': BUNDLE / 'Library.musicdb',
        'bundle_genius': BUNDLE / 'Genius.itdb',
        'bundle_preferences': BUNDLE / 'Library Preferences.musicdb', 'music': MUSIC,
    }
    for key, path in files.items():
        if not Path(path).is_file() or sha(path) != PIN[key]:
            raise ValueError('Pinned baseline input changed: ' + key)
    snapshot = _load(SNAPSHOT)
    native = _load(files['native_state'])
    graph = _load(files['native_graph'])
    title = _load(files['title_state'])
    native_report = _load(files['native_report'])
    native_receipt = _load(files['native_receipt'])
    tracks, library_hash, _ = load_library(track_snapshot=SNAPSHOT)
    require(library_hash == PIN['library'], 'snapshot Library hash differs')
    old_map_doc, new_map_doc = _load(OLD_MAP), _load(NEW_MAP)
    old_map_hash, new_map_hash = sha(OLD_MAP), sha(NEW_MAP)
    require(old_map_hash == PIN['old_map'] and new_map_hash == PIN['new_map'], 'identity map pin mismatch')
    require(native.get('state_sha256') == PIN['native_state_seal']
            and native.get('source', {}).get('library_sha256') == library_hash
            and native.get('source', {}).get('identity_map_sha256') == old_map_hash,
            'Native baseline state source/seal differs')
    require(graph.get('source_state_sha256') == PIN['native_state']
            and isinstance(graph.get('accepted_roots'), list)
            and len(graph['accepted_roots']) == 515,
            'Native baseline graph does not bind the pinned 515-row state')
    require(native_report.get('producer') == 'opengenius-typed-native-genius-baseline-alias-child-v1'
            and native_report.get('identity_map_sha256') == old_map_hash
            and native_report.get('next_state_sha256') == native.get('state_sha256')
            and native_receipt.get('recording_identity_verified') is False
            and native_receipt.get('recording_edges_added') == 0
            and native_receipt.get('network_requests') == 0
            and native_receipt.get('preserved_track_count') == 3004
            and native_receipt.get('preserved_state_assignment_count') == 515
            and native_receipt.get('preserved_directed_edge_count') == 3341
            and native_receipt.get('pid_gid_assignments_unchanged') is True
            and native_receipt.get('metadata_bytes_unchanged') is True
            and native_receipt.get('ordered_relation_bytes_unchanged') is True,
            'Native rebase report/receipt contract differs')
    native_rows = {row['persistent_id']: row for row in native['rows']}
    title_rows = {row['persistent_id']: row for row in title['rows']}
    require(len(native_rows) == 515 and len(title_rows) == 972
            and set(native_rows) <= set(title_rows), 'Native/title PID assignments differ')
    for pid, row in native_rows.items():
        newer = title_rows[pid]
        require(row['genius_id'] == newer['genius_id']
                and row['metadata_hex'] == newer['metadata_hex']
                and _hash(row['equivalence_labels']) == _hash(newer['equivalence_labels']),
                'Title-map child changed a Native PID/GID, metadata, or identity label')
    _, old_labels = _source(IdentityMap(old_map_doc, tracks, library_hash), library_hash, old_map_hash)
    _, new_labels = _source(IdentityMap(new_map_doc, tracks, library_hash), library_hash, new_map_hash)
    require(old_labels == new_labels, 'Reviewed title map changed canonical identity labels')
    original_ids, original_meta, original_relations = state_rows(native)
    require(len(original_ids) == 515 and sum(len(parse_similarities(blob)[1])
                                               for blob in original_relations.values()) == 3341,
            'Native baseline SQL relation count differs from its exact source')
    roots = graph['accepted_roots']
    projected = deepcopy(native)
    projected['source']['identity_map_sha256'] = new_map_hash
    projected['state_sha256'] = _hash({key: value for key, value in projected.items()
                                       if key != 'state_sha256'})
    ids2, meta2, relations2 = state_rows(projected)
    require((original_ids, original_meta, original_relations) == (ids2, meta2, relations2),
            'Map-hash transition changed original SQL rows')
    matcher = IdentityMap(new_map_doc, tracks, library_hash)
    bundle_files = read_bundle(BUNDLE)
    prepared, sql_report, plan = update_bundle(
        bundle_files, projected, roots, matcher, library_hash, new_map_hash, MUSIC,
        allow_unverified_profile=True)
    require(sql_report.get('sql_integrity_check') == 'ok'
            and sql_report.get('changed_id_slots') == []
            and sql_report.get('config_and_auxiliary_tables_preserved') is True
            and sql_report.get('local_genius_state_preserved') is True
            and prepared['Genius.itdb'] == bundle_files['Genius.itdb'],
            'Physical original SQL does not exactly match the Native baseline state')
    require(plan['next_state']['state_sha256'] == projected['state_sha256'],
            'Current-map Native baseline does not reproduce its exact state')
    document = {
        'schema_version': 1,
        'producer': 'opengenius-native-baseline-title-map-hash-transition-v1',
        'scope': 'offline_preparation_no_bundle_write_no_network',
        'native_state': {'path': str((NATIVE / 'baseline-state.json').resolve()), 'sha256': PIN['native_state']},
        'source_map': {'path': str(OLD_MAP.resolve()), 'sha256': old_map_hash},
        'target_map': {'path': str(NEW_MAP.resolve()), 'sha256': new_map_hash},
        'snapshot': {'path': str(SNAPSHOT.resolve()), 'sha256': PIN['snapshot']},
        'physical_bundle': {'path': str(BUNDLE.resolve()), 'file_sha256': {
            name: sha_bytes(blob) for name, blob in bundle_files.items()}},
        'title_child': {'state_path': str((TITLE_CHILD / 'state.json').resolve()),
                        'state_sha256': PIN['title_state'], 'native_assignment_rows_compared': 515},
        'native_assignments': len(original_ids),
        'native_ordered_edges': 3341,
        'native_relations_unchanged': True,
        'title_map_equivalence_labels_unchanged': True,
        'physical_sql_matches_baseline': True,
        'sql_integrity_check': sql_report['sql_integrity_check'],
        'source_bundle_unchanged_on_disk': True,
        'network_requests': 0,
        'projected_state': projected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    state_bytes = (json.dumps(projected, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    document['projected_state_file'] = {'path': str(state_output), 'sha256': sha_bytes(state_bytes)}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    with state_output.open('xb') as stream:
        stream.write(state_bytes)
    return {'status': 'passed', 'output': str(output), 'output_sha256': sha(output),
            'projected_state': str(state_output), 'projected_state_sha256': sha(state_output),
            'native_assignments': 515, 'ordered_edges': 3341, 'network_requests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), sort_keys=True))


if __name__ == '__main__':
    main()
