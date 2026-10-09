"""Generate the Music Library fields required for native Genius syncing.

The observed Music 1.5.6 / iPod classic 2.0.4 path uses the 32-bit value at
``itma+0x68`` as a per-track Genius update value. Native sync copied a track's
metadata and similarities only after that value changed, and stored it at
``mhit+0x1f0``. The original Apple checksum algorithm is not known, so this
module uses a stable content fingerprint for OpenGenius output instead of
claiming Apple's proprietary checksum.
"""

from __future__ import annotations

import struct
import zlib
import argparse
import hashlib
import json
from pathlib import Path


REVISION_DOMAIN = b"OpenGenius Genius sync revision v1\0"


def _blob(value, name):
    if value is None:
        return b"\xff"
    if not isinstance(value, bytes):
        raise ValueError(f"{name} must be bytes")
    if len(value) > 16 * 1024 * 1024:
        raise ValueError(f"{name} is too large")
    return struct.pack("<I", len(value)) + value


def sync_revision(genius_id, persistent_id, metadata=None, similarities=None):
    """Return a stable nonzero uint32 revision for one Genius track.

    Metadata and similarities should be the exact version-1 row BLOBs that are
    written to Genius.itdb. Omitting them is allowed for legacy callers, but
    the resulting revision then changes only when the IDs change.
    """
    if type(genius_id) is not int or not 0 < genius_id <= 0xFFFFFFFF:
        raise ValueError("genius_id must be a nonzero uint32")
    if type(persistent_id) is not int or not 0 < persistent_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("persistent_id must be a nonzero uint64")
    payload = (REVISION_DOMAIN + struct.pack("<IQ", genius_id, persistent_id)
               + _blob(metadata, "metadata") + _blob(similarities, "similarities"))
    value = zlib.crc32(payload) & 0xFFFFFFFF
    return value or 1


def revisions_for_assignments(assignments, metadata, similarities):
    """Build PID -> revision values from an assignment list and Genius rows."""
    if not isinstance(assignments, list):
        raise ValueError("assignments must be a list")
    if not isinstance(metadata, dict) or not isinstance(similarities, dict):
        raise ValueError("metadata and similarities must be mappings")
    result = {}
    for entry in assignments:
        if not isinstance(entry, dict):
            raise ValueError("invalid assignment")
        pid = entry.get("persistent_id")
        gid = entry.get("genius_id")
        if isinstance(pid, str):
            pid = int(pid, 16)
        if isinstance(gid, str):
            gid = int(gid, 16)
        if type(pid) is not int or type(gid) is not int or pid in result:
            raise ValueError("invalid or duplicate assignment")
        if gid not in metadata or gid not in similarities:
            raise ValueError("assignment has no matching Genius rows")
        result[pid] = sync_revision(gid, pid, metadata[gid], similarities[gid])
    return result


def assignments_with_revisions(assignments, metadata, similarities):
    """Return assignment records augmented with generated sync revisions."""
    revisions = revisions_for_assignments(assignments, metadata, similarities)
    result = []
    for entry in assignments:
        pid = entry["persistent_id"]
        if isinstance(pid, str):
            pid = int(pid, 16)
        result.append({**entry, "sync_revision": revisions[pid]})
    return result


def patch_expanded_library(expanded, assignments, revisions):
    """Patch Genius IDs and sync revisions in an expanded Music database.

    Only the observed 376-byte ``itma`` header fields at +0xcc (Genius ID) and
    +0x68 (sync revision) are modified. Every nonzero Genius ID must have a
    nonzero revision after the operation.
    """
    if not isinstance(expanded, bytes):
        raise ValueError("expanded Library must be bytes")
    start = expanded.find(b"ltma")
    if start < 0 or start + 12 > len(expanded):
        raise ValueError("missing ltma track list")
    list_header, count = struct.unpack_from("<II", expanded, start + 4)
    if list_header < 12 or count > 100000 or start + list_header > len(expanded):
        raise ValueError("invalid ltma bounds")
    requested = {}
    for entry in assignments:
        if not isinstance(entry, dict):
            raise ValueError("invalid assignment")
        pid, gid = entry.get("persistent_id"), entry.get("genius_id")
        if isinstance(pid, str):
            pid = int(pid, 16)
        if isinstance(gid, str):
            gid = int(gid, 16)
        if type(pid) is not int or type(gid) is not int or not 0 < gid <= 0xFFFFFFFF:
            raise ValueError("invalid assignment ID")
        if pid in requested:
            raise ValueError("duplicate assignment PID")
        requested[pid] = (gid, revisions.get(pid))

    result = bytearray(expanded)
    cursor = start + list_header
    seen = set()
    changed = []
    for _ in range(count):
        if cursor + 24 > len(expanded) or expanded[cursor:cursor + 4] != b"itma":
            raise ValueError("invalid itma record")
        header, total = struct.unpack_from("<II", expanded, cursor + 4)
        if header < 376 or total < header or cursor + total > len(expanded):
            raise ValueError("unsupported itma profile")
        pid = struct.unpack_from("<Q", expanded, cursor + 16)[0]
        if pid in seen:
            raise ValueError("duplicate Library PID")
        seen.add(pid)
        old_gid = struct.unpack_from("<I", expanded, cursor + 0xCC)[0]
        old_revision = struct.unpack_from("<I", expanded, cursor + 0x68)[0]
        if pid in requested:
            gid, revision = requested[pid]
            if revision is None:
                raise ValueError("missing sync revision")
            if type(revision) is not int or not 0 < revision <= 0xFFFFFFFF:
                raise ValueError("invalid sync revision")
            struct.pack_into("<I", result, cursor + 0xCC, gid)
            struct.pack_into("<I", result, cursor + 0x68, revision)
            if old_gid != gid or old_revision != revision:
                changed.append({"persistent_id": f"{pid:016X}",
                                "genius_id": f"{gid:08X}",
                                "old_revision": old_revision,
                                "new_revision": revision,
                                "expanded_revision_offset": cursor + 0x68})
        cursor += total
    if not set(requested).issubset(seen):
        raise ValueError("assignment PID is missing from Library")
    for pos in range(start + list_header, cursor):
        if result[pos] != expanded[pos] and pos not in {x["expanded_revision_offset"] + i for x in changed for i in range(4)}:
            # The only other permitted change is a Genius ID slot.
            if pos not in {x["expanded_revision_offset"] - 0x68 + 0xCC + i for x in changed for i in range(4)}:
                raise ValueError("unexpected Library change")
    return bytes(result), changed


def decode_library(data):
    """Decode an observed AES-ECB/zlib ``Library.musicdb`` file."""
    if (len(data) < 160 or data[:4] != b"hfma" or struct.unpack_from("<I", data, 4)[0] != 160
            or struct.unpack_from("<I", data, 8)[0] != len(data)):
        raise ValueError("unsupported Library.musicdb header")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    payload = data[160:]
    encrypted = min(struct.unpack_from("<I", data, 84)[0], len(payload)) // 16 * 16
    decryptor = Cipher(algorithms.AES(b"BHUILuilfghuila3"), modes.ECB()).decryptor()
    compressed = decryptor.update(payload[:encrypted]) + decryptor.finalize() + payload[encrypted:]
    inflater = zlib.decompressobj()
    expanded = inflater.decompress(compressed, 128 * 1024 * 1024 + 1)
    if len(expanded) > 128 * 1024 * 1024 or not inflater.eof:
        raise ValueError("invalid or oversized Library.musicdb zlib stream")
    return expanded


def encode_library(expanded, template):
    """Encode an expanded database while preserving the observed header profile."""
    if len(template) < 160 or template[:4] != b"hfma" or struct.unpack_from("<I", template, 4)[0] != 160:
        raise ValueError("unsupported Library.musicdb template")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    compressed = zlib.compress(expanded, 1)
    header = bytearray(template[:160])
    total = 160 + len(compressed)
    struct.pack_into("<I", header, 8, total)
    if struct.unpack_from("<I", template, 128)[0]:
        struct.pack_into("<I", header, 128, total)
    encrypted = min(struct.unpack_from("<I", header, 84)[0], len(compressed)) // 16 * 16
    encryptor = Cipher(algorithms.AES(b"BHUILuilfghuila3"), modes.ECB()).encryptor()
    result = bytes(header) + encryptor.update(compressed[:encrypted]) + encryptor.finalize() + compressed[encrypted:]
    if decode_library(result) != expanded:
        raise ValueError("Library.musicdb roundtrip failed")
    return result


def _load_assignments(path):
    document = json.loads(Path(path).read_text())
    if isinstance(document, dict):
        document = document.get("assignments")
    if not isinstance(document, list) or not document:
        raise ValueError("assignments JSON must contain a nonempty list")
    return document


def _assignment_values(assignments):
    revisions = {}
    for entry in assignments:
        if not isinstance(entry, dict):
            raise ValueError("invalid assignment")
        pid, gid = entry.get("persistent_id"), entry.get("genius_id")
        if isinstance(pid, str):
            pid = int(pid, 16)
        if isinstance(gid, str):
            gid = int(gid, 16)
        value = entry.get("sync_revision")
        if value is None:
            # Compatibility fallback for callers that do not have row BLOBs.
            value = sync_revision(gid, pid)
        if isinstance(value, str):
            value = int(value, 0)
        if type(value) is not int or not 0 < value <= 0xFFFFFFFF:
            raise ValueError("sync_revision must be a nonzero uint32")
        revisions[pid] = value
    return revisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="source Library.musicdb")
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    if source == output or output.exists():
        parser.error("output must be a new path")
    try:
        original = source.read_bytes()
        assignments = _load_assignments(args.assignments)
        revisions = _assignment_values(assignments)
        expanded = decode_library(original)
        patched, changes = patch_expanded_library(expanded, assignments, revisions)
        encoded = encode_library(patched, original)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(encoded)
        report = {
            "schema_version": 1,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "output_sha256": hashlib.sha256(encoded).hexdigest(),
            "assigned_track_count": len(assignments),
            "changed_track_count": len(changes),
            "nonzero_revision_count": sum(value != 0 for value in revisions.values()),
            "unique_revision_count": len(set(revisions.values())),
            "revision_collision_count": len(revisions) - len(set(revisions.values())),
            "sync_revision_field": "itma+0x68 -> mhit+0x1f0",
            "revision_algorithm": "OpenGenius stable CRC32 content token; Apple checksum algorithm unknown",
            "library_roundtrip_verified": True,
            "music_app_acceptance_verified": False,
            "ipod_acceptance_verified": False,
        }
        report_path = Path(str(output) + ".report.json")
        with report_path.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        print(json.dumps(report))
    except (OSError, ValueError, json.JSONDecodeError, struct.error):
        parser.exit(2, "Input rejected or Library.musicdb could not be rewritten.\n")


if __name__ == "__main__":
    main()
