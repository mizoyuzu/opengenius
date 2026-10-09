"""Verify each controlled comparison changes only its intended distance knob."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import pack_config, parse_config
from sweep_genius_profiles import distance_profiles
from genius_profiles import artist_preference_config, seed_affinity_order
from music_identity_map import IdentityMap, build_map


class ProfileTests(unittest.TestCase):
    def test_slight_artist_preference_changes_only_initial_artist_distance(self):
        parsed = {'version': 2, 'filters': [
            {'type': 1, 'parameters': [20, 50, 10, 10]},
            {'type': 3, 'parameters': [1, 1, 6, 6]},
            {'type': 3, 'parameters': [2, 1, 10, 6]},
            {'type': 3, 'parameters': [3, 3, 10, 6]},
            {'type': 4, 'parameters': [2, 604800, 0, 50]},
            {'type': 5, 'parameters': [70, 100]}], 'flags': 0, 'result_words': [10, 20]}
        raw = pack_config(parsed)
        self.assertEqual(artist_preference_config(raw), raw)
        expected = parse_config(raw)
        expected['filters'][1]['parameters'][2] = 5
        self.assertEqual(parse_config(artist_preference_config(raw, 'slight')), expected)
        expected['filters'][1]['parameters'][2] = 4
        self.assertEqual(parse_config(artist_preference_config(raw, 'moderate')), expected)
        self.assertEqual(parse_config(raw), parsed)
        expected['filters'][1]['parameters'][2] = 1
        floor = pack_config(expected)
        self.assertEqual(artist_preference_config(floor, 'slight'), floor)
        self.assertEqual(artist_preference_config(floor, 'moderate'), floor)
        relations = pack_config({**parsed, 'filters': [parsed['filters'][0]]})
        self.assertEqual(artist_preference_config(relations, 'slight'), relations)
        self.assertEqual(artist_preference_config(relations, 'moderate'), relations)
        with self.assertRaisesRegex(ValueError, 'preference'):
            artist_preference_config(raw, 'strong')

    def test_minimum_changes_preserve_song_skip_jitter_and_other_distance_words(self):
        config = {'version': 2, 'filters': [
            {'type': 1, 'parameters': [20, 50, 10, 10]},
            {'type': 3, 'parameters': [1, 2, 6, 6]},
            {'type': 3, 'parameters': [2, 2, 10, 6]},
            {'type': 3, 'parameters': [3, 3, 10, 6]},
            {'type': 4, 'parameters': [2, 604800, 0, 50]},
            {'type': 5, 'parameters': [70, 100]}], 'flags': 0, 'result_words': [10, 20]}
        profiles = distance_profiles(pack_config(config))
        self.assertEqual(len(profiles), 8)
        changed = parse_config(profiles['artist-album-minimum-one'])
        for original, actual in zip(config['filters'], changed['filters']):
            if original['type'] == 3 and original['parameters'][0] in (1, 2):
                self.assertEqual(actual['parameters'], [original['parameters'][0], 1, *original['parameters'][2:]])
            else:
                self.assertEqual(actual, original)
        removed = parse_config(profiles['without-artist-album-distance'])['filters']
        self.assertEqual(removed, [f for f in config['filters'] if f['type'] != 3 or f['parameters'][0] == 3])
        self.assertEqual(parse_config(profiles['baseline']), config)

    def test_unobserved_distance_config_rejected(self):
        config = {'version': 2, 'filters': [{'type': 1, 'parameters': [20, 50, 10, 10]}],
                  'flags': 0, 'result_words': [10, 20]}
        with self.assertRaisesRegex(ValueError, 'indices'):
            distance_profiles(pack_config(config))

    def test_seed_affinity_is_bounded_exact_and_keeps_empty_or_generic_values_neutral(self):
        tracks = [
            {'persistent_id': 'ROOT', 'artist': 'Seed Artist', 'album': 'Seed Album'},
            {'persistent_id': 'X1', 'artist': 'Other', 'album': 'Various Artists'},
            {'persistent_id': 'X2', 'artist': '', 'album': ''},
            {'persistent_id': 'X3', 'artist': 'Seed Artist', 'album': 'Another Album'},
            {'persistent_id': 'X4', 'artist': 'Other', 'album': 'Seed Album'},
            {'persistent_id': 'X5', 'artist': 'Seed Artist', 'album': 'Seed Album'},
            {'persistent_id': 'X6', 'artist': 'Other', 'album': 'Another Album'},
            {'persistent_id': 'X7', 'artist': 'Other', 'album': 'Another Album'},
        ]
        matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        original = ['ROOT', 'X1', 'X2', 'X3', 'X4', 'X5', 'X6', 'X7']
        hops = {pid: index for index, pid in enumerate(original)}
        neutral, info = seed_affinity_order(original, tracks, matcher, 'ROOT', 'neutral', hops)
        self.assertEqual(neutral, original)
        self.assertFalse(info['changed'])
        tuned, info = seed_affinity_order(original, tracks, matcher, 'ROOT', 'moderate', hops)
        self.assertEqual(tuned[0], 'ROOT')
        self.assertLess(tuned.index('X5'), original.index('X5'))
        self.assertTrue(all(abs(item['rank_movement']) <= 4 for item in info['candidates']))
        by_pid = {item['persistent_id']: item for item in info['candidates']}
        self.assertFalse(by_pid['X1']['album_title_match'])
        self.assertFalse(by_pid['X2']['artist_credit_match'])
        self.assertEqual(by_pid['X5']['affinity_score'], 3)
        self.assertEqual(by_pid['X5']['observed_relation_hops'], hops['X5'])
        with self.assertRaisesRegex(ValueError, 'preference'):
            seed_affinity_order(original, tracks, matcher, 'ROOT', 'strong')

    def test_affinity_rejects_unversioned_candidate_shapes_and_anchors_the_seed(self):
        tracks = [{'persistent_id': 'ROOT', 'artist': 'A', 'album': 'Album'},
                  {'persistent_id': 'OTHER', 'artist': 'B', 'album': 'Other'}]
        matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        with self.assertRaisesRegex(ValueError, 'unique ordered'):
            seed_affinity_order(['ROOT', 'ROOT'], tracks, matcher, 'ROOT', 'slight')
        reordered, _ = seed_affinity_order(['OTHER', 'ROOT'], tracks, matcher, 'ROOT', 'slight')
        self.assertEqual(reordered, ['ROOT', 'OTHER'])
        with self.assertRaisesRegex(ValueError, 'source-hop'):
            seed_affinity_order(['ROOT'], tracks, matcher, 'ROOT', 'slight', {'ROOT': -1})

    def test_artist_affinity_uses_complete_canonical_credit_and_rejects_various_artists(self):
        tracks = [
            {'persistent_id': 'ROOT', 'artist': 'Rita & VISUAL ARTS / Key', 'album': 'Seed Album'},
            {'persistent_id': 'SOLO', 'artist': 'Rita', 'album': 'Other Album'},
            {'persistent_id': 'ALIAS', 'artist': 'Rita + Key', 'album': 'Other Album'},
            {'persistent_id': 'VA_ROOT', 'artist': 'Various Artists', 'album': 'VA Album'},
            {'persistent_id': 'VA_COPY', 'artist': 'V.A.', 'album': 'Other Album'},
        ]
        document = build_map(tracks, 'hash')
        document['artist_credit_sets'] = [{'enabled': True,
                                           'labels': ['Rita & VISUAL ARTS / Key', 'Rita + Key'],
                                           'components': ['Rita', 'VISUAL ARTS / Key']}]
        matcher = IdentityMap(document, tracks, 'hash')
        tuned, info = seed_affinity_order(['ROOT', 'SOLO', 'ALIAS'], tracks, matcher, 'ROOT', 'slight')
        self.assertEqual(tuned, ['ROOT', 'ALIAS', 'SOLO'])
        matches = {item['persistent_id']: item['artist_credit_match'] for item in info['candidates']}
        self.assertTrue(matches['ALIAS'])
        self.assertFalse(matches['SOLO'])
        va_order, va_info = seed_affinity_order(['VA_ROOT', 'VA_COPY'], tracks, matcher, 'VA_ROOT', 'moderate')
        self.assertEqual(va_order, ['VA_ROOT', 'VA_COPY'])
        self.assertFalse(va_info['candidates'][1]['artist_credit_match'])


if __name__ == '__main__':
    unittest.main()
