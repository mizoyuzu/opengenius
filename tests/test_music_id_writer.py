import struct
import sys
from pathlib import Path
import unittest
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inspect_music_library import decode_musicdb, parse_tracks
from rewrite_music_ids import encode_library, patch_ids


def library_fixture():
    expanded = bytearray(b'ltma' + struct.pack('<II', 12, 2))
    for pid, gid in [(17, 0), (34, 99)]:
        track = bytearray(376)
        track[:4] = b'itma'
        struct.pack_into('<IIIQ', track, 4, 376, 376, 0, pid)
        struct.pack_into('<I', track, 0xCC, gid)
        expanded.extend(track)
    compressed = zlib.compress(expanded, 1)
    header = bytearray(160)
    header[:4] = b'hfma'
    struct.pack_into('<II', header, 4, 160, 160 + len(compressed))
    struct.pack_into('<I', header, 84, 102400)
    struct.pack_into('<I', header, 128, 160 + len(compressed))
    header[120:128] = b'opaque!!'
    encrypted_size = len(compressed) // 16 * 16
    cipher = Cipher(algorithms.AES(b'BHUILuilfghuila3'), modes.ECB()).encryptor()
    encoded = bytes(header) + cipher.update(compressed[:encrypted_size]) + cipher.finalize() + compressed[encrypted_size:]
    return bytes(expanded), encoded


class MusicIDWriterTests(unittest.TestCase):
    def assignment(self, pid=17, gid=51):
        return dict(persistent_id=f'{pid:016X}', genius_id=f'{gid:016X}')

    def test_round_trip_preserves_opaque_header_and_only_changes_id_slots(self):
        expanded, encoded = library_fixture()
        self.assertEqual(encode_library(expanded, encoded), encoded)
        changed, records = patch_ids(expanded, [self.assignment()])
        output = encode_library(changed, encoded)
        self.assertEqual(output[120:128], b'opaque!!')
        self.assertEqual(struct.unpack_from('<I', output, 8)[0], len(output))
        self.assertEqual(struct.unpack_from('<I', output, 128)[0], len(output))
        self.assertEqual(decode_musicdb(output), changed)
        self.assertEqual(parse_tracks(changed)[0][0]['genius_id'], '0000000000000033')
        self.assertEqual(changed[:12 + 0xCC], expanded[:12 + 0xCC])
        self.assertEqual(changed[12 + 0xD0:], expanded[12 + 0xD0:])
        self.assertEqual(len(records), 1)

    def test_rejects_existing_missing_duplicate_and_unrepresentable_ids(self):
        expanded, encoded = library_fixture()
        for assignments in [[self.assignment(34, 51)], [self.assignment(99, 51)],
                            [self.assignment(17, 99)], [self.assignment(17, 1 << 32)],
                            [self.assignment(17, 0)], [self.assignment(), self.assignment()],
                            [self.assignment(), self.assignment(99, 52)]]:
            with self.assertRaises(ValueError): patch_ids(expanded, assignments)
        unsupported = bytearray(encoded)
        struct.pack_into('<I', unsupported, 128, len(encoded) + 1)
        with self.assertRaises(ValueError): encode_library(expanded, unsupported)
        two_empty = bytearray(expanded)
        struct.pack_into('<I', two_empty, 12 + 376 + 0xCC, 0)
        with self.assertRaises(ValueError):
            patch_ids(bytes(two_empty), [self.assignment(17, 51), self.assignment(34, 51)])


if __name__ == '__main__':
    unittest.main()
