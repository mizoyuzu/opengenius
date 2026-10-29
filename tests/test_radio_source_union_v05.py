"""Regression tests for the exact source-parent active-root delta."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path


RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v06-c6-cache-rematch-v01')
MANIFEST_SHA256 = 'd2e005f3b711b2bc20877b9e5353d00c3af6ff4e2aa67f1bcdaf10ecddc7584f'
ADAPTER = RUNTIME / 'scripts/project_radio_source_union_v05.py'
SOURCE = Path('/tmp/opengenius-radio-source-map-rematch-v16-732-actual-child-v01/accepted-graphs.json')
PARENT = Path('/tmp/opengenius-real-parent-alias-v01/latest-batch11-v13-development-v03/v13-append-chain-child/accepted-baseline-graphs.json')


def load_adapter():
    manifest_path = RUNTIME / 'runtime-manifest.json'
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise AssertionError('v06 test runtime manifest pin changed')
    manifest = json.loads(raw)
    for row in manifest['modules']:
        digest = hashlib.sha256((RUNTIME / row['path']).read_bytes()).hexdigest()
        if digest != row['sha256']:
            raise AssertionError('v06 test runtime module pin changed: ' + row['path'])
    spec = importlib.util.spec_from_file_location('_test_radio_source_union_v05', ADAPTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceParentActiveDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = load_adapter()

    def test_actual_saved_source_and_parent_rows_find_35_empty_to_active_pids(self):
        source = json.loads(SOURCE.read_bytes())
        parent = json.loads(PARENT.read_bytes())
        source_rows = self.adapter._relation_map(source['accepted_roots'], 'v16 source')
        parent_rows = self.adapter._relation_map(parent['accepted_roots'], 'verified 732-root parent')
        source_active = {pid: edges for pid, edges in source_rows.items() if edges}
        parent_active = {pid: edges for pid, edges in parent_rows.items() if edges}
        self.assertEqual((len(source_rows), len(source_active), sum(map(len, source_active.values()))),
                         (908, 767, 18021))
        self.assertEqual((len(parent_rows), len(parent_active), sum(map(len, parent_active.values()))),
                         (908, 732, 16976))
        self.assertEqual(len(set(source_active) - set(parent_rows)), 0)
        empty_existing = {pid for pid in source_active if pid in parent_rows and not parent_rows[pid]}
        self.assertEqual(len(empty_existing), 35)
        delta = self.adapter._typed_rematch_delta(source_active, parent_rows)
        self.assertEqual(set(delta), set(source_active) - set(parent_active))
        self.assertEqual(len(delta), 35)
        self.assertEqual(sum(map(len, delta.values())), 1045)

    def test_parent_rows_with_only_empty_vectors_do_not_hide_new_active_roots(self):
        delta = self.adapter._typed_rematch_delta(
            {'A': ['X'], 'B': ['Y']}, {'A': [], 'B': []})
        self.assertEqual(delta, {'A': ['X'], 'B': ['Y']})

    def test_exact_parent_active_vectors_are_not_new(self):
        source = {'A': ['X', 'Y'], 'B': ['Z']}
        parent = {'A': ['X', 'Y'], 'B': []}
        self.assertEqual(self.adapter._typed_rematch_delta(source, parent), {'B': ['Z']})


if __name__ == '__main__':
    unittest.main()
