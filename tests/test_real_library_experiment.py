"""Empty-target bootstrap refuses nonempty data and broken relation references."""
import sqlite3
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_real_library_experiment import initialize_empty_database
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


if __name__ == '__main__':
    unittest.main()
