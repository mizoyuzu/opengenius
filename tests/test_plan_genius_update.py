"""Incremental synthetic plans preserve established IDs and equivalence groups."""
import copy
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import parse_metadata, parse_similarities
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state, plan_genius_update, _seal, MAX_GID, MAX_GROUP_ID

LIBRARY_HASH, MAP_HASH = 'a' * 64, 'b' * 64


def pid(number):
    return f'{number:016X}'


def root(number, targets):
    return dict(root_pid=pid(number), ordered_target_pids=[pid(target) for target in targets])


def rows(state):
    return {row['persistent_id']: row for row in state['rows']}


def reseal(state):
    return _seal({key: value for key, value in state.items() if key != 'state_sha256'})


class IncrementalPlanTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [dict(persistent_id=pid(number), title=title, artist=artist, album=album, duration_ms=100000)
                       for number, title, artist, album in ((1, 'Alpha', 'Alpha', 'Alpha'),
                                                          (2, 'Zulu', 'Zulu', 'Zulu'),
                                                          (3, 'Zulu', 'Zulu', 'Zulu'),
                                                          (4, 'Other', 'Other', 'Other'))]
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH), self.tracks, LIBRARY_HASH)
        self.state = make_synthetic_state([root(2, [3])], self.matcher, LIBRARY_HASH, MAP_HASH)

    def plan(self, full, state=None, matcher=None, library_hash=LIBRARY_HASH, map_hash=MAP_HASH):
        return plan_genius_update(self.state if state is None else state, full,
                                 self.matcher if matcher is None else matcher, library_hash, map_hash)

    def test_addition_preserves_old_ids_and_all_metadata_bytes_and_reuses_labels(self):
        original = copy.deepcopy(self.state)
        plan = self.plan([root(2, [1, 3, 4]), root(1, [3])])
        old, new = rows(self.state), rows(plan['next_state'])
        for persistent_id, row in old.items():
            self.assertEqual(new[persistent_id]['genius_id'], row['genius_id'])
            self.assertEqual(new[persistent_id]['metadata_hex'], row['metadata_hex'])
        self.assertEqual(self.state, original)
        self.assertEqual(plan['new_pids'], [pid(1), pid(4)])
        self.assertEqual(new[pid(1)]['genius_id'], max(row['genius_id'] for row in old.values()) + 1)
        old_words = parse_metadata(bytes.fromhex(old[pid(2)]['metadata_hex']))
        self.assertEqual(old_words, parse_metadata(bytes.fromhex(old[pid(3)]['metadata_hex'])))
        first_new_words = parse_metadata(bytes.fromhex(new[pid(1)]['metadata_hex']))
        self.assertEqual(first_new_words, (0, *(value + 1 for value in old_words[1:])))
        all_ids = {persistent_id: row['genius_id'] for persistent_id, row in new.items()}
        self.assertEqual(parse_similarities(bytes.fromhex(new[pid(2)]['similarities_hex']))[1],
                         [all_ids[pid(1)], all_ids[pid(3)], all_ids[pid(4)]])
        self.assertEqual(parse_similarities(bytes.fromhex(new[pid(3)]['similarities_hex']))[1], [])
        self.assertTrue(plan['audit']['existing_assignments_preserved'])
        self.assertTrue(plan['audit']['existing_metadata_bytes_preserved'])
        self.assertEqual(plan['files_written'], 0)
        self.assertFalse(plan['music_app_acceptance_verified'])

    def test_new_pid_with_existing_equivalence_labels_reuses_exact_old_groups(self):
        state = make_synthetic_state([root(2, [])], self.matcher, LIBRARY_HASH, MAP_HASH)
        plan = self.plan([root(2, [3])], state=state)
        new = rows(plan['next_state'])
        self.assertEqual(new[pid(2)]['metadata_hex'], new[pid(3)]['metadata_hex'])

    def test_full_graph_replaces_edges_removed_roots_are_empty_and_order_is_reported(self):
        state = make_synthetic_state([root(2, [3, 4]), root(3, [4])], self.matcher, LIBRARY_HASH, MAP_HASH)
        plan = self.plan([root(2, [4, 3])], state=state)
        deltas = {row['root_pid']: row for row in plan['relation_deltas']}
        self.assertTrue(deltas[pid(2)]['target_order_changed'])
        self.assertEqual(deltas[pid(2)]['added_target_pids'], [])
        self.assertEqual(deltas[pid(3)]['ordered_targets_after'], [])
        self.assertEqual(deltas[pid(3)]['removed_target_pids'], [pid(4)])
        self.assertEqual(parse_similarities(bytes.fromhex(rows(plan['next_state'])[pid(3)]['similarities_hex']))[1], [])
        repeat = self.plan([root(2, [4, 3])], state=plan['next_state'])
        self.assertEqual(repeat['relation_deltas'], [])
        self.assertEqual(repeat['next_state'], plan['next_state'])

    def test_source_hash_track_metadata_and_canonical_labels_changes_are_rejected(self):
        for changed in ({'library_hash': 'c' * 64}, {'map_hash': 'c' * 64}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'Source changed'):
                self.plan([root(2, [3])], **changed)
        tracks = copy.deepcopy(self.tracks)
        tracks[0]['duration_ms'] += 1  # Source change even outside assigned rows.
        matcher = IdentityMap(build_map(tracks, LIBRARY_HASH), tracks, LIBRARY_HASH)
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.plan([root(2, [3])], matcher=matcher)
        document = build_map(self.tracks, LIBRARY_HASH)
        document['artist_alias_groups'] = [dict(enabled=True, aliases=['Zulu', 'Other'])]
        matcher = IdentityMap(document, self.tracks, LIBRARY_HASH)
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.plan([root(2, [3])], matcher=matcher)

    def test_marker_integrity_assignment_and_genre_mismatches_rejected(self):
        state = copy.deepcopy(self.state); state['producer'] = 'apple-original'
        with self.assertRaisesRegex(ValueError, 'Only dedicated'):
            self.plan([root(2, [3])], state=state)
        state = copy.deepcopy(self.state); state['rows'][0]['genius_id'] += 10
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.plan([root(2, [3])], state=state)
        for gid in (0, True, MAX_GID + 1, self.state['rows'][1]['genius_id']):
            state = copy.deepcopy(self.state); state['rows'][0]['genius_id'] = gid
            with self.subTest(gid=gid), self.assertRaisesRegex(ValueError, 'assignment'):
                self.plan([root(2, [3])], state=reseal(state))
        state = copy.deepcopy(self.state)
        words = list(parse_metadata(bytes.fromhex(state['rows'][0]['metadata_hex']))); words[0] = 1
        state['rows'][0]['metadata_hex'] = struct.pack('<4Q', *words).hex()
        with self.assertRaisesRegex(ValueError, 'genre zero'):
            self.plan([root(2, [3])], state=reseal(state))

    def test_group_label_bijection_and_dangling_existing_edges_are_rejected(self):
        # Equal labels cannot have different group IDs.
        state = copy.deepcopy(self.state)
        state['rows'][1]['metadata_hex'] = struct.pack('<4Q', 0, 2, 1, 1).hex()
        with self.assertRaisesRegex(ValueError, 'equivalence label mismatch'):
            self.plan([root(2, [3])], state=reseal(state))
        # Different artist labels cannot share one existing artist group.
        state = make_synthetic_state([root(2, [4])], self.matcher, LIBRARY_HASH, MAP_HASH)
        words = list(parse_metadata(bytes.fromhex(state['rows'][1]['metadata_hex'])))
        words[1] = parse_metadata(bytes.fromhex(state['rows'][0]['metadata_hex']))[1]
        state['rows'][1]['metadata_hex'] = struct.pack('<4Q', *words).hex()
        with self.assertRaisesRegex(ValueError, 'equivalence label mismatch'):
            self.plan([root(2, [4])], state=reseal(state))
        state = copy.deepcopy(self.state)
        state['rows'][0]['similarities_hex'] = struct.pack('<IIIQ', 0, 12, 1, 123).hex()
        with self.assertRaisesRegex(ValueError, 'unassigned ID'):
            self.plan([root(2, [3])], state=reseal(state))

    def test_gid_wrap_uses_unused_uint32_and_group_overflow_fails(self):
        state = make_synthetic_state([root(2, [])], self.matcher, LIBRARY_HASH, MAP_HASH)
        state['rows'][0]['genius_id'] = MAX_GID
        plan = self.plan([root(2, [3])], state=reseal(state))
        self.assertEqual(rows(plan['next_state'])[pid(3)]['genius_id'], 1)
        self.assertEqual(rows(plan['next_state'])[pid(2)]['genius_id'], MAX_GID)
        state['rows'][0]['metadata_hex'] = struct.pack('<4Q', 0, MAX_GROUP_ID, 1, 1).hex()
        with self.assertRaisesRegex(ValueError, 'group ID range exhausted'):
            self.plan([root(2, [1])], state=reseal(state))

    def test_full_graph_unknown_nodes_and_duplicate_roots_fail_without_mutation(self):
        before = json.dumps(self.state, sort_keys=True)
        for graph in ([], [root(2, [99])], [root(2, [3, 3])], [root(2, [3]), root(2, [4])]):
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                self.plan(graph)
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)


if __name__ == '__main__':
    unittest.main()
