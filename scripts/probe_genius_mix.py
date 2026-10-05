"""Read-only, build-pinned Genius Mix entry-point inventory.

String references are bounded static candidates, not proof of a storage ABI or
native Mix playback. No library, account, or device is opened by this probe.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

from decrypt_genius import SUPPORTED_SHA256
from inspect_music_macho import MachO, signed

NEEDLES = ('geniusmix', 'genius-mix', 'genius mix',
           'geniuscreateclustercontext', 'geniuscreatetrackslistfromclustercontext')
CORE_ENTRIES = {0x10036532C: 'existing_cluster_constructor',
                0x100365718: 'existing_next_track',
                0x100365D94: 'existing_cluster_destructor'}


def matching_strings(image):
    """Match full NUL-delimited strings, never an interior substring address."""
    found = {}
    for section in image.sections:
        if section['name'] not in ('__cstring', '__oslogstring'):
            continue
        raw = image.blob(section['offset'], section['size'])
        offset = 0
        for value in raw.split(b'\0')[:-1]:
            decoded = value.decode(errors='replace')
            if any(needle in decoded.lower() for needle in NEEDLES):
                found[section['address'] + offset] = decoded
            offset += len(value) + 1
    return found


def register_number(name):
    # A write to Wn also invalidates a previously computed Xn page pointer.
    if name[:1] in ('x', 'w') and name[1:].isdigit():
        return int(name[1:])
    return {'fp': 29, 'lr': 30}.get(name)


def address_references(raw, base, targets, function_end, window=12):
    """Find ADRP/ADD pairs within a straight-line register-live window.

    Stop at control flow, clobbers, undecodable instructions, and function ends.
    Indirect loads and pointer fixups are intentionally outside this coverage.
    """
    engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    engine.detail = True
    pages = {address & ~4095 for address in targets}
    references = []
    for offset in range(0, len(raw) - 3, 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        if word & 0x9F000000 != 0x90000000:
            continue
        pc = base + offset
        page = (pc & ~4095) + signed(((word >> 5) & 0x7FFFF) << 2
                                    | ((word >> 29) & 3), 21) * 4096
        if page not in pages:
            continue
        register = word & 31
        if register == 31:
            continue
        end = min(len(raw), offset + 4 * (window + 1), function_end(pc) - base)
        for next_offset in range(offset + 4, end - 3, 4):
            instruction_word = struct.unpack_from('<I', raw, next_offset)[0]
            instruction_pc = base + next_offset
            instruction = next(engine.disasm(raw[next_offset:next_offset + 4], instruction_pc), None)
            if instruction is None:
                break
            mnemonic = instruction.mnemonic
            if (mnemonic in ('b', 'bl', 'br', 'blr', 'ret', 'retaa', 'retab',
                             'cbz', 'cbnz', 'tbz', 'tbnz', 'svc', 'brk')
                    or mnemonic.startswith(('b.', 'bra', 'blra'))):
                break
            # 64-bit ADD immediate; 32-bit arithmetic cannot form this address.
            if (instruction_word & 0xFF000000 == 0x91000000
                    and (instruction_word >> 5) & 31 == register):
                address = page + ((instruction_word >> 10) & 4095) * (
                    4096 if instruction_word & (1 << 22) else 1)
                if address in targets:
                    references.append(dict(pc=hex(instruction_pc), adrp_pc=hex(pc),
                                           address=hex(address), string=targets[address]))
            _, written = instruction.regs_access()
            if any(register_number(instruction.reg_name(reg)) == register for reg in written):
                break
    return references


def inspect(executable):
    image = MachO(executable)
    digest = hashlib.sha256(image.data).hexdigest()
    if digest != SUPPORTED_SHA256:
        raise ValueError('Unsupported Music binary')
    targets = matching_strings(image)
    raw = image.blob(image.text['offset'], image.text['size'])
    refs = address_references(raw, image.text['address'], targets,
                              lambda pc: image.function(pc)[1])
    for ref in refs:
        ref['function'] = hex(image.function(int(ref['pc'], 16))[0])
    calls = []
    for offset in range(0, len(raw) - 3, 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        if word & 0xFC000000 == 0x94000000:
            pc = image.text['address'] + offset
            destination = pc + signed(word & 0x3FFFFFF, 26) * 4
            if destination in CORE_ENTRIES:
                calls.append(dict(pc=hex(pc), function=hex(image.function(pc)[0]),
                                  target=hex(destination), role=CORE_ENTRIES[destination]))
    return dict(schema_version=1, executable_sha256=digest,
                strings={hex(address): value for address, value in targets.items()},
                references=refs, existing_core_calls=calls,
                coverage='full_cstrings_and_oslogstrings_bounded_live_adrp_add_and_direct_bl',
                native_mix_playback_verified=False, mix_storage_format_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect(args.executable)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
