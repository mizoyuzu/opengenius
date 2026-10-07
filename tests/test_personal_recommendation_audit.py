"""Offline checks for structural and explicit-feedback recommendation audits."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_personal_recommendations import _evaluation, audit_personal_recommendations
from music_identity_map import IdentityMap
from taste_feedback import empty_feedback, feedback_context, record_feedback


LIBRARY = 'a' * 64
ROOT_A = '0000000000000001'
ROOT_B = '0000000000000002'
ROOT_C = '0000000000000003'
TARGET = '0000000000000004'
MISSING_ARTIST = '0000000000000005'


class PersonalRecommendationAuditTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            dict(persistent_id=ROOT_A, title='Seed A', artist='Seed Artist'),
            dict(persistent_id=ROOT_B, title='Seed B', artist='Seed Artist'),
            dict(persistent_id=ROOT_C, title='Seed C', artist='Other Seed'),
            dict(persistent_id=TARGET, title='Target (Game Size)', artist='Target Artist'),
            dict(persistent_id=MISSING_ARTIST, title='Uncredited Candidate', artist=''),
        ]
        self.identity = dict(schema_version=1, library_sha256=LIBRARY, artist_alias_groups=[])
        self.matcher = IdentityMap(self.identity, self.tracks, LIBRARY)
        self.evaluation = self.make_evaluation([
            (ROOT_A, [ROOT_A, TARGET, MISSING_ARTIST]),
            (ROOT_B, [ROOT_B, TARGET]),
        ], [
            (ROOT_A, [TARGET, MISSING_ARTIST]),
            (ROOT_B, [TARGET]),
            (TARGET, [MISSING_ARTIST]),
        ])

    def make_evaluation(self, playlists, relations):
        relation_rows = [dict(root_pid=root, ordered_target_pids=targets)
                         for root, targets in relations]
        root_results = []
        edges = {root: set(targets) for root, targets in relations}
        for root, pids in playlists:
            entries = []
            for pid in pids:
                if pid == root:
                    path = [root]
                elif pid == TARGET:
                    path = [root, TARGET] if TARGET in edges[root] else [root, TARGET]
                else:
                    path = [root, MISSING_ARTIST] if MISSING_ARTIST in edges[root] else [root, TARGET, MISSING_ARTIST]
                entries.append(dict(persistent_id=pid,
                                    observed_relation_path=path,
                                    observed_relation_hops=len(path) - 1))
            root_results.append(dict(root_pid=root, playlist_pids=list(pids), playlist=entries))
        return dict(library_sha256=LIBRARY, profile='relations-only', artist_preference='neutral',
                    relation_rows=relation_rows, root_results=root_results)

    def test_exposure_concentration_is_distinct_from_unobserved_library_tracks(self):
        report = audit_personal_recommendations(self.evaluation, self.tracks, self.matcher, LIBRARY)
        coverage = report['structural_coverage']
        self.assertEqual(coverage['library_track_count'], 5)
        self.assertEqual(coverage['observed_root_count'], 3)
        self.assertEqual(coverage['evaluated_root_count'], 2)
        self.assertEqual(coverage['root_coverage'], 3 / 5)
        self.assertEqual(coverage['distinct_observed_candidate_count'], 2)
        self.assertEqual(coverage['distinct_reachable_node_count'], 2)
        self.assertEqual(coverage['participating_node_count'], 4)
        self.assertIn('does not mean unrelated', coverage['unseen_tracks_interpretation'])
        exposure = report['exposure']
        self.assertEqual(exposure['nonroot_exposure_slots'], 3)
        self.assertEqual(exposure['top_nonroot_pids'][0]['persistent_id'], TARGET)
        self.assertEqual(exposure['top_nonroot_pids'][0]['count'], 2)
        self.assertEqual(exposure['top_nonroot_pids'][0]['denominator'], 3)
        self.assertEqual(exposure['missing_artist_exposure_slots'], 1)
        self.assertIsNone(report['quality_winner'])

    def test_classification_and_version_counts_are_explicit_and_unknown_stays_unknown(self):
        config = dict(schema_version=1, source_library_sha256=LIBRARY,
                      rules=[dict(match={'persistent_ids': [TARGET]}, tags=['game'], kind='bgm')],
                      overrides={})
        report = audit_personal_recommendations(self.evaluation, self.tracks, self.matcher, LIBRARY,
                                                cluster_config=config)
        classification = report['classification']
        self.assertEqual(classification['evaluated_tracks']['kind_counts']['bgm'], 1)
        self.assertEqual(classification['evaluated_tracks']['kind_counts']['unknown'], 3)
        self.assertEqual(classification['evaluated_tracks']['kind_counts']['vocal'], 0)
        self.assertEqual(report['paths']['playlist_slots'], 5)
        self.assertEqual(report['paths']['missing_path_count'], 0)
        self.assertEqual(report['version_facets']['marker_exposure_slots']['game_size'], 2)
        self.assertEqual(report['version_facets']['marker_unique_tracks']['game_size'], 1)

    def test_common_space_child_inherits_missing_wrapper_provenance_but_keeps_conflict(self):
        child = copy.deepcopy(self.evaluation)
        child.pop('library_sha256')
        child['controlled_config_sha256'] = 'b' * 64
        wrapper = dict(library_sha256=LIBRARY, input_provenance={'identity_map_sha256': 'c' * 64},
                       input_sha256={'executable': 'd' * 64},
                       graph_comparison={'expanded': child})
        inherited = _evaluation(wrapper)
        self.assertEqual(inherited['library_sha256'], LIBRARY)
        self.assertEqual(inherited['input_provenance'], wrapper['input_provenance'])
        self.assertEqual(inherited['input_sha256'], wrapper['input_sha256'])
        self.assertEqual(inherited['controlled_config_sha256'], 'b' * 64)
        conflict = dict(wrapper, graph_comparison={'expanded': dict(child, library_sha256='e' * 64)})
        self.assertEqual(_evaluation(conflict)['library_sha256'], 'e' * 64)

    def test_feedback_keeps_reason_classes_separate_and_unrated_is_not_good(self):
        feedback = empty_feedback(LIBRARY)
        context = feedback_context(self.evaluation)
        feedback = record_feedback(feedback, dict(root_pid=ROOT_A, target_pid=TARGET, verdict='avoid',
                                                 reason='wrong_version', context_sha256=context),
                                  self.tracks, LIBRARY)
        feedback = record_feedback(feedback, dict(root_pid=ROOT_B, target_pid=TARGET, verdict='avoid',
                                                 reason='taste', context_sha256=context),
                                  self.tracks, LIBRARY)
        report = audit_personal_recommendations(self.evaluation, self.tracks, self.matcher, LIBRARY,
                                                feedback=feedback)
        counts = report['feedback']['counts']
        self.assertEqual(counts['avoid'], 2)
        self.assertEqual(counts['taste_avoid'], 1)
        self.assertEqual(counts['issue_wrong_version'], 1)
        self.assertEqual(counts['unjudged'], 1)
        self.assertEqual(counts['fits'], 0)
        self.assertEqual(counts['reason_wrong_version'], 1)

    def test_comparison_uses_common_roots_and_has_no_winner(self):
        comparison = self.make_evaluation([
            (ROOT_B, [ROOT_B, TARGET]),
            (ROOT_C, [ROOT_C, TARGET]),
        ], [
            (ROOT_B, [TARGET]),
            (ROOT_C, [TARGET]),
            (TARGET, [MISSING_ARTIST]),
        ])
        feedback = empty_feedback(LIBRARY)
        context = feedback_context(self.evaluation)
        feedback = record_feedback(feedback, dict(root_pid=ROOT_B, target_pid=TARGET, verdict='fits',
                                                 reason='taste', context_sha256=context),
                                  self.tracks, LIBRARY)
        report = audit_personal_recommendations(self.evaluation, self.tracks, self.matcher, LIBRARY,
                                                feedback=feedback, comparison=comparison)
        self.assertEqual(report['comparison']['common_roots'], [ROOT_B])
        self.assertEqual(report['comparison']['feedback_comparison']['common_roots'], [ROOT_B])
        self.assertIsNone(report['comparison']['winner'])
        self.assertIsNone(report['comparison']['feedback_comparison']['winner'])

    def test_malformed_evaluated_pid_or_saved_path_fails_closed(self):
        bad_pid = copy.deepcopy(self.evaluation)
        bad_pid['root_results'][0]['playlist_pids'][1] = 'unknown'
        bad_pid['root_results'][0]['playlist'][1]['persistent_id'] = 'unknown'
        with self.assertRaises(ValueError):
            audit_personal_recommendations(bad_pid, self.tracks, self.matcher, LIBRARY)
        bad_path = copy.deepcopy(self.evaluation)
        bad_path['root_results'][0]['playlist'][1]['observed_relation_path'] = [ROOT_A, MISSING_ARTIST, TARGET]
        with self.assertRaisesRegex(ValueError, 'path'):
            audit_personal_recommendations(bad_path, self.tracks, self.matcher, LIBRARY)


if __name__ == '__main__':
    unittest.main()
