"""Hard cluster configuration preserves the baseline and rejects unsafe rebasing."""
import copy
import os
from pathlib import Path
import sqlite3
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from emulate_genius import MusicCore
from genius_format import pack_config, pack_similarities
from prepare_native_cluster_filter import compile_filter
from rewrite_genius import table_snapshot
from update_genius_bundle import update_sql
import test_update_genius_bundle as fixtures


class NativeClusterFilterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IncrementalSqlTests()
        self.fixture.setUp()
        self.source, self.before = self.fixture.database()
        self.labels = {pid: dict(tags=['cluster'] if i == 1 else [], kind='unknown')
                       for i, pid in enumerate(self.fixture.pids)}

    def test_only_word_zero_and_config_data_change_and_updater_stays_strict(self):
        clear, expected, audit = compile_filter(self.source, self.fixture.old, self.labels, tags=['cluster'])
        self.assertEqual(audit['bucket_counts'], {'1': 1, '2': 1, '3': 0})
        for old, new in zip(self.before['genius_metadata'], expected['genius_metadata']):
            self.assertEqual(old[:2], new[:2])
            self.assertEqual(old[2][8:], new[2][8:])
            self.assertGreater(struct.unpack('<Q', new[2][:8])[0], 0)
        self.assertEqual(self.before['genius_config'][0][:-1], expected['genius_config'][0][:-1])
        for name in self.before.keys() - {'genius_metadata', 'genius_config'}:
            self.assertEqual(self.before[name], expected[name])
        with sqlite3.connect(':memory:') as c:
            c.deserialize(self.source)
            self.assertEqual(table_snapshot(c), self.before)
            c.deserialize(clear)
            self.assertEqual(table_snapshot(c), expected)
        with self.assertRaises(ValueError):
            update_sql(clear, self.fixture.old, self.fixture.old)
        with self.assertRaises(ValueError):
            compile_filter(clear, self.fixture.old, self.labels, tags=['cluster'])

    def test_unknowns_have_explicit_positive_bucket_and_exclusions_take_precedence(self):
        labels = copy.deepcopy(self.labels)
        labels[self.fixture.pids[2]] = dict(tags=['cluster'], kind='off_vocal')
        _, _, audit = compile_filter(self.source, self.fixture.old, labels, tags=['cluster'], excluded_kinds=['off_vocal'])
        self.assertEqual(audit['pid_buckets'], {self.fixture.pids[1]: 1, self.fixture.pids[2]: 3})
        labels[self.fixture.pids[2]] = dict(tags=[], kind='unknown')
        _, _, audit = compile_filter(self.source, self.fixture.old, labels, tags=['cluster'])
        self.assertEqual(audit['pid_buckets'][self.fixture.pids[2]], 2)

    def test_missing_classification_unknown_tag_unassigned_match_and_source_mutation_refused(self):
        missing = {self.fixture.pids[1]: self.labels[self.fixture.pids[1]]}
        unassigned = {pid: dict(tags=['elsewhere'] if i == 0 else [], kind='unknown')
                      for i, pid in enumerate(self.fixture.pids)}
        for labels, tag in ((missing, 'cluster'), (self.labels, 'typo'), (unassigned, 'elsewhere')):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                compile_filter(self.source, self.fixture.old, labels, tags=[tag])
        for mutation in ("UPDATE genius_metadata SET version=9",
                         "CREATE TRIGGER alter_config AFTER UPDATE ON genius_metadata BEGIN UPDATE genius_config SET min_num_results=99; END"):
            source, _ = self.fixture.database(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compile_filter(source, self.fixture.old, self.labels, tags=['cluster'])

    @unittest.skipUnless(os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE'), 'Needs the pinned Music executable')
    def test_actual_music_core_enforces_scope_and_empty_exclusion_without_soft_priority(self):
        exe = Path(os.environ['OPENGENIUS_MUSIC_EXECUTABLE'])
        base = dict(version=2, filters=[dict(type=1, parameters=[20, 50, 10, 10])], flags=0, result_words=[10, 20])
        metadata = {i: struct.pack('<4Q', group, 1, 1, i) for i, group in [(1, 1), (2, 1), (3, 2), (4, 3)]}
        relations = {i: pack_similarities(0, [j for j in metadata if j != i]) for i in metadata}
        def run(records):
            config = copy.deepcopy(base)
            config['filters'].append(dict(type=2, metadata_index=0, records=records))
            core = MusicCore(exe, pack_config(config), metadata, relations)
            return [[int(gid, 16) for gid in core.generate(seed, 10)['result_genius_ids']] for seed in metadata]
        self.assertEqual(run([(1, [1]), (2, [1, 2]), (3, [])]), [[1, 2], [2, 1], [3, 1, 2], [4]])
        ascending = run([(i, [1, 2, 3]) for i in (1, 2, 3)])
        descending = run([(i, [3, 2, 1]) for i in (1, 2, 3)])
        self.assertEqual(ascending, [[1, 2, 3, 4], [2, 1, 3, 4], [3, 1, 2, 4], [4, 1, 2, 3]])
        self.assertEqual(ascending, descending)


if __name__ == '__main__':
    unittest.main()
