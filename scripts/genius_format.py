"""Lossless codecs for the observed Genius version-1 BLOB envelopes.

Metadata identifiers and config parameters are only partly understood.
Order is preserved; an explicit per-edge rank has not been observed.
"""
import struct

CONFIG_FILTER_NAMES = {1: "already_added", 2: "compatible_genre", 3: "distance",
                       4: "skip_count", 5: "random_jitter"}


def parse_metadata(blob):
    if len(blob) != 32:
        raise ValueError("Expected 32-byte version-1 metadata")
    return struct.unpack("<4Q", blob)


def parse_config(blob):
    """Parse the observed version-2 filter configuration, preserving raw values.

    Field widths follow Music's consumer. Most parameter meanings are unknown.
    """
    offset = 0

    def read(fmt):
        nonlocal offset
        size = struct.calcsize("<" + fmt)
        if offset + size > len(blob):
            raise ValueError("Truncated configuration")
        result = struct.unpack_from("<" + fmt, blob, offset)
        offset += size
        return result

    count, = read("I")
    if count > max(0, (len(blob) - 20) // 4):
        raise ValueError("Configuration filter count exceeds envelope")
    filters = []
    for _ in range(count):
        kind, = read("I")
        if kind == 2:
            index, length = read("II")
            if length > (len(blob) - offset) // 12:
                raise ValueError("Configuration record count exceeds envelope")
            records, keys = [], set()
            for _ in range(length):
                key, size = read("QI")
                if size > (len(blob) - offset) // 8 or key in keys:
                    raise ValueError("Invalid configuration list size or duplicate key")
                records.append((key, list(read(f"{size}Q"))))
                keys.add(key)
            filters.append(dict(type=kind, metadata_index=index, records=records))
        elif kind in (1, 3, 4, 5):
            parameters = list(read("4I" if kind != 5 else "2I"))
            filters.append(dict(type=kind, parameters=parameters))
        else:
            raise ValueError("Unsupported configuration filter type")
    flags, result_word_1, result_word_2 = read("QII")
    if offset != len(blob):
        raise ValueError("Unexpected trailing configuration data")
    return dict(version=2, filters=filters, flags=flags,
                result_words=[result_word_1, result_word_2])


def pack_config(config):
    """Lossless encoder; does not assign or infer configuration parameters."""
    if config.get("version") != 2:
        raise ValueError("Unsupported configuration version")
    filters = config["filters"]
    encoded = bytearray(struct.pack("<I", len(filters)))
    for item in filters:
        kind = item["type"]
        encoded.extend(struct.pack("<I", kind))
        if kind == 2:
            records = item["records"]
            encoded.extend(struct.pack("<II", item["metadata_index"], len(records)))
            for key, values in records:
                encoded.extend(struct.pack("<QI", key, len(values)))
                encoded.extend(struct.pack(f"<{len(values)}Q", *values))
        elif kind in (1, 3, 4, 5):
            expected = 2 if kind == 5 else 4
            if len(item["parameters"]) != expected:
                raise ValueError("Wrong number of filter parameters")
            encoded.extend(struct.pack(f"<{expected}I", *item["parameters"]))
        else:
            raise ValueError("Unsupported configuration filter type")
    if len(config["result_words"]) != 2:
        raise ValueError("Wrong number of result words")
    encoded.extend(struct.pack("<QII", config["flags"], *config["result_words"]))
    parse_config(encoded)
    return bytes(encoded)


def parse_similarities(blob):
    if len(blob) < 12:
        raise ValueError("Truncated similarity header")
    compression, length, count = struct.unpack_from("<3I", blob)
    if compression != 0:
        raise ValueError("Compressed similarity payloads are not supported")
    if length != len(blob) - 8 or len(blob) != 12 + count * 8:
        raise ValueError("Inconsistent similarity size/count")
    return compression, list(struct.unpack_from(f"<{count}Q", blob, 12))


def pack_similarities(compression, ids):
    if not isinstance(compression, int) or isinstance(compression, bool) or not 0 <= compression <= 0xFFFFFFFF:
        raise ValueError("Header word outside uint32")
    if compression != 0:
        raise ValueError("Compressed similarity payloads are not supported")
    ids = list(ids)
    if len(ids) > (0xFFFFFFFF - 4) // 8:
        raise ValueError("Too many similarity IDs")
    for value in ids:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Similarity ID outside uint64")
    return struct.pack("<3I", compression, 4 + len(ids) * 8, len(ids)) + struct.pack(f"<{len(ids)}Q", *ids)


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
