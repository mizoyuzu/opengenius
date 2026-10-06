import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('sync_audit', Path(__file__).parents[1] / 'scripts/audit_ipod_genius_sync.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class SyncAuditTests(unittest.TestCase):
    def test_id_alignment_and_missing_update_candidate_are_separate(self):
        report = audit.compare({111: (71, 0), 222: (72, 1), 333: (0, 0)}, {111: 71, 222: 72, 333: 0})
        self.assertEqual(report['matched_nonzero_genius_ids'], 2)
        self.assertEqual(report['host_genius_tracks_with_zero_candidate_checksum'], 1)
        self.assertEqual(report['host_genius_tracks_with_nonzero_candidate_checksum'], 1)
        self.assertFalse(report['checksum_field_native_verified'])
        self.assertNotIn('111', str(report))

    def test_missing_and_mismatched_tracks(self):
        report = audit.compare({1: (7, 0), 2: (8, 0), 3: (0, 0)}, {1: 9, 3: 10})
        self.assertEqual(report['host_genius_tracks_missing_on_device'], 1)
        self.assertEqual(report['mismatched_genius_ids'], 2)

    def test_observed_host_header_and_duplicate_pid(self):
        data = bytearray(12 + 376 * 2)
        data[:4] = b'ltma'
        struct.pack_into('<II', data, 4, 12, 2)
        for pos, pid, gid, checksum in [(12, 1, 7, 0), (388, 2, 8, 1)]:
            data[pos:pos + 4] = b'itma'
            struct.pack_into('<II', data, pos + 4, 376, 376)
            struct.pack_into('<Q', data, pos + 16, pid)
            struct.pack_into('<I', data, pos + 0xcc, gid)
            struct.pack_into('<I', data, pos + 0x68, checksum)
        self.assertEqual(audit.host_tracks(data), {1: (7, 0), 2: (8, 1)})
        struct.pack_into('<Q', data, 388 + 16, 1)
        with self.assertRaises(ValueError):
            audit.host_tracks(data)

    def test_observed_device_profile_and_truncation(self):
        data = bytearray(244 + 96 + 12 + 624)
        for pos, magic, header, total in [(0, b'mhbd', 244, len(data)), (244, b'mhsd', 96, len(data) - 244), (352, b'mhit', 624, 624)]:
            data[pos:pos + 4] = magic
            struct.pack_into('<II', data, pos + 4, header, total)
        struct.pack_into('<I', data, 244 + 12, 1)
        data[340:344] = b'mhlt'
        struct.pack_into('<II', data, 344, 12, 1)
        struct.pack_into('<Q', data, 352 + 0x70, 123)
        struct.pack_into('<Q', data, 352 + 0x1e4, 456)
        self.assertEqual(audit.device_tracks(data), {123: 456})
        with self.assertRaises(ValueError):
            audit.device_tracks(data[:-1])
        struct.pack_into('<I', data, 352 + 4, 620)
        with self.assertRaises(ValueError):
            audit.device_tracks(data)


if __name__ == '__main__':
    unittest.main()
