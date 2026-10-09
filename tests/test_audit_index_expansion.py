"""Offline checks for the incremental index auditor."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_index_expansion import audit_rows, _core_sample
from genius_format import pack_config
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state, plan_genius_update
from update_genius_bundle import state_rows


LIBRARY_HASH = 'a' * 64
MAP_HASH = 'b' * 64


def pid(number):
    return f'{number:016X}'


def root(number, targets):
    return {'root_pid': pid(number), 'ordered_target_pids': [pid(target) for target in targets]}


def db_rows(state):
    _, metadata, relations = state_rows(state)
    return metadata, relations


def library_rows(state, source_tracks):
    ids, _, _ = state_rows(state)
    return [{**track, 'genius_id': f'{ids[track["persistent_id"]]:016X}'
             if track['persistent_id'] in ids else '0000000000000000'}
            for track in source_tracks]


class AuditIndexExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': pid(1), 'title': 'Alpha', 'artist': 'Alpha', 'album': 'A', 'duration_ms': 100000},
            {'persistent_id': pid(2), 'title': 'Beta', 'artist': 'Beta', 'album': 'B', 'duration_ms': 100000},
            {'persistent_id': pid(3), 'title': 'Gamma', 'artist': 'Gamma', 'album': 'C', 'duration_ms': 100000},
            {'persistent_id': pid(4), 'title': 'Delta', 'artist': 'Delta', 'album': 'D', 'duration_ms': 100000},
        ]
        matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH), self.tracks, LIBRARY_HASH)
        self.baseline = make_synthetic_state([root(2, [3])], matcher, LIBRARY_HASH, MAP_HASH)
        self.expanded = plan_genius_update(
            self.baseline, [root(2, [1, 3]), root(1, [3])], matcher,
            LIBRARY_HASH, MAP_HASH)['next_state']
        self.config = pack_config({'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                                   'flags': 0, 'result_words': [10, 20]})

    def audit(self, baseline=None, expanded=None, config=None, output_db=None):
        baseline = self.baseline if baseline is None else baseline
        expanded = self.expanded if expanded is None else expanded
        baseline_meta, baseline_rel = db_rows(baseline)
        output_meta, output_rel = db_rows(expanded) if output_db is None else output_db
        return audit_rows(baseline, expanded, self.config,
                          self.config if config is None else config,
                          baseline_meta, baseline_rel, output_meta, output_rel,
                          library_rows(baseline, self.tracks), library_rows(expanded, self.tracks))

    def test_reports_preservation_growth_and_relation_counts(self):
        report = self.audit()
        self.assertEqual(report['existing_pid_count'], 2)
        self.assertEqual(report['output_indexed_pid_count'], 3)
        self.assertEqual(report['added_indexed_pid_count'], 1)
        self.assertEqual(report['baseline_directed_edge_count'], 1)
        self.assertEqual(report['output_directed_edge_count'], 3)
        self.assertTrue(report['existing_pid_gid_and_metadata_preserved'])
        self.assertTrue(report['genius_config_preserved'])

    def test_rejects_changed_existing_assignment_or_metadata(self):
        altered = copy.deepcopy(self.expanded)
        row = next(row for row in altered['rows'] if row['persistent_id'] == pid(2))
        row['metadata_hex'] = '00' * 32
        from plan_genius_update import _seal
        altered = _seal({key: value for key, value in altered.items() if key != 'state_sha256'})
        with self.assertRaisesRegex(ValueError, 'Existing PID/GID'):
            self.audit(expanded=altered)

    def test_rejects_state_and_decrypted_sql_mismatch_or_unknown_relation_id(self):
        output_meta, output_rel = db_rows(self.expanded)
        bad_meta = dict(output_meta)
        bad_meta.pop(next(iter(bad_meta)))
        with self.assertRaisesRegex(ValueError, 'SQL rows differ'):
            self.audit(output_db=(bad_meta, output_rel))
        # Corrupt the sealed relation blob so its target is outside the metadata IDs.
        from genius_format import pack_similarities
        bad_rel = dict(output_rel)
        bad_rel[next(iter(bad_rel))] = pack_similarities(0, [0xFFFFFFFE])
        with self.assertRaisesRegex(ValueError, 'state'):
            self.audit(output_db=(output_meta, bad_rel))

    def test_rejects_configuration_or_library_id_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.audit(config=b'changed')
        bad_tracks = library_rows(self.expanded, self.tracks)
        bad_tracks[1]['genius_id'] = '0000000000000042'
        base_meta, base_rel = db_rows(self.baseline)
        out_meta, out_rel = db_rows(self.expanded)
        with self.assertRaisesRegex(ValueError, 'Library Genius IDs'):
            audit_rows(self.baseline, self.expanded, self.config, self.config,
                       base_meta, base_rel, out_meta, out_rel,
                       library_rows(self.baseline, self.tracks), bad_tracks)

    def test_persisted_core_sample_rejects_unreachable_output(self):
        ids, metadata, relations = state_rows(self.expanded)
        # Root 3 has no outgoing edges; indexed PID 1 is outside its graph.
        returned = {'result_genius_ids': [f'{ids[pid(1)]:016X}'], 'instructions': 10}
        with patch('audit_index_expansion.MusicCore') as core:
            core.return_value.generate.return_value = returned
            with self.assertRaisesRegex(ValueError, 'outside the persisted'):
                _core_sample('unused', self.config, metadata, relations, ids, [pid(3)])
            core.return_value.generate.return_value = {
                'result_genius_ids': [f'{ids[pid(2)]:016X}', f'{ids[pid(1)]:016X}'], 'instructions': 10}
            sampled = _core_sample('unused', self.config, metadata, relations, ids, [pid(2)])
            self.assertTrue(sampled[0]['all_generated_tracks_reachable'])


if __name__ == '__main__':
    unittest.main()
