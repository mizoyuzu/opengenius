"""Offline paired-state preparation preserves all other data and codec guards."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inspect_music_library import decode_musicdb
from prepare_music_genius_state import (encode_preferences, inspect_state_pair,
    prepare_state_blobs, prepare_pair)
from prepare_macos_real_rebase import prepare_output_state


def fixture(state, *, legacy=False, selector=0):
    # Deliberately synthetic, never evidence of native Music acceptance.
    expanded = bytearray(194)
    expanded[:4] = b'plma'
    struct.pack_into('<II', expanded, 4, 194, 1)
    expanded[12] = state
    key_header = b'\x00\x01' + selector.to_bytes(2, 'big') + bytes(16)
    expanded += b'boma' + struct.pack('<IIII', 20, 40, 502, 0) + key_header
    expanded += b'ltma' + struct.pack('<II', 12, 0)
    header = bytearray(160)
    header[:4] = b'hfma'
    struct.pack_into('<II', header, 4, 160, 160)
    struct.pack_into('<I', header, 12, 0x320009 if legacy else 0x1f000c)
    version = b'1.5.6.11' if legacy else b'1.6.6.4'
    header[16:16+len(version)] = version
    struct.pack_into('<I', header, 84, 102400)
    return encode_preferences(bytes(expanded), bytes(header))


class PairedGeniusStateTests(unittest.TestCase):
    def pair(self, library=0, preferences=0, **kw):
        return {'Library.musicdb': fixture(library, **kw),
                'Library Preferences.musicdb': fixture(preferences, **kw),
                'Genius.itdb': b'untouched-data'}

    def test_preferences_only_enable_is_visible_then_repaired_in_both_files(self):
        source = self.pair(0, 1)
        observed = inspect_state_pair(source)
        self.assertFalse(observed['states_match'])
        self.assertFalse(observed['locally_enabled'])
        prepared, report = prepare_state_blobs(source)
        self.assertTrue(inspect_state_pair(prepared)['locally_enabled'])
        self.assertEqual(prepared['Genius.itdb'], source['Genius.itdb'])
        self.assertEqual(inspect_state_pair(source), observed)
        for name in report:
            before, after = decode_musicdb(source[name]), decode_musicdb(prepared[name])
            restored = bytearray(after)
            restored[report[name]['expanded_offset']] = before[report[name]['expanded_offset']]
            self.assertEqual(bytes(restored), before)
            self.assertTrue(report[name]['key_header_preserved'])

    def test_legacy_guard_is_not_relaxed_by_local_enable(self):
        source = self.pair(legacy=True)
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            prepare_state_blobs(source)
        prepared, _ = prepare_state_blobs(source, allow_unverified_profile=True)
        self.assertTrue(inspect_state_pair(prepared)['locally_enabled'])

    def test_transitional_state_and_mismatching_key_refuse_before_output(self):
        cases = [self.pair(2, 0), {**self.pair(), 'Library Preferences.musicdb': fixture(0, selector=1)}]
        for source in cases:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_dir = root / 'source'; input_dir.mkdir()
                for name in ('Library.musicdb', 'Library Preferences.musicdb'):
                    (input_dir / name).write_bytes(source[name])
                with self.assertRaises(ValueError):
                    prepare_pair(input_dir, root / 'output')
                self.assertFalse((root / 'output').exists())
        state = inspect_state_pair(self.pair(2, 2))
        self.assertTrue(state['states_match'])
        self.assertFalse(state['locally_enabled'])

    def test_rebase_default_preserves_state_explicit_option_prepares_both(self):
        source = self.pair(0, 1)
        files, report = prepare_output_state(source, source['Library.musicdb'], b'new-genius', False)
        self.assertEqual(files['Library.musicdb'], source['Library.musicdb'])
        self.assertEqual(files['Library Preferences.musicdb'], source['Library Preferences.musicdb'])
        self.assertFalse(report['after']['states_match'])
        files, report = prepare_output_state(source, source['Library.musicdb'], b'new-genius', True)
        self.assertTrue(report['after']['locally_enabled'])
        self.assertEqual(files['Genius.itdb'], b'new-genius')
        self.assertIsNotNone(report['changes'])


if __name__ == '__main__':
    unittest.main()
