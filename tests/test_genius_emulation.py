"""Integration tests requiring the user-supplied, hash-pinned Music binary.

Set OPENGENIUS_MUSIC_EXECUTABLE to opt in; no binary is stored in the repository.
"""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from emulate_genius import MusicCore
from genius_format import pack_config, pack_similarities


EXECUTABLE = os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE')


@unittest.skipUnless(EXECUTABLE, 'Set OPENGENIUS_MUSIC_EXECUTABLE for binary integration tests')
class MusicCoreTests(unittest.TestCase):
    def core(self, order):
        config = pack_config(dict(version=2, filters=[dict(type=1, parameters=[20, 50, 10, 10])],
                                  flags=0, result_words=[10, 20]))
        return MusicCore(EXECUTABLE, config, {i: bytes(32) for i in [17, 34, 51]},
                         {17: pack_similarities(0, order)})

    def test_real_core_consumes_reordered_relations_and_deduplicates_seed(self):
        a = self.core([17, 34, 51]).generate(17, 25)['result_genius_ids']
        b = self.core([51, 34, 17]).generate(17, 25)['result_genius_ids']
        self.assertEqual(a, ['0000000000000011', '0000000000000022', '0000000000000033'])
        self.assertEqual(b, ['0000000000000011', '0000000000000033', '0000000000000022'])

    def test_real_core_excludes_candidate_without_metadata(self):
        result = self.core([17, 99, 34]).generate(17, 25)
        self.assertEqual(result['result_genius_ids'], ['0000000000000011', '0000000000000022'])
        self.assertFalse(result['stopped_at_limit'])

    def test_unimplemented_import_and_syscall_fail_closed(self):
        core = self.core([17, 34])
        address = next(a for a, name in core.image.imports.items() if name == '_CFAbsoluteTimeGetCurrent')
        with self.assertRaisesRegex(ValueError, 'Unimplemented import'):
            core.run(address)
        # Change emulated memory only; the supplied executable remains read-only.
        core.cpu.mem_write(0x100364D04, struct.pack('<I', 0xD4000001))  # svc #0
        with self.assertRaisesRegex(ValueError, 'Unsupported trap'):
            core.run(0x100364D04)

    def test_distance_tracks_selection_age_and_relaxes_to_floor(self):
        core = self.core([17, 34])
        library = core.run(0x100364D04, core.callbacks, 0, 0)
        criterion = core.alloc(0x38)
        core.write(criterion, 'QQ', 0x101F21E18, library)
        core.write(criterion + 0x10, 'I', 1)  # Select metadata index 1.
        core.write(criterion + 0x18, '4Q', 2, 6, 0, 1)
        state = core.run(0x100366F40, criterion, 0)

        def track(group):
            metadata = core.alloc(32, struct.pack('<4Q', 29, group, 11, 12))
            result = core.alloc(0x40)
            core.write(result + 0x10, 'QQ', metadata, 4)
            return result

        a, b, unseen = track(100), track(200), track(300)
        core.run(0x10036715C, criterion, state, a)
        core.run(0x10036715C, criterion, state, b)
        table, = core.read(state + 0x30, 'Q')
        node_a = core.run(0x100368C04, table, 100)
        node_b = core.run(0x100368C04, table, 200)
        self.assertEqual(core.read(node_a + 0x18, 'I'), (2,))
        self.assertEqual(core.read(node_b + 0x18, 'I'), (1,))
        self.assertEqual(core.run(0x1003670BC, criterion, library, state, a), 1)
        self.assertEqual(core.run(0x1003670BC, criterion, library, state, unseen), 0)
        thresholds = [core.read(state + 0x28, 'Q')[0]]
        for _ in range(5):
            core.run(0x100367128, criterion, state)
            thresholds.append(core.read(state + 0x28, 'Q')[0])
        self.assertEqual(thresholds, [6, 5, 4, 3, 2, 2])
        self.assertEqual(core.run(0x1003670BC, criterion, library, state, a), 0)
        core.run(0x10036715C, criterion, state, a)
        self.assertEqual(core.read(node_a + 0x18, 'I'), (1,))
        self.assertEqual(core.read(node_b + 0x18, 'I'), (2,))
        core.write(criterion + 0x28, 'Q', 6)
        core.run(0x100367580, criterion, state)
        self.assertEqual(core.read(state + 0x28, 'Q'), (9,))  # base 6 + fixed random 3
        self.assertEqual(core.calls['_random'], 1)


if __name__ == '__main__':
    unittest.main()
