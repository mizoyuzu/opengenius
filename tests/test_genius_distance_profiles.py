"""Verify each controlled comparison changes only its intended distance knob."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import pack_config, parse_config
from sweep_genius_profiles import distance_profiles


class ProfileTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
