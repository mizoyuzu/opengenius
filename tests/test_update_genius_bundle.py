"""Synthetic incremental SQL updates cannot silently replace unrelated data."""
import copy
from pathlib import Path
import sqlite3
import sys
import json
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import pack_config
from genius_format import pack_similarities
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state, plan_genius_update, _seal
from rewrite_genius import table_snapshot
from update_genius_bundle import update_sql, state_rows, read_bundle, update_library_sync
import struct
import tempfile


class IncrementalSqlTests(unittest.TestCase):
    def setUp(self):
        self.digest, self.map_hash = 'a' * 64, 'b' * 64
        self.pids = [f'{i:016X}' for i in range(1, 5)]
        tracks = [dict(persistent_id=pid, title=str(i), artist='Artist', album='Album')
                  for i, pid in enumerate(self.pids)]
        self.tracks = tracks
        matcher = IdentityMap(build_map(tracks, self.digest), tracks, self.digest)
        self.old = make_synthetic_state([self.root(2, [3])], matcher, self.digest, self.map_hash)
        self.new = plan_genius_update(self.old, [self.root(2, [1, 3]), self.root(1, [3])],
                                      matcher, self.digest, self.map_hash)['next_state']
        self.config = pack_config(dict(version=2, filters=[dict(type=1, parameters=[20, 50, 10, 10])],
                                       flags=0, result_words=[10, 20]))

    def root(self, number, targets):
        return dict(root_pid=self.pids[number - 1], ordered_target_pids=[self.pids[i - 1] for i in targets])

    def database(self, mutation=None):
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE genius_metadata(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_similarities(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_config(id INTEGER PRIMARY KEY,version INTEGER,default_num_results INTEGER,min_num_results INTEGER,data BLOB)')
            c.execute('CREATE TABLE genius_fingerprint(id INTEGER PRIMARY KEY,data BLOB)')
            _, metadata, relations = state_rows(self.old)
            for table, blobs in (('genius_metadata', metadata), ('genius_similarities', relations)):
                c.executemany('INSERT INTO ' + table + ' VALUES(?,1,?)', sorted(blobs.items()))
            c.execute('INSERT INTO genius_config VALUES(1,2,25,10,?)', (self.config,))
            if mutation:
                c.execute(mutation)
            c.commit()
            return c.serialize(), table_snapshot(c)

    def test_preserves_config_old_metadata_schema_and_original_bytes(self):
        source, before = self.database()
        no_op, no_op_tables = update_sql(source, self.old, self.old)
        self.assertEqual(no_op, source)
        self.assertEqual(no_op_tables, before)
        updated, expected = update_sql(source, self.old, self.new)
        with sqlite3.connect(':memory:') as c:
            c.deserialize(updated)
            self.assertEqual(table_snapshot(c), expected)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone(), ('ok',))
        self.assertEqual(expected['genius_config'], before['genius_config'])
        self.assertTrue(set(before['genius_metadata']) <= set(expected['genius_metadata']))
        self.assertEqual(len(expected['genius_metadata']), 3)
        with sqlite3.connect(':memory:') as c:
            c.deserialize(source)
            self.assertEqual(table_snapshot(c), before)
        again, after = update_sql(updated, self.new, self.new)
        self.assertEqual(again, updated)
        self.assertEqual(after, expected)

    def test_baseline_mismatch_auxiliary_data_and_trigger_side_effect_refused(self):
        for mutation in ("UPDATE genius_similarities SET version=9",
                         "INSERT INTO genius_fingerprint VALUES(1,x'00')",
                         "CREATE TRIGGER alter_config AFTER UPDATE ON genius_similarities BEGIN UPDATE genius_config SET default_num_results=99; END"):
            with self.subTest(mutation=mutation):
                source, before = self.database(mutation)
                with self.assertRaises(ValueError):
                    update_sql(source, self.old, self.new)
                with sqlite3.connect(':memory:') as c:
                    c.deserialize(source)
                    self.assertEqual(table_snapshot(c), before)

    def test_existing_id_and_metadata_replacement_refused(self):
        source, _ = self.database()
        for field, value in (('genius_id', 123), ('metadata_hex', bytes(32).hex())):
            invalid = copy.deepcopy(self.new)
            row = next(row for row in invalid['rows'] if row['persistent_id'] == self.pids[1])
            row[field] = value
            invalid = _seal({k: v for k, v in invalid.items() if k != 'state_sha256'})
            with self.subTest(field=field), self.assertRaises(ValueError):
                update_sql(source, self.old, invalid)

    def test_relation_only_state_refreshes_revision_and_is_idempotent(self):
        ids, _, relations = state_rows(self.old)
        target_pid = next(pid for pid, gid in ids.items() if relations[gid] != pack_similarities(0, []))
        target_gid = ids[target_pid]
        changed = copy.deepcopy(self.old)
        row = next(row for row in changed['rows'] if row['genius_id'] == target_gid)
        row['similarities_hex'] = pack_similarities(0, []).hex()
        changed = _seal({key: value for key, value in changed.items() if key != 'state_sha256'})
        self.assertEqual(set(state_rows(changed)[0]), set(ids))

        records = bytearray(12 + 3 * 376)
        records[:4] = b'ltma'
        struct.pack_into('<II', records, 4, 12, 3)
        pids = list(ids) + [self.pids[3]]
        for index, pid_value in enumerate(pids):
            offset = 12 + index * 376
            records[offset:offset + 4] = b'itma'
            struct.pack_into('<II', records, offset + 4, 376, 376)
            struct.pack_into('<Q', records, offset + 16, int(pid_value, 16))
        unindexed_offset = 12 + 2 * 376
        struct.pack_into('<I', records, unindexed_offset + 0x68, 0x12345678)

        original, _ = update_library_sync(bytes(records), self.old)
        refreshed, changes = update_library_sync(original, changed)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['persistent_id'], target_pid)
        self.assertEqual(struct.unpack_from('<I', refreshed, 12 + pids.index(target_pid) * 376 + 0xCC)[0], target_gid)
        self.assertNotEqual(struct.unpack_from('<I', refreshed, 12 + pids.index(target_pid) * 376 + 0x68)[0],
                            struct.unpack_from('<I', original, 12 + pids.index(target_pid) * 376 + 0x68)[0])
        self.assertEqual(struct.unpack_from('<I', refreshed, unindexed_offset + 0x68)[0], 0x12345678)
        again, no_changes = update_library_sync(refreshed, changed)
        self.assertEqual(again, refreshed)
        self.assertEqual(no_changes, [])

    def test_bundle_reader_preserves_nested_files_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('Library.musicdb', 'Library Preferences.musicdb', 'Genius.itdb'):
                (root / name).write_bytes(b'fixture')
            (root / 'nested').mkdir()
            (root / 'nested' / 'support').write_bytes(b'keep')
            self.assertEqual(read_bundle(root)['nested/support'], b'keep')
            (root / 'link').symlink_to(root / 'Genius.itdb')
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                read_bundle(root)

    def test_review_preference_cannot_be_silently_exported_as_native_genius(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                'track-snapshot': dict(source_sha256=self.digest, tracks=self.tracks),
                'identity-map': build_map(self.tracks, self.digest),
                'evaluation': dict(library_sha256=self.digest, selection_policy='user_cluster_preferred_then_fill'),
                'baseline-state': self.old}
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/update_genius_bundle.py')]
            for name, document in inputs.items():
                path = root / (name + '.json')
                path.write_text(json.dumps(document))
                command.extend(['--' + name, str(path)])
            (root / 'source').mkdir()
            (root / 'Music').write_bytes(b'unused executable')
            output = root / 'output'
            command.extend(['--bundle', str(root / 'source'), '--executable', str(root / 'Music'), '--output', str(output)])
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Application selection policy cannot be encoded', result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
