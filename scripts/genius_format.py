"""Lossless codecs for the observed Genius version-1 BLOB envelopes.

The meanings of metadata words and the similarity header's first word remain
unknown. Order is preserved; an explicit per-edge rank has not been observed.
"""
import struct


def parse_metadata(blob):
    if len(blob) != 32:
        raise ValueError("Expected 32-byte version-1 metadata")
    return struct.unpack("<4Q", blob)


def parse_similarities(blob):
    if len(blob) < 12:
        raise ValueError("Truncated similarity header")
    unknown, length, count = struct.unpack_from("<3I", blob)
    if length != len(blob) - 8 or len(blob) != 12 + count * 8:
        raise ValueError("Inconsistent similarity size/count")
    return unknown, list(struct.unpack_from(f"<{count}Q", blob, 12))


def pack_similarities(unknown, ids):
    if not isinstance(unknown, int) or isinstance(unknown, bool) or not 0 <= unknown <= 0xFFFFFFFF:
        raise ValueError("Header word outside uint32")
    ids = list(ids)
    if len(ids) > (0xFFFFFFFF - 4) // 8:
        raise ValueError("Too many similarity IDs")
    for value in ids:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Similarity ID outside uint64")
    return struct.pack("<3I", unknown, 4 + len(ids) * 8, len(ids)) + struct.pack(f"<{len(ids)}Q", *ids)


def sql_id(value):
    """SQLite signed integer representation of an unsigned 64-bit identifier."""
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("Genius ID outside uint64")
    return value if value < (1 << 63) else value - (1 << 64)


def unsigned_id(value):
    return value & 0xFFFFFFFFFFFFFFFF


def parse_id(value):
    if not isinstance(value, str) or len(value) != 16 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Expected a 16-character hexadecimal Genius ID")
    return int(value, 16)
