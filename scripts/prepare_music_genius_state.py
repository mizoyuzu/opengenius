"""Prepare offline copies with matching local Genius state in both Music DBs.

Requires an existing Genius dataset. This does not contact Apple, create a
Genius account, or install files into a live library. Stop Music and its library
agent and back up the complete library before installing the prepared pair.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from decrypt_genius import extract_key_header
from inspect_music_library import decode_musicdb, u32
from rewrite_music_ids import encode_library


def patch_state(raw):
    expanded = decode_musicdb(raw)
    position = expanded.find(b"plma")
    if (position < 0 or expanded.find(b"plma", position + 1) >= 0
            or u32(expanded, position + 4) != 194
            or position + 194 > len(expanded)):
        raise ValueError("Unsupported or ambiguous plma profile")
    offset = position + 12
    if expanded[offset] not in (0, 1):
        raise ValueError("Refusing to overwrite a transitional Genius state")
    changed = bytearray(expanded)
    changed[offset] = 1
    return expanded, bytes(changed), offset


def encode_preferences(expanded, template):
    if (template[:4] != b"hfma" or u32(template, 4) != 160
            or u32(template, 8) != len(template)
            or u32(template, 128) not in (0, len(template))
            or u32(template, 84) != 102400):
        raise ValueError("Unsupported Preferences hfma profile")
    compressed = zlib.compress(expanded, 1)
    header = bytearray(template[:160])
    struct.pack_into("<I", header, 8, 160 + len(compressed))
    if u32(template, 128):
        struct.pack_into("<I", header, 128, 160 + len(compressed))
    encrypted_size = min(u32(header, 84), len(compressed)) // 16 * 16
    cipher = Cipher(algorithms.AES(b"BHUILuilfghuila3"), modes.ECB()).encryptor()
    return (bytes(header) + cipher.update(compressed[:encrypted_size])
            + cipher.finalize() + compressed[encrypted_size:])


def prepare_pair(source, output, *, allow_unverified_profile=False):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Output must be a new directory")
    prepared, report, key_headers = {}, {}, []
    for name in ("Library.musicdb", "Library Preferences.musicdb"):
        raw = (source / name).read_bytes()
        original, expanded, offset = patch_state(raw)
        if name == "Library.musicdb":
            encoded = encode_library(expanded, raw,
                allow_unverified_profile=allow_unverified_profile)
        else:
            encoded = encode_preferences(expanded, raw)
        if decode_musicdb(encoded) != expanded:
            raise ValueError("Prepared file failed its round-trip check")
        restored = bytearray(expanded)
        restored[offset] = original[offset]
        if bytes(restored) != original:
            raise ValueError("Unexpected expanded data change")
        key_header = extract_key_header(raw)
        if extract_key_header(encoded) != key_header:
            raise ValueError("Genius key header changed")
        key_headers.append(key_header)
        prepared[name] = encoded
        report[name] = {
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "output_sha256": hashlib.sha256(encoded).hexdigest(),
            "expanded_offset": offset, "before": original[offset], "after": 1,
            "only_state_byte_changed": True, "key_header_preserved": True,
        }
    if key_headers[0] != key_headers[1]:
        raise ValueError("Source DBs have different Genius key headers")
    output.mkdir(parents=True)
    for name, raw in prepared.items():
        (output / name).write_bytes(raw)
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--allow-unverified-profile", action="store_true",
                        help="Explicitly allow the legacy 1.5.6.11 codec profile")
    args = parser.parse_args()
    print(json.dumps(prepare_pair(args.source, args.output,
        allow_unverified_profile=args.allow_unverified_profile), indent=2))


if __name__ == "__main__":
    main()
