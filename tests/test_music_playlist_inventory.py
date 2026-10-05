import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inspect_music_playlists import inventory_playlists, parse_lpma_candidate, parse_playlist_list_candidate


def boma(payload=b"payload", kind=201, header_bytes=20):
    total = header_bytes + len(payload)
    return (b"boma" + struct.pack("<III", header_bytes, total, kind)
            + bytes(header_bytes - 16) + payload)


def lpma(children=(), kind_byte=26, declared_item_count=0, header_bytes=368,
         declared_child_count=None, trailing=b""):
    children = list(children)
    count = len(children) if declared_child_count is None else declared_child_count
    total = header_bytes + sum(map(len, children)) + len(trailing)
    header = bytearray(header_bytes)
    header[:4] = b"lpma"
    struct.pack_into("<III", header, 4, header_bytes, total, count)
    struct.pack_into("<I", header, 16, declared_item_count)
    header[79] = kind_byte
    return bytes(header) + b"".join(children) + trailing


def playlist_list(records, count=None):
    return b'lPma' + struct.pack('<II', 92, len(records) if count is None else count) + bytes(80) + b''.join(records)


class MusicPlaylistInventoryTests(unittest.TestCase):
    def test_observed_list_count_walks_records_and_leaves_other_sections_unclaimed(self):
        records = [lpma([boma()]), lpma([], kind_byte=32)]
        data = b'prefix' + playlist_list(records) + b'unknown trailing section'
        report = inventory_playlists(data)
        parent = report['playlist_list_candidates'][0]
        self.assertTrue(parent['valid'])
        self.assertEqual(parent['record_offsets'], [6 + 92, 6 + 92 + len(records[0])])
        self.assertEqual(parent['end_offset'], len(data) - len(b'unknown trailing section'))
        self.assertFalse(parent['root_database_framing_verified'])
        self.assertEqual(report['unlisted_validated_lpma_candidates'], 0)
        self.assertEqual(report['parent_playlist_list_framing'], 'observed_lPma_92_byte_header')

    def test_list_mismatched_count_or_noncontiguous_record_is_rejected(self):
        for data in (playlist_list([lpma()], count=2),
                     playlist_list([lpma(), b'gap' + lpma()])):
            with self.subTest(length=len(data)):
                with self.assertRaisesRegex(ValueError, 'LPMA header'):
                    parse_playlist_list_candidate(data, 0)
                report = inventory_playlists(data)
                self.assertFalse(report['playlist_list_candidates'][0]['valid'])
                self.assertEqual(report['parent_playlist_list_framing'], 'unknown')

    def test_list_header_profile_and_declared_count_are_bounded(self):
        for header, count in ((93, 0), (92, 100001)):
            data = b'lPma' + struct.pack('<II', header, count) + bytes(80)
            with self.assertRaises(ValueError):
                parse_playlist_list_candidate(data, 0)
        with self.assertRaisesRegex(ValueError, 'header extends'):
            parse_playlist_list_candidate(b'lPma' + struct.pack('<II', 92, 0), 0)

    def test_both_kind_bytes_are_reported_without_classifying_a_mix(self):
        data = bytearray(lpma(kind_byte=26))
        data[80] = 32
        record = parse_lpma_candidate(data, 0)
        self.assertEqual(record['raw_kind_byte_79'], 26)
        self.assertEqual(record['raw_kind_byte_80'], 32)
        self.assertEqual(record['classification'], 'validated_lpma_candidate_unknown_semantics')
    def test_valid_record_reports_only_sanitized_counts_kinds_sizes_and_hashes(self):
        child_a = boma(b"hidden-name-and-id", kind=200)
        child_a_repeat = boma(b"masked-private-key", kind=200)
        child_b = boma(b"other-hidden-data", kind=202)
        record = lpma([child_a, child_a_repeat, child_b], kind_byte=26,
                      declared_item_count=17)

        parsed = parse_lpma_candidate(record, 0)

        self.assertEqual(parsed["header_bytes"], 368)
        self.assertEqual(parsed["total_bytes"], len(record))
        self.assertEqual(parsed["declared_child_boma_count"], 3)
        self.assertEqual(parsed["parsed_child_boma_count"], 3)
        self.assertEqual(parsed["declared_item_count_raw"], 17)
        self.assertEqual(parsed["raw_kind_byte_79"], 26)
        self.assertEqual([b["kind"] for b in parsed["child_boma_profiles"]], [200, 202])
        self.assertEqual([b["total_bytes"] for b in parsed["child_boma_profiles"]],
                         [len(child_a), len(child_b)])
        self.assertEqual(parsed["child_boma_profiles"][0]["count"], 2)
        self.assertEqual(parsed["sha256"], hashlib.sha256(record).hexdigest())
        self.assertEqual(parsed["child_boma_profiles"][0]["sha256"],
                         hashlib.sha256(child_a + child_a_repeat).hexdigest())
        encoded = repr(parsed)
        self.assertNotIn("hidden-name", encoded)
        self.assertNotIn("other-hidden", encoded)
        self.assertEqual(parsed["classification"],
                         "validated_lpma_candidate_unknown_semantics")

    def test_inventory_keeps_valid_and_malformed_hits_visible(self):
        good = lpma([boma(kind=201)])
        malformed = b"lpma" + struct.pack("<III", 368, 500, 1) + bytes(32)

        result = inventory_playlists(b"prefix" + good + b"gap" + malformed)

        self.assertEqual(result["parent_playlist_list_framing"], "unknown")
        self.assertEqual(result["candidate_hit_count"], 2)
        self.assertEqual(result["validated_lpma_candidate_count"], 1)
        self.assertEqual(result["rejected_magic_hit_count"], 1)
        self.assertTrue(result["candidates"][0]["valid"])
        self.assertFalse(result["candidates"][1]["valid"])
        self.assertIn("extends past", result["candidates"][1]["rejection"])

    def test_rejects_truncated_lpma_header_and_record(self):
        with self.assertRaisesRegex(ValueError, "truncated LPMA"):
            parse_lpma_candidate(b"lpma\0", 0)

        truncated = lpma([boma()], trailing=b"\x00")[:-1]
        with self.assertRaisesRegex(ValueError, "extends past"):
            parse_lpma_candidate(truncated, 0)

    def test_rejects_child_count_and_boma_boundary_mismatches(self):
        no_children_but_claims_one = lpma([], declared_child_count=1)
        with self.assertRaisesRegex(ValueError, "not a complete BOMA"):
            parse_lpma_candidate(no_children_but_claims_one, 0)

        wrong_count = lpma([boma()], declared_child_count=0)
        with self.assertRaisesRegex(ValueError, "do not cover"):
            parse_lpma_candidate(wrong_count, 0)

        malformed_child = bytearray(lpma([boma()]))
        struct.pack_into("<I", malformed_child, 368 + 8, len(malformed_child) + 1)
        with self.assertRaisesRegex(ValueError, "BOMA total length"):
            parse_lpma_candidate(malformed_child, 0)

    def test_rejects_out_of_bounds_counts_and_lengths(self):
        oversized_header = bytearray(lpma())
        struct.pack_into("<I", oversized_header, 4, 5000)
        with self.assertRaisesRegex(ValueError, "header length"):
            parse_lpma_candidate(oversized_header, 0)

        oversized_count = bytearray(lpma())
        struct.pack_into("<I", oversized_count, 12, 5000)
        with self.assertRaisesRegex(ValueError, "child count"):
            parse_lpma_candidate(oversized_count, 0)


if __name__ == "__main__":
    unittest.main()
