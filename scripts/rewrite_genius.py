"""Build an experimental encrypted Genius copy by editing existing relations.

This tests serialization/encryption compatibility, not Music/iPod acceptance.
Only IDs already present in the source metadata may be used; all other tables
are preserved. Source library files are never changed.
"""
import argparse
import hashlib
import json
import secrets
import sqlite3
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database, MAGIC
from genius_format import parse_id, parse_similarities, pack_similarities, sql_id, unsigned_id


def encrypt_pages(clear, key):
    if len(key) != 16 or len(clear) < 4096 or len(clear) % 4096 or clear[:16] != MAGIC:
        raise ValueError("Expected complete SQLite pages and an AES-128 key")
    if clear[16:24] != bytes.fromhex("100001010c402020"):
        raise ValueError("Unsupported SQLite page header")
    result = bytearray()
    for number in range(1, len(clear) // 4096 + 1):
        page = clear[(number - 1) * 4096:number * 4096]
        nonce = secrets.token_bytes(12)
        cipher = Cipher(algorithms.AES(key), modes.OFB(struct.pack("<I", number) + nonce)).encryptor()
        encrypted = bytearray(cipher.update(page[:-12]) + cipher.finalize())
        encrypted.extend(nonce)
        if number == 1:
            encrypted[16:24] = page[16:24]
        result.extend(encrypted)
    return bytes(result)


def table_snapshot(connection):
    names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    result = {}
    for name in names:
        if not name.startswith("genius_") or not name.replace("_", "").isalnum():
            raise ValueError("Unexpected table in source DB")
        result[name] = connection.execute('SELECT * FROM "' + name + '" ORDER BY 1').fetchall()
    return result


def relation_manifest(connection, encrypted):
    relations = []
    for seed, version, blob in connection.execute("SELECT genius_id,version,data FROM genius_similarities ORDER BY genius_id"):
        if version != 1:
            raise ValueError("Unsupported similarity version")
        _, ids = parse_similarities(blob)
        relations.append(dict(seed_genius_id=f"{unsigned_id(seed):016X}",
                              ordered_genius_ids=[f"{i:016X}" for i in ids]))
    return dict(schema_version=1, source_genius_sha256=hashlib.sha256(encrypted).hexdigest(), relations=relations)


def apply_relations(connection, manifest, source_hash):
    if manifest.get("schema_version") != 1 or manifest.get("source_genius_sha256") != source_hash:
        raise ValueError("Manifest version/source hash mismatch")
    relations = manifest.get("relations")
    if not isinstance(relations, list) or not relations:
        raise ValueError("Expected at least one relation row")
    before = table_snapshot(connection)
    known = {unsigned_id(row[0]) for row in before["genius_metadata"]}
    previous = {unsigned_id(row[0]): row for row in before["genius_similarities"]}
    expected = {k: list(v) for k, v in before.items()}
    updated = dict(previous)
    seeds = set()
    changes = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise ValueError("Invalid relation record")
        seed = parse_id(relation.get("seed_genius_id"))
        if seed in seeds or seed not in known or seed not in previous:
            raise ValueError("Duplicate or unknown seed ID")
        seeds.add(seed)
        values = relation.get("ordered_genius_ids")
        if not isinstance(values, list) or len(values) > 10000:
            raise ValueError("Invalid ordered ID list")
        ids = [parse_id(value) for value in values]
        if len(ids) != len(set(ids)) or any(value not in known for value in ids):
            raise ValueError("Duplicate or unknown target ID")
        old = previous[seed]
        if old[1] != 1:
            raise ValueError("Unsupported similarity version")
        word, original = parse_similarities(old[2])
        blob = pack_similarities(word, ids)
        updated[seed] = (old[0], old[1], blob)
        if original != ids:
            changes.append(dict(seed_genius_id=f"{seed:016X}", before_count=len(original), after_count=len(ids)))
    expected["genius_similarities"] = sorted(updated.values(), key=lambda row: row[0])
    # Validate every record before changing anything; roll back on SQL errors
    # or any unexpected table delta.
    with connection:
        connection.executemany("UPDATE genius_similarities SET data=? WHERE genius_id=?",
                               [(updated[seed][2], sql_id(seed)) for seed in seeds])
        if table_snapshot(connection) != expected:
            raise ValueError("Unexpected database changes")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--export-relations", type=Path)
    parser.add_argument("--relations", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.export_relations) == bool(args.relations) or bool(args.relations) != bool(args.output):
        parser.error("Choose --export-relations, or --relations with --output")
    bundle = args.bundle.resolve()
    destinations = [p.resolve() for p in (args.export_relations, args.output) if p]
    if args.output:
        destinations.append(Path(str(args.output.resolve()) + ".report.json"))
    for path in destinations:
        if path.exists() or path.is_relative_to(bundle) or path.is_relative_to(args.executable.resolve().parent):
            parser.error("Output exists or is inside a source directory")
    encrypted = (bundle / "Genius.itdb").read_bytes()
    source_hash = hashlib.sha256(encrypted).hexdigest()
    header = extract_key_header((bundle / "Library Preferences.musicdb").read_bytes())
    key, _ = derive_key(args.executable, header)
    clear = decrypt_pages(encrypted, key)
    validate_database(clear)
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(clear)
        if args.export_relations:
            manifest = relation_manifest(connection, encrypted)
            with destinations[0].open("x") as file:
                json.dump(manifest, file, indent=2)
                file.write("\n")
            print(f"Exported {len(manifest['relations'])} existing relation rows")
            return
        manifest = json.loads(args.relations.read_text())
        changes = apply_relations(connection, manifest, source_hash)
        rewritten = connection.serialize()
        counts = validate_database(rewritten)
        output = encrypt_pages(rewritten, key)
        # Decrypt the written format again and compare every logical row.
        check = sqlite3.connect(":memory:")
        try:
            decoded = decrypt_pages(output, key)
            validate_database(decoded)
            check.deserialize(decoded)
            if table_snapshot(check) != table_snapshot(connection):
                raise ValueError("Encrypted output did not round-trip")
        finally:
            check.close()
        report = dict(schema_version=1, source_genius_sha256=source_hash,
                      output_sha256=hashlib.sha256(output).hexdigest(), changed_relations=changes,
                      table_counts=counts, sqlite_integrity="ok", encrypted_round_trip_verified=True,
                      music_app_acceptance_verified=False, ipod_acceptance_verified=False,
                      preserved_tables=[name for name in counts if name != "genius_similarities"])
        with destinations[0].open("xb") as file:
            file.write(output)
        with destinations[1].open("x") as file:
            json.dump(report, file, indent=2)
            file.write("\n")
        print(f"Wrote experimental encrypted copy; {len(changes)} relation rows changed")
        print("SQLite integrity and logical row round-trip verified; Music/iPod acceptance untested")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
