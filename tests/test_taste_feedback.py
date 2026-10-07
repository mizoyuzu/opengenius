"""Offline tests for Library-bound, pair-specific personal playlist feedback."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from taste_feedback import (compare_feedback, empty_feedback, feedback_context,
                            record_feedback, summarize_feedback, validate_feedback)


LIBRARY = 'a' * 64
OBSERVATION_A = 'b' * 64
OBSERVATION_B = 'c' * 64


class TasteFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [{'persistent_id': pid} for pid in ('ROOT_A', 'ROOT_B', 'TARGET', 'OTHER')]
        self.evaluation = {
            'library_sha256': LIBRARY,
            'profile': 'relations-only',
            'artist_preference': 'neutral',
            'cluster_config_semantic_sha256': 'd' * 64,
            'cluster_scope': {'tags': ['jazz'], 'kind': None, 'excluded_kinds': [], 'mode': 'only',
                              'preferred_pids': ['ROOT_A'], 'removed_target_count': 0},
            'input_sha256': {'genius_reference': 'e' * 64},
            'observation_inputs': [{'path': '/ignored/a.json', 'sha256': OBSERVATION_A}],
            'input_provenance': {
                'observations': [{'path': '/web/old-a.json', 'sha256': '1' * 64},
                                 {'path': '/web/old-b.json', 'sha256': '2' * 64}],
                'identity_map_sha256': '3' * 64, 'executable_sha256': '4' * 64,
                'genius_reference_sha256': '5' * 64,
            },
            'root_results': [
                {'root_pid': 'ROOT_A', 'playlist_pids': ['ROOT_A', 'TARGET', 'OTHER']},
                {'root_pid': 'ROOT_B', 'playlist_pids': ['ROOT_B', 'TARGET']},
            ],
        }
        self.doc = empty_feedback(LIBRARY)
        self.context = feedback_context(self.evaluation)

    def judgment(self, root='ROOT_A', target='TARGET', verdict='fits', reason='taste', **extra):
        payload = {'root_pid': root, 'target_pid': target, 'verdict': verdict,
                   'reason': reason, 'context_sha256': self.context}
        payload.update(extra)
        self.doc = record_feedback(self.doc, payload, self.tracks, LIBRARY)
        return payload

    def test_schema_strictly_binds_library_and_known_pairs(self):
        self.assertEqual(validate_feedback(self.doc, self.tracks, LIBRARY), self.doc)
        with self.assertRaisesRegex(ValueError, 'different Library'):
            validate_feedback(self.doc, self.tracks, 'f' * 64)
        invalid = copy.deepcopy(self.doc)
        invalid['extra'] = True
        with self.assertRaisesRegex(ValueError, 'fields'):
            validate_feedback(invalid, self.tracks, LIBRARY)
        with self.assertRaisesRegex(ValueError, 'unknown Library PID'):
            record_feedback(self.doc, {'root_pid': 'MISSING', 'target_pid': 'TARGET',
                                       'verdict': 'fits', 'reason': 'taste',
                                       'context_sha256': self.context}, self.tracks, LIBRARY)
        with self.assertRaisesRegex(ValueError, 'itself'):
            record_feedback(self.doc, {'root_pid': 'ROOT_A', 'target_pid': 'ROOT_A',
                                       'verdict': 'avoid', 'reason': 'taste',
                                       'context_sha256': self.context}, self.tracks, LIBRARY)

    def test_pair_specific_judgment_does_not_infer_global_dislike(self):
        self.judgment('ROOT_A', 'TARGET', 'avoid', 'taste')
        summary = summarize_feedback(self.evaluation, self.doc, self.tracks, LIBRARY)
        self.assertEqual(summary['roots']['ROOT_A']['avoid'], 1)
        self.assertEqual(summary['roots']['ROOT_B']['unjudged'], 1)
        self.assertEqual(summary['counts']['all_pair_avoid'], 1)
        self.assertEqual(summary['identity_status'], 'unverified')

    def test_wrong_recording_reason_is_retained_as_identity_error_not_taste(self):
        self.judgment(verdict='avoid', reason='wrong_recording', note='Different live version')
        saved, = self.doc['judgments']
        self.assertEqual(saved['reason'], 'wrong_recording')
        self.assertEqual(saved['note'], 'Different live version')
        counts = summarize_feedback(self.evaluation, self.doc, self.tracks, LIBRARY)['counts']
        self.assertEqual(counts['avoid'], 1)
        self.assertEqual(counts['taste_avoid'], 0)
        self.assertEqual(counts['issue_wrong_recording'], 1)
        self.assertEqual(counts['reason_wrong_recording'], 1)

    def test_upsert_and_clear_are_immutable_and_unique(self):
        original = self.doc
        self.judgment()
        first = self.doc
        self.assertEqual(original['judgments'], [])
        updated = record_feedback(first, {'root_pid': 'ROOT_A', 'target_pid': 'TARGET',
                                          'verdict': 'avoid', 'reason': 'too_repetitive',
                                          'context_sha256': self.context}, self.tracks, LIBRARY)
        self.assertEqual(len(updated['judgments']), 1)
        self.assertEqual(updated['judgments'][0]['verdict'], 'avoid')
        cleared = record_feedback(updated, {'root_pid': 'ROOT_A', 'target_pid': 'TARGET', 'verdict': None},
                                  self.tracks, LIBRARY)
        self.assertEqual(cleared['judgments'], [])
        self.assertEqual(updated['judgments'][0]['verdict'], 'avoid')

    def test_invalid_update_leaves_source_document_untouched(self):
        self.judgment()
        before = copy.deepcopy(self.doc)
        with self.assertRaisesRegex(ValueError, 'reason'):
            record_feedback(self.doc, {'root_pid': 'ROOT_A', 'target_pid': 'OTHER',
                                       'verdict': 'avoid', 'reason': 'global_dislike',
                                       'context_sha256': self.context}, self.tracks, LIBRARY)
        self.assertEqual(self.doc, before)
        duplicate = copy.deepcopy(before)
        duplicate['judgments'].append(copy.deepcopy(duplicate['judgments'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate judgment pair'):
            record_feedback(duplicate, {'root_pid': 'ROOT_A', 'target_pid': 'OTHER', 'verdict': None},
                            self.tracks, LIBRARY)

    def test_context_tracks_semantic_inputs_but_ignores_subset_and_dynamic_results(self):
        altered = copy.deepcopy(self.evaluation)
        altered['root_results'] = altered['root_results'][:1]
        altered['root_results'][0]['playlist_pids'] = ['ROOT_A']
        altered['observation_inputs'][0]['path'] = '/different/path.json'
        altered['input_provenance']['observations'][0]['path'] = '/web/new-path.json'
        self.assertEqual(feedback_context(altered), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['profile'] = 'relations-only-alt'
        self.assertNotEqual(feedback_context(changed), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['observation_inputs'][0]['sha256'] = OBSERVATION_B
        self.assertNotEqual(feedback_context(changed), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['input_provenance']['observations'][0]['sha256'] = '6' * 64
        self.assertNotEqual(feedback_context(changed), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['input_provenance']['observations'].reverse()
        self.assertNotEqual(feedback_context(changed), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['input_provenance']['identity_map_sha256'] = '6' * 64
        self.assertNotEqual(feedback_context(changed), self.context)
        changed = copy.deepcopy(self.evaluation)
        changed['artist_preference'] = 'avoid-same-artist'
        self.assertNotEqual(feedback_context(changed), self.context)

    def test_scope_canonicalizes_constraint_order_and_ignores_diagnostics(self):
        changed = copy.deepcopy(self.evaluation)
        changed['cluster_scope']['tags'] = ['rock', 'jazz']
        changed['cluster_scope']['excluded_kinds'] = ['off_vocal', 'unknown']
        expected = feedback_context(changed)
        reordered = copy.deepcopy(changed)
        reordered['cluster_scope']['tags'].reverse()
        reordered['cluster_scope']['excluded_kinds'].reverse()
        reordered['cluster_scope']['preferred_pids'] = ['TARGET', 'OTHER']
        reordered['cluster_scope']['removed_target_count'] = 100
        reordered['cluster_scope']['excluded_root_pids'] = ['ROOT_B']
        self.assertEqual(feedback_context(reordered), expected)
        changed_constraints = copy.deepcopy(reordered)
        changed_constraints['cluster_scope']['kind'] = 'live'
        self.assertNotEqual(feedback_context(changed_constraints), expected)

    def test_summary_separates_current_context_from_historical_exposure(self):
        self.judgment(verdict='avoid')
        changed = copy.deepcopy(self.evaluation)
        changed['profile'] = 'artist-album-minimum-one'
        summary = summarize_feedback(changed, self.doc, self.tracks, LIBRARY)
        self.assertEqual(summary['counts']['avoid'], 0)
        self.assertEqual(summary['counts']['context_mismatch'], 1)
        self.assertEqual(summary['counts']['all_pair_avoid'], 1)
        self.assertEqual(summary['counts']['taste_avoid'], 0)
        self.assertEqual(summary['context_mismatches'][0]['reason'], 'taste')

    def test_unrated_targets_are_never_counted_as_good(self):
        summary = summarize_feedback(self.evaluation, self.doc, self.tracks, LIBRARY)
        self.assertEqual(summary['counts']['fits'], 0)
        self.assertEqual(summary['counts']['avoid'], 0)
        self.assertEqual(summary['counts']['unjudged'], 3)

    def test_comparison_uses_common_roots_and_has_no_winner(self):
        self.judgment('ROOT_A', 'TARGET', 'fits')
        self.judgment('ROOT_B', 'TARGET', 'avoid')
        baseline = copy.deepcopy(self.evaluation)
        variant = copy.deepcopy(self.evaluation)
        baseline['root_results'] = [self.evaluation['root_results'][0]]
        variant['root_results'] = [self.evaluation['root_results'][1]]
        variant['root_results'].append({'root_pid': 'OTHER', 'playlist_pids': ['OTHER', 'TARGET']})
        compared = compare_feedback(baseline, variant, self.doc, self.tracks, LIBRARY)
        self.assertEqual(compared['common_roots'], [])
        self.assertIsNone(compared['winner'])
        variant['root_results'] = [self.evaluation['root_results'][0], self.evaluation['root_results'][1]]
        compared = compare_feedback(baseline, variant, self.doc, self.tracks, LIBRARY)
        self.assertEqual(compared['common_roots'], ['ROOT_A'])
        self.assertEqual(compared['baseline']['counts']['fits'], 1)
        self.assertEqual(compared['variant']['counts']['fits'], 1)

    def test_bounded_fallback_settings_change_feedback_context(self):
        report = copy.deepcopy(self.evaluation)
        report['cluster_scope'].update(mode='prefer', fallback_tags=['Game', 'Ambient'], fallback_limit=2)
        first = feedback_context(report)
        report['cluster_scope']['fallback_tags'].reverse()
        self.assertEqual(first, feedback_context(report))
        report['cluster_scope']['fallback_limit'] = 3
        self.assertNotEqual(first, feedback_context(report))


if __name__ == '__main__':
    unittest.main()
