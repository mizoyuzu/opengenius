"""Validation tests for per-root offline recommendation preferences."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from root_recommendation_preferences import defaults_for_root, validate_root_preferences


LIBRARY = 'a' * 64


class RootRecommendationPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [{'persistent_id': pid} for pid in ('ROOT_JP', 'ROOT_GAME', 'TARGET')]
        self.classifications = {
            'ROOT_JP': {'tags': ['j-pop', 'rock'], 'kind': 'vocal'},
            'ROOT_GAME': {'tags': ['minecraft', 'game'], 'kind': 'bgm'},
            'TARGET': {'tags': ['game'], 'kind': 'bgm'},
        }
        self.doc = {'schema_version': 1, 'library_sha256': LIBRARY, 'root_preferences': [
            {'root_pid': 'ROOT_JP', 'name': 'J-pop roots', 'tags': ['j-pop'], 'kind': 'vocal',
             'excluded_kinds': [], 'cluster_mode': 'only', 'profile': 'relations-only',
             'artist_preference': 'neutral', 'note': 'Personal preference'},
            {'root_pid': 'ROOT_GAME', 'name': 'Minecraft roots', 'tags': ['minecraft'], 'kind': 'bgm',
             'excluded_kinds': [], 'cluster_mode': 'prefer', 'profile': 'without-compatible-genre',
             'artist_preference': 'slight', 'fallback_tags': ['game'], 'fallback_limit': 4},
        ]}

    def validate(self, doc=None, **kwargs):
        return validate_root_preferences(doc or self.doc, self.tracks, LIBRARY,
                                         self.classifications, **kwargs)

    def test_validation_returns_detached_preferences_and_isolates_each_root(self):
        original = copy.deepcopy(self.doc)
        validated = self.validate()
        self.assertEqual(self.doc, original)
        self.assertEqual(validated, original)
        jp = defaults_for_root(validated, 'ROOT_JP')
        self.assertEqual(jp, {'tags': ['j-pop'], 'kind': 'vocal', 'excluded_kinds': [],
                              'cluster_mode': 'only', 'profile': 'relations-only',
                              'artist_preference': 'neutral'})
        self.assertNotIn('name', jp)
        self.assertNotIn('note', jp)
        self.assertNotIn('root_pid', jp)
        game = defaults_for_root(validated, 'ROOT_GAME')
        self.assertEqual(game['fallback_tags'], ['game'])
        self.assertEqual(game['fallback_limit'], 4)
        self.assertIsNone(defaults_for_root(validated, 'TARGET'))
        jp['tags'].append('mutated')
        self.assertEqual(validated['root_preferences'][0]['tags'], ['j-pop'])

    def test_only_requires_seed_in_primary_scope_but_prefer_allows_outside_seed(self):
        changed = copy.deepcopy(self.doc)
        changed['root_preferences'][0]['tags'] = ['game']
        with self.assertRaisesRegex(ValueError, 'No roots remain'):
            self.validate(changed)
        changed['root_preferences'][0]['cluster_mode'] = 'prefer'
        self.assertEqual(self.validate(changed)['root_preferences'][0]['cluster_mode'], 'prefer')

    def test_library_fields_pids_and_duplicate_roots_are_strict(self):
        with self.assertRaisesRegex(ValueError, 'different Library'):
            validate_root_preferences(self.doc, self.tracks, 'b' * 64, self.classifications)
        with self.assertRaisesRegex(ValueError, 'unknown Library PID'):
            changed = copy.deepcopy(self.doc)
            changed['root_preferences'][0]['root_pid'] = 'UNKNOWN'
            self.validate(changed)
        with self.assertRaisesRegex(ValueError, 'Duplicate root preference PID'):
            changed = copy.deepcopy(self.doc)
            changed['root_preferences'][1]['root_pid'] = 'ROOT_JP'
            self.validate(changed)
        with self.assertRaisesRegex(ValueError, 'not an evaluated root'):
            self.validate(root_pids=['ROOT_JP'])

    def test_invalid_scope_terms_and_modes_are_rejected(self):
        for key, value in (('tags', ['super-unknown-tag']), ('kind', 'nonexistent'),
                           ('excluded_kinds', ['nonexistent']), ('cluster_mode', 'mix'),
                           ('profile', 'invented'), ('artist_preference', 'favorite')):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.doc)
                changed['root_preferences'][0][key] = value
                with self.assertRaises(ValueError):
                    self.validate(changed)
        changed = copy.deepcopy(self.doc)
        changed['root_preferences'][0]['unrecognized'] = True
        with self.assertRaisesRegex(ValueError, 'fields'):
            self.validate(changed)

    def test_fallback_requires_prefer_constrained_scope_and_valid_limit(self):
        changed = copy.deepcopy(self.doc)
        changed['root_preferences'][0]['fallback_tags'] = ['game']
        changed['root_preferences'][0]['fallback_limit'] = 2
        with self.assertRaisesRegex(ValueError, 'Fallbacks require'):
            self.validate(changed)
        changed['root_preferences'][0]['cluster_mode'] = 'prefer'
        changed['root_preferences'][0]['tags'] = []
        changed['root_preferences'][0]['kind'] = None
        with self.assertRaisesRegex(ValueError, 'Fallbacks require'):
            self.validate(changed)
        for value in (True, -1, 26, 1.0):
            with self.subTest(value=value):
                invalid = copy.deepcopy(self.doc)
                invalid['root_preferences'][1]['fallback_limit'] = value
                with self.assertRaisesRegex(ValueError, 'integer from 0 through 25'):
                    self.validate(invalid)
        invalid = copy.deepcopy(self.doc)
        invalid['root_preferences'][1]['fallback_tags'] = ['bogus']
        with self.assertRaisesRegex(ValueError, 'Unknown scope tag'):
            self.validate(invalid)

    def test_optional_known_root_set_and_input_mutation_safety(self):
        original = copy.deepcopy(self.doc)
        with self.assertRaisesRegex(ValueError, 'not an evaluated root'):
            self.validate(root_pids=['ROOT_JP'])
        self.assertEqual(self.doc, original)
        empty = {'schema_version': 1, 'library_sha256': LIBRARY, 'root_preferences': []}
        self.assertEqual(self.validate(empty, root_pids=[]), empty)


if __name__ == '__main__':
    unittest.main()
