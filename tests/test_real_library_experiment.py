"""Empty-target bootstrap refuses nonempty data and broken relation references."""
import sqlite3
import json
import tempfile
from unittest.mock import patch
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_real_library_experiment import initialize_empty_database, load_native_template, scope_recommendation_graphs, sha, CORE_FILES
from rewrite_music_ids import encode_library
from inspect_music_library import decode_musicdb, parse_tracks
from genius_format import pack_config, pack_similarities


class RealLibraryExperimentTests(unittest.TestCase):
    def setUp(self):
        self.config = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20,50,10,10]}],
                                   'flags': 0, 'result_words': [10,20]})
        self.metadata = {1: struct.pack('<4Q',0,1,1,1), 2: struct.pack('<4Q',0,1,1,2)}
        self.relations = {1: pack_similarities(0,[2]), 2: pack_similarities(0,[])}

    def template(self, nonempty=None):
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE genius_metadata(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_similarities(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_config(id INTEGER PRIMARY KEY,version INTEGER,default_num_results INTEGER,min_num_results INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_fingerprint(id INTEGER PRIMARY KEY,data BLOB)')
            if nonempty == 'fingerprint':
                c.execute('INSERT INTO genius_fingerprint VALUES(1,?)',(b'keep',))
            if nonempty == 'config':
                c.execute('INSERT INTO genius_config VALUES(1,2,25,10,?)',(self.config,))
            c.commit()
            return c.serialize()

    def test_commit_preserves_serialized_rows_and_empty_auxiliary_schema(self):
        source = self.template()
        clear, tables = initialize_empty_database(source,self.config,self.metadata,self.relations,25)
        with sqlite3.connect(':memory:') as c:
            c.deserialize(clear)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertEqual(c.execute('SELECT genius_id FROM genius_metadata ORDER BY 1').fetchall(),[(1,),(2,)])
            self.assertEqual(c.execute('SELECT default_num_results,min_num_results FROM genius_config').fetchone(),(25,10))
            self.assertEqual(c.execute('SELECT * FROM genius_fingerprint').fetchall(),[])
        with sqlite3.connect(':memory:') as c:
            c.deserialize(source)
            self.assertEqual(c.execute('SELECT count(*) FROM genius_metadata').fetchone(),(0,))
        self.assertEqual(len(tables['genius_similarities']),2)

    def test_nonempty_auxiliary_or_configuration_is_not_replaced(self):
        for table in ('fingerprint','config'):
            with self.assertRaisesRegex(ValueError,'entirely empty'):
                initialize_empty_database(self.template(table),self.config,self.metadata,self.relations,25)

    def test_unknown_relation_targets_rejected_before_any_output(self):
        source = self.template()
        with self.assertRaisesRegex(ValueError,'confined'):
            initialize_empty_database(source,self.config,self.metadata,{1:pack_similarities(0,[99]),2:self.relations[2]},25)
        with sqlite3.connect(':memory:') as c:
            c.deserialize(source)
            self.assertEqual(c.execute('SELECT count(*) FROM genius_config').fetchone(),(0,))


class NativeTemplateAndScopeTests(unittest.TestCase):
    def fixture(self, root, gids=(0, 0)):
        expanded = bytearray(b'ltma' + struct.pack('<II', 12, 2))
        for pid, gid in zip((17, 34), gids):
            record = bytearray(376)
            record[:4] = b'itma'
            struct.pack_into('<IIIQ', record, 4, 376, 376, 0, pid)
            struct.pack_into('<I', record, 0xCC, gid)
            expanded.extend(record)
        header = bytearray(160)
        header[:4] = b'hfma'
        struct.pack_into('<III', header, 4, 160, 160, 0x1f000c)
        header[16:48] = b'1.6.6.4' + bytes(25)
        struct.pack_into('<I', header, 84, 102400)
        library = encode_library(bytes(expanded), bytes(header))
        tracks, _ = parse_tracks(decode_musicdb(library))
        after = root / 'after'
        after.mkdir()
        files = {'Library.musicdb': library, 'Genius.itdb': b'synthetic-untrusted-genius', 'Library Preferences.musicdb': b'synthetic-preferences'}
        for name, data in files.items():
            (after / name).write_bytes(data)
        # Deliberately synthetic input for rejection tests; never used to build
        # output or claim that native Music ran or accepted this fixture.
        report = {'schema_version': 1, 'private_real_library_probe': True, 'status': 'library_loaded',
                  'music_exited': True, 'after_copy_consistent_exit': True, 'expected_library_track_count': 2,
                  'native_library': {'library_count': 2, 'expected_library_count_loaded': True,
                                     'selected_tracks_loaded': True, 'library_pids': [t['persistent_id'] for t in tracks]},
                  'after_files': [{'path': n, 'size': len(d), 'sha256': sha(d)} for n, d in files.items()]}
        (root / 'report.json').write_text(json.dumps(report))
        return after, tracks, report

    def test_native_template_refuses_failed_load_quit_and_hash_evidence(self):
        for field in ('music_exited', 'after_copy_consistent_exit', 'status'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                after, tracks, report = self.fixture(root)
                report[field] = False
                (root / 'report.json').write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'verified normal exit'):
                    load_native_template(after, tracks)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            after, tracks, report = self.fixture(root)
            (after / 'Genius.itdb').write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError, 'hash or size mismatch'):
                load_native_template(after, tracks)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            after, tracks, report = self.fixture(root)
            report['native_library']['selected_tracks_loaded'] = False
            (root / 'report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'load evidence'):
                load_native_template(after, tracks)

    def test_template_pid_metadata_and_nonzero_genius_ids_are_rejected(self):
        for change in ('pid', 'title'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                after, tracks, report = self.fixture(root)
                if change == 'pid':
                    report['native_library']['library_pids'][0] = 'FFFFFFFFFFFFFFFF'
                    (root / 'report.json').write_text(json.dumps(report))
                else:
                    tracks[0]['title'] = 'Different parsed title'
                with self.assertRaisesRegex(ValueError, 'PID|metadata'):
                    load_native_template(after, tracks)
        with tempfile.TemporaryDirectory() as directory:
            after, tracks, report = self.fixture(Path(directory), gids=(1, 0))
            with self.assertRaisesRegex(ValueError, 'must all be zero'):
                load_native_template(after, tracks)

    def test_foreign_cluster_config_and_filter_without_config_rejected(self):
        tracks = [{'persistent_id': f'{n:016X}', 'title': title, 'album': 'Album'}
                  for n, title in ((1, 'Seed'), (2, 'Other'), (3, 'Other (Off Vocal)'))]
        graph = {'root_pid': tracks[0]['persistent_id'], 'ordered_target_pids': [t['persistent_id'] for t in tracks[1:]]}
        with self.assertRaisesRegex(ValueError, 'requires a cluster config'):
            scope_recommendation_graphs([graph], tracks, 'a' * 64, excluded_kinds=['off_vocal'])
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'clusters.json'
            config.write_text(json.dumps({'schema_version': 1, 'source_library_sha256': 'b' * 64, 'rules': [], 'overrides': {}}))
            with self.assertRaisesRegex(ValueError, 'source Library'):
                scope_recommendation_graphs([graph], tracks, 'a' * 64, config)

    def test_scope_removes_off_vocal_before_shared_graph_but_retains_unknown(self):
        tracks = [{'persistent_id': f'{n:016X}', 'title': title, 'album': 'Album'}
                  for n, title in ((1, 'Seed'), (2, 'Other'), (3, 'Other (Off Vocal)'))]
        graph = {'root_pid': tracks[0]['persistent_id'], 'ordered_target_pids': [t['persistent_id'] for t in tracks[1:]]}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'clusters.json'
            config.write_text(json.dumps({'schema_version': 1, 'source_library_sha256': 'a' * 64, 'rules': [], 'overrides': {}}))
            selected, report = scope_recommendation_graphs([graph], tracks, 'a' * 64, config, excluded_kinds=['off_vocal'])
        self.assertEqual(selected[0]['ordered_target_pids'], [tracks[1]['persistent_id']])
        self.assertEqual(graph['ordered_target_pids'], [t['persistent_id'] for t in tracks[1:]])
        self.assertEqual(report['input_snapshot_edge_count'], 2)
        self.assertEqual(report['scoped_snapshot_edge_count'], 1)
        self.assertEqual(report['classification_kind_counts'], {'unknown': 2, 'off_vocal': 1})

    def test_excluded_off_vocal_root_cannot_bridge_to_unknown_track(self):
        from music_identity_map import IdentityMap
        from evaluate_ytmusic_batch import shared_rows, relation_distances
        tracks = [{'persistent_id': f'{n:016X}', 'title': title, 'album': 'Album'}
                  for n, title in ((1, 'Seed'), (2, 'Bridge (Off Vocal)'), (3, 'Unrelated destination'))]
        a, b, c = [t['persistent_id'] for t in tracks]
        graphs = [{'root_pid': a, 'ordered_target_pids': [b]}, {'root_pid': b, 'ordered_target_pids': [c]}]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'clusters.json'
            config.write_text(json.dumps({'schema_version': 1, 'source_library_sha256': 'a' * 64, 'rules': [], 'overrides': {}}))
            selected, report = scope_recommendation_graphs(graphs, tracks, 'a' * 64, config, excluded_kinds=['off_vocal'])
        matcher = IdentityMap({'schema_version': 1, 'library_sha256': 'a' * 64}, tracks, 'a' * 64)
        ids, metadata, relations, mapping = shared_rows(selected, matcher)
        self.assertEqual(set(ids), {a})
        self.assertEqual(len(metadata), 1)
        self.assertEqual(relation_distances(a, {g['root_pid']: g['ordered_target_pids'] for g in selected}), {a: 0})
        self.assertEqual(report['scoped_snapshot_edge_count'], 0)


if __name__ == '__main__':
    unittest.main()
