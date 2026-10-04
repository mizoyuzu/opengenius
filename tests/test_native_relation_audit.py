from pathlib import Path
import sqlite3
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_macos_real_library import audit_relation_graph
from genius_format import pack_similarities, sql_id


class NativeRelationAuditTests(unittest.TestCase):
    def database(self, metadata, rows):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE genius_metadata(genius_id INTEGER)')
        connection.execute('CREATE TABLE genius_similarities(genius_id INTEGER,version INTEGER,data BLOB)')
        connection.executemany('INSERT INTO genius_metadata VALUES(?)', [(sql_id(i),) for i in metadata])
        connection.executemany('INSERT INTO genius_similarities VALUES(?,?,?)', [(sql_id(i), v, b) for i, v, b in rows])
        return connection

    def test_signed_sql_ids_match_unsigned_targets_without_exporting_ids(self):
        large = (1 << 63) + 7
        connection = self.database([1, large], [(1, 1, pack_similarities(0, [large])),
                                              (large, 1, pack_similarities(0, []))])
        result = audit_relation_graph(connection)
        self.assertTrue(result['complete_reference_integrity_verified'])
        self.assertEqual(result['examined_directed_edges'], 1)
        self.assertNotIn(str(large), str(result))

    def test_missing_source_and_target_prevent_integrity_claim(self):
        result = audit_relation_graph(self.database([1], [(2, 1, pack_similarities(0, [1, 3]))]))
        self.assertEqual(result['sources_missing_metadata'], 1)
        self.assertEqual(result['dangling_targets'], 1)
        self.assertFalse(result['complete_reference_integrity_verified'])

    def test_unknown_version_compressed_and_truncated_rows_are_incomplete(self):
        result = audit_relation_graph(self.database([1], [(1, 2, pack_similarities(0, [])),
            (1, 1, struct.pack('<3I', 1, 4, 0)), (1, 1, b'truncated')]))
        self.assertEqual(result['unsupported_rows'], 3)
        self.assertFalse(result['complete_reference_integrity_verified'])
