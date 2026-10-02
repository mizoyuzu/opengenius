import sqlite3
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from genius_format import parse_similarities, pack_similarities, parse_metadata, parse_id, sql_id
from rewrite_genius import apply_relations, table_snapshot, encrypt_pages
from decrypt_genius import decrypt_pages, MAGIC


class GeniusFormatTests(unittest.TestCase):
    def test_known_envelope_preserves_self_and_order(self):
        # A captured structure, with IDs anonymized. The source may contain self.
        blob = bytes.fromhex("000000001c00000003000000110000000000000022000000000000003300000000000000")
        word, ids = parse_similarities(blob)
        self.assertEqual((word, ids), (0, [17, 34, 51]))
        self.assertEqual(pack_similarities(word, ids), blob)

    def test_malformed_sizes_and_identifiers_rejected(self):
        valid = pack_similarities(0, [17, 34])
        for blob in [valid[:-1], valid + b"\0", valid[:8] + struct.pack("<I", 0xFFFFFFFF) + valid[12:]]:
            with self.assertRaises(ValueError): parse_similarities(blob)
        for value in ["1", "00000000000000ZZ", 17]:
            with self.assertRaises(ValueError): parse_id(value)
        with self.assertRaises(ValueError): parse_metadata(bytes(31))
        with self.assertRaises(ValueError): pack_similarities(0, [1 << 64])
        self.assertEqual(sql_id(0xFFFFFFFFFFFFFFFF), -1)

    def database(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE genius_metadata(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)")
        c.execute("CREATE TABLE genius_similarities(genius_id INTEGER PRIMARY KEY,version INTEGER,data BLOB)")
        c.execute("CREATE TABLE genius_config(id INTEGER PRIMARY KEY,version INTEGER,data BLOB)")
        for gid in [17, 34]:
            c.execute("INSERT INTO genius_metadata VALUES(?,1,?)", (gid, bytes(32)))
            c.execute("INSERT INTO genius_similarities VALUES(?,1,?)", (gid, pack_similarities(7, [17, 34])))
        c.execute("INSERT INTO genius_config VALUES(1,2,?)", (b"unknown-config",))
        c.commit()
        self.addCleanup(c.close)
        return c

    def manifest(self, ids=None):
        return dict(schema_version=1, source_genius_sha256="hash", relations=[dict(
            seed_genius_id="0000000000000011", ordered_genius_ids=ids or ["0000000000000022", "0000000000000011"])])

    def test_rewrite_preserves_other_tables_and_header_word(self):
        c = self.database(); before = table_snapshot(c)
        changes = apply_relations(c, self.manifest(), "hash")
        after = table_snapshot(c)
        self.assertEqual(len(changes), 1)
        self.assertEqual(before["genius_metadata"], after["genius_metadata"])
        self.assertEqual(before["genius_config"], after["genius_config"])
        self.assertEqual(before["genius_similarities"][1], after["genius_similarities"][1])
        self.assertEqual(parse_similarities(after["genius_similarities"][0][2]), (7, [34, 17]))

    def test_unknown_target_duplicate_and_wrong_source_rejected(self):
        mixed = self.manifest()
        mixed["relations"].append(dict(seed_genius_id="0000000000000022", ordered_genius_ids=["0000000000000099"]))
        for manifest, source in [(self.manifest(["0000000000000099"]), "hash"),
                                 (self.manifest(["0000000000000011"] * 2), "hash"),
                                 (self.manifest(), "other-source"), (mixed, "hash")]:
            c = self.database(); before = table_snapshot(c)
            with self.assertRaises(ValueError): apply_relations(c, manifest, source)
            self.assertEqual(before, table_snapshot(c))

    def test_encryption_preserves_page_stream_position_and_randomizes_nonces(self):
        # A two-page format fixture, independent of SQLite logical validation.
        clear = bytearray((bytes(range(256)) * 32))
        clear[:16] = MAGIC; clear[16:24] = bytes.fromhex("100001010c402020")
        key = bytes(range(16))
        a, b = encrypt_pages(bytes(clear), key), encrypt_pages(bytes(clear), key)
        self.assertNotEqual(a, b)
        self.assertEqual(a[16:24], clear[16:24])
        decoded = decrypt_pages(a, key)
        for off in [0, 4096]: self.assertEqual(decoded[off:off + 4084], clear[off:off + 4084])
        with self.assertRaises(ValueError): decrypt_pages(a, bytes(16))


if __name__ == "__main__":
    unittest.main()
