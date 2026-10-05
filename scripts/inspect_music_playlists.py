"""Inventory structurally valid LPMA playlist-record candidates.

The observed lPma list header and its LPMA records are validated as candidates.
Every LPMA magic hit is also inventoried independently. Neither path establishes
the root database framing or native Mix acceptance. Output omits names and IDs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from inspect_music_library import decode_musicdb


MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_CANDIDATE_HITS = 100_000
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 4096
MAX_CHILD_BOMAS = 4096
PLAYLIST_LIST_HEADER_BYTES = 92
MAX_LIST_CANDIDATES = 128
LPMA_KIND_BYTE_OFFSET = 79


def u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("truncated integer")
    return struct.unpack_from("<I", data, offset)[0]


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse_lpma_candidate(data, offset):
    """Validate one observed LPMA record's declared bounds and BOMA children."""
    if offset < 0 or offset + 20 > len(data) or data[offset:offset + 4] != b"lpma":
        raise ValueError("missing or truncated LPMA header")

    header_bytes = u32(data, offset + 4)
    total_bytes = u32(data, offset + 8)
    child_count = u32(data, offset + 12)
    if header_bytes < LPMA_KIND_BYTE_OFFSET + 1 or header_bytes > MAX_HEADER_BYTES:
        raise ValueError("LPMA header length outside supported bounds")
    if total_bytes < header_bytes or total_bytes > MAX_RECORD_BYTES:
        raise ValueError("LPMA total length outside supported bounds")
    end = offset + total_bytes
    if end > len(data):
        raise ValueError("LPMA record extends past expanded database")
    if child_count > MAX_CHILD_BOMAS:
        raise ValueError("LPMA child count outside supported bounds")

    child_offset = offset + header_bytes
    boma_profiles = {}
    for _ in range(child_count):
        if child_offset + 20 > end or data[child_offset:child_offset + 4] != b"boma":
            raise ValueError("declared child is not a complete BOMA header")
        child_header_bytes = u32(data, child_offset + 4)
        child_total_bytes = u32(data, child_offset + 8)
        child_kind = u32(data, child_offset + 12)
        if child_header_bytes < 20 or child_header_bytes > MAX_HEADER_BYTES:
            raise ValueError("BOMA header length outside supported bounds")
        if child_total_bytes < child_header_bytes or child_total_bytes > end - child_offset:
            raise ValueError("BOMA total length outside LPMA bounds")
        child_end = child_offset + child_total_bytes
        profile_key = (child_kind, child_header_bytes, child_total_bytes)
        profile = boma_profiles.setdefault(profile_key, {
            "kind": child_kind,
            "header_bytes": child_header_bytes,
            "total_bytes": child_total_bytes,
            "count": 0,
            "_hash": hashlib.sha256(),
        })
        profile["count"] += 1
        profile["_hash"].update(data[child_offset:child_end])
        child_offset = child_end

    if child_offset != end:
        raise ValueError("declared BOMA children do not cover LPMA record")

    bomas = []
    for profile in sorted(boma_profiles.values(),
                          key=lambda item: (item["kind"], item["header_bytes"],
                                            item["total_bytes"])):
        bomas.append({key: value for key, value in profile.items() if key != "_hash"}
                     | {"sha256": profile["_hash"].hexdigest()})

    return {
        "offset": offset,
        "header_bytes": header_bytes,
        "total_bytes": total_bytes,
        "declared_child_boma_count": child_count,
        "parsed_child_boma_count": sum(item["count"] for item in bomas),
        "declared_item_count_raw": u32(data, offset + 16),
        "raw_kind_byte_79": data[offset + LPMA_KIND_BYTE_OFFSET],
        "raw_kind_byte_80": data[offset + 80] if header_bytes > 80 else None,
        "child_boma_profiles": bomas,
        "sha256": _sha256(data[offset:end]),
        "classification": "validated_lpma_candidate_unknown_semantics",
    }


def parse_playlist_list_candidate(data, offset):
    """Walk the observed lPma header/count and contiguous declared LPMA records."""
    if offset < 0 or offset + 12 > len(data) or data[offset:offset + 4] != b'lPma':
        raise ValueError('missing or truncated lPma header')
    header = u32(data, offset + 4)
    count = u32(data, offset + 8)
    if header != PLAYLIST_LIST_HEADER_BYTES:
        raise ValueError('unsupported lPma header profile')
    if offset + header > len(data):
        raise ValueError('lPma header extends past expanded database')
    if count > MAX_CANDIDATE_HITS:
        raise ValueError('lPma record count exceeds supported bound')
    cursor = offset + header
    records = []
    for _ in range(count):
        record = parse_lpma_candidate(data, cursor)
        records.append(record)
        cursor += record['total_bytes']
    return dict(offset=offset, header_bytes=header, declared_record_count=count,
                parsed_record_count=len(records), end_offset=cursor,
                record_offsets=[record['offset'] for record in records],
                sha256=_sha256(data[offset:cursor]),
                root_database_framing_verified=False)


def inventory_playlists(expanded):
    """Report LPMA magic hits, keeping malformed hits visible as rejections."""
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded database exceeds supported bound")
    hits = []
    offset = 0
    while True:
        offset = expanded.find(b"lpma", offset)
        if offset < 0:
            break
        if len(hits) >= MAX_CANDIDATE_HITS:
            raise ValueError("LPMA candidate hit count exceeds supported bound")
        try:
            candidate = parse_lpma_candidate(expanded, offset)
        except ValueError as error:
            hits.append({"offset": offset, "valid": False, "rejection": str(error)})
        else:
            candidate["valid"] = True
            hits.append(candidate)
        offset += 4

    valid_count = sum(hit["valid"] for hit in hits)
    lists = []
    offset = 0
    total_declared = 0
    while (offset := expanded.find(b'lPma', offset)) >= 0:
        if len(lists) >= MAX_LIST_CANDIDATES:
            raise ValueError('lPma list candidate count exceeds supported bound')
        if offset + 12 <= len(expanded):
            total_declared += u32(expanded, offset + 8)
            if total_declared > MAX_CANDIDATE_HITS:
                raise ValueError('total lPma declared records exceed supported bound')
        try:
            item = parse_playlist_list_candidate(expanded, offset)
        except ValueError as error:
            lists.append(dict(offset=offset, valid=False, rejection=str(error)))
        else:
            lists.append(dict(item, valid=True))
        offset += 4
    valid_lists = [item for item in lists if item['valid']]
    listed_offsets = {offset for item in valid_lists for offset in item['record_offsets']}
    return {
        "parent_playlist_list_framing": ('observed_lPma_92_byte_header'
                                         if valid_lists else 'unknown'),
        "playlist_list_candidates": lists,
        "validated_playlist_list_candidate_count": len(valid_lists),
        "unlisted_validated_lpma_candidates": sum(hit['valid'] and hit['offset'] not in listed_offsets
                                                  for hit in hits),
        "candidate_hit_count": len(hits),
        "validated_lpma_candidate_count": valid_count,
        "rejected_magic_hit_count": len(hits) - valid_count,
        "candidates": hits,
    }


def inspect_library_musicdb(path):
    raw = Path(path).read_bytes()
    expanded = decode_musicdb(raw)
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded database exceeds supported bound")
    return {
        "schema_version": 1,
        "input_file": "Library.musicdb",
        "source_size_bytes": len(raw),
        "source_sha256": _sha256(raw),
        "expanded_size_bytes": len(expanded),
        "playlist_inventory": inventory_playlists(expanded),
        "genius_mix_detected": None,
        "genius_mix_detection_status": "not_implemented_no_mix_storage_profile",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library_musicdb", type=Path,
                        help="Path to Library.musicdb (read-only)")
    parser.add_argument("--output", type=Path,
                        help="Save inventory to a new JSON file")
    args = parser.parse_args()
    try:
        result = inspect_library_musicdb(args.library_musicdb)
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(encoded)
    except (ValueError, OSError, zlib.error, ImportError) as error:
        parser.exit(1, f"Playlist inventory failed: {error}\n")
    if args.output:
        inventory = result['playlist_inventory']
        print(json.dumps({key: inventory[key] for key in (
            'candidate_hit_count', 'validated_lpma_candidate_count', 'rejected_magic_hit_count')}))
    else:
        print(encoded, end='')


if __name__ == "__main__":
    main()
