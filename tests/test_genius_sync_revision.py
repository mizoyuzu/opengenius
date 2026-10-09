import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location(
    "genius_sync_revision", Path(__file__).parents[1] / "scripts/genius_sync_revision.py"
)
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


def fixture_library():
    header = bytearray(12)
    header[:4] = b"ltma"
    struct.pack_into("<II", header, 4, 12, 2)
    rows = bytearray()
    for pid in (0x101, 0x202):
        row = bytearray(376)
        row[:4] = b"itma"
        struct.pack_into("<II", row, 4, 376, 376)
        struct.pack_into("<Q", row, 16, pid)
        rows.extend(row)
    return bytes(header + rows)


class GeniusSyncRevisionTests(unittest.TestCase):
    def test_revision_is_stable_and_content_sensitive(self):
        a = revision.sync_revision(7, 0x101, b"metadata", b"similarities")
        self.assertEqual(a, revision.sync_revision(7, 0x101, b"metadata", b"similarities"))
        self.assertNotEqual(a, revision.sync_revision(7, 0x101, b"metadata-2", b"similarities"))
        self.assertNotEqual(a, revision.sync_revision(8, 0x101, b"metadata", b"similarities"))
        self.assertNotEqual(a, 0)

    def test_all_assigned_tracks_receive_id_and_revision(self):
        assignments = [{"persistent_id": "0000000000000101", "genius_id": "00000007"},
                       {"persistent_id": "0000000000000202", "genius_id": "00000008"}]
        revisions = {0x101: 101, 0x202: 202}
        patched, changes = revision.patch_expanded_library(fixture_library(), assignments, revisions)
        self.assertEqual(len(changes), 2)
        for offset, gid, value in [(12, 7, 101), (388, 8, 202)]:
            self.assertEqual(struct.unpack_from("<I", patched, offset + 0xCC)[0], gid)
            self.assertEqual(struct.unpack_from("<I", patched, offset + 0x68)[0], value)

    def test_only_id_and_revision_header_slots_may_change(self):
        source = bytearray(fixture_library())
        source[12 + 0x120] = 0xA5
        assignments = [{"persistent_id": "0000000000000101", "genius_id": "00000007"}]
        patched, changes = revision.patch_expanded_library(bytes(source), assignments, {0x101: 101})
        self.assertEqual(patched[12 + 0x120], 0xA5)
        self.assertEqual(patched[388:], source[388:])
        self.assertEqual(len(changes), 1)
        changed_positions = {i for i, (before, after) in enumerate(zip(source, patched)) if before != after}
        self.assertLessEqual(changed_positions, {12 + 0x68 + i for i in range(4)} |
                             {12 + 0xCC + i for i in range(4)})

    def test_missing_assignment_and_zero_revision_are_rejected(self):
        with self.assertRaises(ValueError):
            revision.patch_expanded_library(fixture_library(),
                                             [{"persistent_id": "0000000000000101", "genius_id": "00000007"}],
                                             {0x101: 0})
        with self.assertRaises(ValueError):
            revision.patch_expanded_library(fixture_library(),
                                             [{"persistent_id": "0000000000000303", "genius_id": "00000007"}],
                                             {0x101: 1})

    def test_rows_generate_assignment_revisions(self):
        assignments = [{"persistent_id": "0000000000000101", "genius_id": "00000007"}]
        result = revision.assignments_with_revisions(assignments, {7: b"metadata"}, {7: b"similarities"})
        self.assertEqual(result[0]["persistent_id"], assignments[0]["persistent_id"])
        self.assertGreater(result[0]["sync_revision"], 0)


if __name__ == "__main__":
    unittest.main()
