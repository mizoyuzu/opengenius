import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_genius_mix import address_references, inspect, matching_strings


def code(*words):
    return struct.pack('<' + 'I' * len(words), *words)


class MixProbeTests(unittest.TestCase):
    BASE = 0x100000000
    TARGET = BASE + 0x120
    ADRP_X8 = 0x90000008  # adrp x8, current page
    ADD_X0_X8 = 0x91048100  # add x0, x8, #0x120

    def references(self, raw, end=None):
        return address_references(raw, self.BASE, {self.TARGET: 'GeniusMix'},
                                  lambda pc: end or self.BASE + len(raw))

    def test_nonadjacent_reference_survives_unrelated_register_write(self):
        raw = code(self.ADRP_X8, 0x52800029, self.ADD_X0_X8)  # mov w9, #1
        refs = self.references(raw)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['pc'], hex(self.BASE + 8))
        self.assertEqual(refs[0]['address'], hex(self.TARGET))

    def test_overwritten_page_register_and_control_flow_are_not_references(self):
        for word in (0x52800028, 0xD2800028, 0x94000001, 0x14000001,
                     0x54000020, 0xB4000020, 0xD65F03C0, 0xD4000001,
                     0xFFFFFFFF):
            with self.subTest(word=hex(word)):
                self.assertEqual(self.references(code(self.ADRP_X8, word, self.ADD_X0_X8)), [])

    def test_function_boundary_and_32_bit_add_do_not_form_reference(self):
        raw = code(self.ADRP_X8, self.ADD_X0_X8)
        self.assertEqual(self.references(raw, self.BASE + 4), [])
        self.assertEqual(self.references(code(self.ADRP_X8, 0x11048100)), [])

    def test_add_to_page_register_stops_tracking_after_reference(self):
        raw = code(self.ADRP_X8, 0x91048108, self.ADD_X0_X8)
        self.assertEqual(len(self.references(raw)), 1)

    def test_negative_adrp_page_displacement(self):
        raw = code(0xF0FFFFE8, self.ADD_X0_X8)  # adrp x8, preceding page
        refs = address_references(raw, self.BASE + 4096,
                                  {self.TARGET: 'GeniusMix'},
                                  lambda pc: self.BASE + 4096 + len(raw))
        self.assertEqual(refs[0]['address'], hex(self.TARGET))

    def test_full_string_start_not_substring_or_unterminated_tail(self):
        class Image:
            sections = [dict(name='__cstring', address=0x1000, offset=0, size=100)]

            def blob(self, offset, size):
                return b'ordinary\0prefix GeniusMix suffix\0genius-mix-unterminated'

        self.assertEqual(matching_strings(Image()), {0x1009: 'prefix GeniusMix suffix'})

    @unittest.skipUnless(os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE'),
                         'Set OPENGENIUS_MUSIC_EXECUTABLE for binary integration tests')
    def test_supplied_binary_contains_mix_candidates_without_playback_claim(self):
        result = inspect(os.environ['OPENGENIUS_MUSIC_EXECUTABLE'])
        self.assertTrue(any('genius-mix-make-new-context' in value
                            for value in result['strings'].values()))
        self.assertTrue(result['references'])
        self.assertTrue(result['existing_core_calls'])
        self.assertFalse(result['native_mix_playback_verified'])
        self.assertFalse(result['mix_storage_format_verified'])


if __name__ == '__main__':
    unittest.main()
