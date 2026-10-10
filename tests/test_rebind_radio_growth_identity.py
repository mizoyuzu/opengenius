"""Typed identity-map rebinding invariants for genuine radio state."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from plan_genius_update import _source, make_synthetic_state, plan_genius_update
from rebind_radio_growth_identity import _rebind_state_map_hash, _stable_map_delta


LIBRARY = 'a' * 64
PID1, PID2 = '0000000000000001', '0000000000000002'


class RadioGrowthIdentityRebindingTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': PID1, 'title': 'Root', 'artist': 'Local A',
             'album': 'Album', 'duration_ms': 100000},
            {'persistent_id': PID2, 'title': 'Target', 'artist': 'Local B',
             'album': 'Album', 'duration_ms': 101000},
        ]
        self.old_map = build_map(self.tracks, LIBRARY)
        self.new_map = copy.deepcopy(self.old_map)
        self.new_map['artist_credit_aliases'] = [{
            'remote_credit': 'Remote B', 'local_credit': 'Local B',
            'enabled': True, 'status': 'reviewed_whole_credit_alias',
            'evidence': {'source_report_sha256': '1' * 64}}]
        self.old_bytes = json.dumps(self.old_map, ensure_ascii=False).encode()
        self.new_bytes = json.dumps(self.new_map, ensure_ascii=False).encode()
        self.old_hash = hashlib.sha256(self.old_bytes).hexdigest()
        self.new_hash = hashlib.sha256(self.new_bytes).hexdigest()
        self.old_matcher = IdentityMap(self.old_map, self.tracks, LIBRARY)
        self.new_matcher = IdentityMap(self.new_map, self.tracks, LIBRARY)

    def test_only_append_only_credit_aliases_can_rebind(self):
        old_aliases, new_aliases, title_delta = _stable_map_delta(self.old_map, self.new_map)
        self.assertEqual(old_aliases, [])
        self.assertEqual(len(new_aliases), 1)
        self.assertEqual(title_delta, [])
        _, old_labels = _source(self.old_matcher, LIBRARY, self.old_hash)
        _, new_labels = _source(self.new_matcher, LIBRARY, self.new_hash)
        self.assertEqual(old_labels, new_labels)

        changed_local = copy.deepcopy(self.new_map)
        changed_local['artist_alias_groups'] = [{'enabled': True, 'aliases': ['Local A', 'Different']}]
        with self.assertRaisesRegex(ValueError, 'differ beyond'):
            _stable_map_delta(self.old_map, changed_local)
        removed = copy.deepcopy(self.new_map)
        removed['artist_credit_aliases'] = []
        with self.assertRaisesRegex(ValueError, 'differ beyond'):
            _stable_map_delta(self.new_map, removed)

    def test_only_append_only_reviewed_title_alias_rows_can_rebind(self):
        base = copy.deepcopy(self.old_map)
        base['track_title_aliases'] = [{
            'persistent_id': PID1, 'original_title': 'Root',
            'aliases': [{'value': 'Root EN', 'enabled': False, 'status': 'needs_review'}]}]
        titled = copy.deepcopy(self.new_map)
        titled['track_title_aliases'] = copy.deepcopy(base['track_title_aliases'])
        titled['track_title_aliases'].append({
            'persistent_id': PID2, 'original_title': 'Target',
            'aliases': [{'value': 'Target - Target EN', 'enabled': True,
                         'status': 'reviewed_spelling_alias', 'reason': 'Exact title suffix.'}],
        })
        old_aliases, new_aliases, title_delta = _stable_map_delta(base, titled)
        self.assertEqual(len(new_aliases) - len(old_aliases), 1)
        self.assertEqual(len(title_delta), 1)
        mutated = copy.deepcopy(titled)
        mutated['track_title_aliases'][0]['original_title'] = 'Changed'
        with self.assertRaisesRegex(ValueError, 'append-only'):
            _stable_map_delta(base, mutated)

    def test_rebound_state_changes_only_map_hash_and_preserves_gid_and_edges(self):
        roots = [{'root_pid': PID1, 'ordered_target_pids': [PID2]},
                 {'root_pid': PID2, 'ordered_target_pids': []}]
        old_state = make_synthetic_state(roots, self.old_matcher, LIBRARY, self.old_hash)
        rebound = _rebind_state_map_hash(old_state, self.new_hash)
        self.assertNotEqual(old_state['state_sha256'], rebound['state_sha256'])
        self.assertEqual(old_state['rows'], rebound['rows'])
        self.assertEqual(old_state['source']['library_sha256'], rebound['source']['library_sha256'])
        self.assertEqual(rebound['source']['identity_map_sha256'], self.new_hash)
        self.assertEqual(plan_genius_update(rebound, roots, self.new_matcher,
                                            LIBRARY, self.new_hash)['next_state']['state_sha256'],
                         rebound['state_sha256'])


if __name__ == '__main__':
    unittest.main()
