"""Read-only probe for the supplied Music.app library format.

Requires cryptography. Prints JSON; personal track metadata is opt-in.
This tool neither decrypts Genius.itdb nor writes Apple-compatible databases.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import zlib


MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"Truncated integer at offset {offset}")
    return struct.unpack_from("<I", data, offset)[0]


def decode_musicdb(data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(data) < 88 or data[:4] != b"hfma":
        raise ValueError("Expected an hfma Music database")
    header_size = u32(data, 4)
    if not 88 <= header_size <= len(data):
        raise ValueError("Invalid hfma header length")
    payload = data[header_size:]
    encrypted_size = min(u32(data, 84), len(payload)) // 16 * 16
    decryptor = Cipher(algorithms.AES(b"BHUILuilfghuila3"), modes.ECB()).decryptor()
    compressed = (
        decryptor.update(payload[:encrypted_size])
        + decryptor.finalize()
        + payload[encrypted_size:]
    )
    inflater = zlib.decompressobj()
    expanded = inflater.decompress(compressed, MAX_EXPANDED_BYTES + 1)
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded database exceeds probe limit")
    if not inflater.eof:
        raise ValueError("Incomplete zlib stream")
    return expanded


def parse_tracks(data):
    # Locate the candidate list, then validate every declared record boundary.
    start = data.find(b"ltma")
    if start < 0:
        raise ValueError("No ltma track list found")
    header_size = u32(data, start + 4)
    count = u32(data, start + 8)
    if header_size < 12 or start + header_size > len(data):
        raise ValueError("Invalid ltma header length")
    offset = start + header_size
    tracks = []
    metadata_types = Counter()
    for _ in range(count):
        if data[offset:offset + 4] != b"itma":
            raise ValueError(f"Expected itma at offset {offset}")
        header_size, size, child_count = (u32(data, offset + n) for n in (4, 8, 12))
        end = offset + size
        if header_size < 24 or size < header_size or end > len(data):
            raise ValueError(f"Invalid itma lengths at offset {offset}")
        pid = struct.unpack_from("<Q", data, offset + 16)[0]
        track = {"persistent_id": f"{pid:016X}"}
        child = offset + header_size
        for _ in range(child_count):
            if child + 20 > end or data[child:child + 4] != b"boma":
                raise ValueError(f"Expected boma at offset {child}")
            child_header, child_size, kind = (u32(data, child + n) for n in (4, 8, 12))
            child_end = child + child_size
            if child_header < 20 or child_size < child_header or child_end > end:
                raise ValueError(f"Invalid boma lengths at offset {child}")
            metadata_types[kind] += 1
            # Observed numeric metadata layout in the supplied 1.7.0.146 sample.
            if kind == 1 and child_header == 20 and child_size == 384:
                track["duration_ms"] = u32(data, child + 176)
            if kind in (2, 3, 4):
                body = child + child_header
                if body + 16 > child_end or u32(data, body) != 1:
                    raise ValueError(f"Unsupported string format at offset {body}")
                length = u32(data, body + 4)
                if length % 2 or body + 16 + length > child_end:
                    raise ValueError(f"Invalid string length at offset {body}")
                key = {2: "title", 3: "album", 4: "artist"}[kind]
                track[key] = data[body + 16:body + 16 + length].decode("utf-16-le")
            child = child_end
        if child != end:
            raise ValueError(f"Unconsumed itma bytes at offset {offset}")
        tracks.append(track)
        offset = end
    return tracks, metadata_types


def inspect_bundle(bundle, include_tracks=False):
    if not (bundle / "Library.musicdb").is_file():
        raise ValueError("Bundle does not contain Library.musicdb")
    summary = {"files": {}}
    for name in ("Library.musicdb", "Genius.itdb", "Extras.itdb",
                 "Application.musicdb", "Library Preferences.musicdb"):
        path = bundle / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        entry = {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if data[:4] == b"hfma":
            expanded = decode_musicdb(data)
            entry["expanded_bytes"] = len(expanded)
            if name == "Library.musicdb":
                tracks, kinds = parse_tracks(expanded)
                entry["track_count"] = len(tracks)
                entry["unique_persistent_ids"] = len({t["persistent_id"] for t in tracks})
                entry["missing_titles"] = sum(not t.get("title") for t in tracks)
                entry["metadata_types"] = dict(sorted(kinds.items()))
                if include_tracks:
                    entry["tracks"] = tracks
        elif name == "Genius.itdb" and len(data) >= 24:
            page_size = int.from_bytes(data[16:18], "big")
            if page_size == 1:
                page_size = 65536
            plausible = (
                page_size in {512, 1024, 2048, 4096, 8192, 16384, 32768, 65536}
                and data[18] in (1, 2) and data[19] in (1, 2)
                and data[21:24] == bytes((64, 32, 32))
                and len(data) % page_size == 0
            )
            entry["sqlite_header_window_hex"] = data[16:24].hex()
            entry["see_like_header_candidate"] = (
                plausible and not data.startswith(b"SQLite format 3\0")
            )
            if plausible:
                entry["candidate_page_size"] = page_size
                entry["candidate_page_count"] = len(data) // page_size
                entry["candidate_reserved_bytes"] = data[20]
        summary["files"][name] = entry
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--include-tracks", action="store_true",
                        help="Include personal track IDs and metadata in JSON output")
    args = parser.parse_args()
    if not args.bundle.is_dir():
        parser.error("bundle must be a Music library directory")
    try:
        result = inspect_bundle(args.bundle, args.include_tracks)
    except (ValueError, OSError, zlib.error, ImportError) as error:
        parser.exit(1, f"Probe failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
