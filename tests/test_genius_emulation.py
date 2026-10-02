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


if __name__ == '__main__':
    unittest.main()
