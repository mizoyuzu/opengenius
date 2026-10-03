"""Transactional append tests using disposable in-memory SQLite templates."""
import sqlite3
import struct
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_dataset import append_dataset
from genius_format import pack_config, pack_similarities


def snapshot(connection):
    master = connection.execute('SELECT * FROM sqlite_master ORDER BY type,name').fetchall()
    tables = {row[0]: connection.execute('SELECT * FROM "' + row[0] + '" ORDER BY 1').fetchall()
              for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    return master, tables


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.config = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                                   'flags': 0, 'result_words': [10, 20]})
        self.metadata = {0x70000001: struct.pack('<4Q', 0, 1, 1, 1), 0x70000002: struct.pack('<4Q', 0, 2, 2, 2)}
        self.similarities = {0x70000001: pack_similarities(0, [0x70000002]), 0x70000002: pack_similarities(0, [])}

    def database(self, constraint=''):
        connection = sqlite3.connect(':memory:')
        connection.execute('CREATE TABLE genius_metadata(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB' + constraint + ')')
        connection.execute('CREATE TABLE genius_similarities(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
        connection.execute('CREATE TABLE genius_config(id INTEGER PRIMARY KEY,version INTEGER,default_num_results INTEGER,min_num_results INTEGER,data BLOB)')
        connection.execute('CREATE TABLE genius_fingerprint(item_id INTEGER PRIMARY KEY,data BLOB)')
        connection.execute('CREATE TABLE genius_additional(item_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)')
        connection.execute('CREATE TABLE genius_other(id INTEGER PRIMARY KEY,value TEXT)')
        connection.execute('CREATE INDEX genius_other_value ON genius_other(value)')
        connection.execute('INSERT INTO genius_metadata VALUES(17,1,?)', (bytes(32),))
        # A similarities-only reference ID must also be protected from collisions.
        connection.execute('INSERT INTO genius_similarities VALUES(34,1,?)', (pack_similarities(0, [17]),))
        for identifier in (1, 2):
            connection.execute('INSERT INTO genius_config VALUES(?,2,25,10,?)', (identifier, self.config))
        connection.execute('INSERT INTO genius_fingerprint VALUES(101,?)', (b'fingerprint-template',))
        connection.execute('INSERT INTO genius_additional VALUES(101,9,?)', (b'auxiliary-template',))
        connection.execute("INSERT INTO genius_other VALUES(101,'retain')")
        connection.commit()
        self.addCleanup(connection.close)
        return connection

    def test_appends_without_replacing_template_or_auxiliary_rows(self):
        connection = self.database()
        before_master, before = snapshot(connection)
        changed = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [1, 2, 3, 4]}],
                              'flags': 0, 'result_words': [10, 20]})
        report = append_dataset(connection, self.metadata, self.similarities, changed)
        after_master, after = snapshot(connection)
        self.assertEqual(before_master, after_master)
        self.assertEqual(after['genius_metadata'][0], before['genius_metadata'][0])
        self.assertEqual(after['genius_similarities'][0], before['genius_similarities'][0])
        for table in ('genius_fingerprint', 'genius_additional', 'genius_other'):
            self.assertEqual(before[table], after[table])
        self.assertEqual(after['genius_config'][0][:-1], before['genius_config'][0][:-1])
        self.assertEqual(after['genius_config'][0][-1], changed)
        self.assertEqual(after['genius_config'][1], before['genius_config'][1])
        self.assertEqual(after['genius_metadata'][1:], [(gid, 1, self.metadata[gid]) for gid in sorted(self.metadata)])
        self.assertEqual(after['genius_similarities'][1:], [(gid, 1, self.similarities[gid]) for gid in sorted(self.similarities)])
        self.assertEqual(report['new_relation_count'], 1)
        self.assertIn('not rebased', report['auxiliary_preservation_limits'])
        self.assertFalse(report['music_app_acceptance_verified'])

    def test_invalid_ids_collisions_metadata_relations_and_config_leave_db_unchanged(self):
        genre_config = pack_config({'version': 2, 'filters': [{'type': 2, 'metadata_index': 0, 'records': []}],
                                    'flags': 0, 'result_words': [10, 20]})
        cases = [({}, {}, self.config), (self.metadata, {}, self.config)]
        for invalid_id in (0, True, -1, 1 << 32, 17, 34, '70000001'):
            cases.append(({invalid_id: bytes(32)}, {invalid_id: pack_similarities(0, [])}, self.config))
        cases.extend([
            ({0x70000001: bytes(31)}, {0x70000001: pack_similarities(0, [])}, self.config),
            ({0x70000001: struct.pack('<4Q', 1, 1, 1, 1)}, {0x70000001: pack_similarities(0, [])}, self.config),
            (self.metadata, {**self.similarities, 0x70000001: pack_similarities(0, [0x70000002, 0x70000002])}, self.config),
            (self.metadata, {**self.similarities, 0x70000001: pack_similarities(0, [17])}, self.config),
            (self.metadata, {**self.similarities, 0x70000001: pack_similarities(0, [99])}, self.config),
            (self.metadata, {**self.similarities, 0x70000001: b'bad'}, self.config),
            (self.metadata, self.similarities, b'bad'),
            (self.metadata, self.similarities, genre_config),
        ])
        connection = self.database()
        before = snapshot(connection)
        for index, (metadata, similarities, config) in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    append_dataset(connection, metadata, similarities, config)
                self.assertEqual(snapshot(connection), before)

    def test_unknown_schema_and_missing_config_are_rejected_before_mutation(self):
        for statement in ('CREATE TABLE unrelated(id INTEGER)',
                          'ALTER TABLE genius_metadata ADD COLUMN unexpected TEXT',
                          'UPDATE genius_config SET version=1 WHERE id=1',
                          'DELETE FROM genius_config WHERE id=1'):
            with self.subTest(statement=statement):
                connection = self.database()
                connection.execute(statement)
                connection.commit()
                before = snapshot(connection)
                with self.assertRaises(ValueError):
                    append_dataset(connection, self.metadata, self.similarities, self.config)
                self.assertEqual(snapshot(connection), before)

    def test_sql_error_rolls_back_partial_insert_and_preserves_caller_transaction(self):
        connection = self.database(', CHECK(genius_id != 1879048194)')
        connection.execute("UPDATE genius_other SET value='caller pending'")
        before = snapshot(connection)
        with self.assertRaises(sqlite3.IntegrityError):
            append_dataset(connection, self.metadata, self.similarities, self.config)
        self.assertEqual(snapshot(connection), before)
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertEqual(connection.execute('SELECT value FROM genius_other').fetchone()[0], 'retain')

    def test_unexpected_trigger_delta_rolls_back_every_table(self):
        connection = self.database()
        connection.execute("CREATE TRIGGER genius_append_side_effect AFTER INSERT ON genius_metadata BEGIN UPDATE genius_other SET value='unexpected'; END")
        connection.commit()
        before = snapshot(connection)
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            append_dataset(connection, self.metadata, self.similarities, self.config)
        self.assertEqual(snapshot(connection), before)


if __name__ == '__main__':
    unittest.main()
