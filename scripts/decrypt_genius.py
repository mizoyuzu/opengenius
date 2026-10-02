"""Recover a Genius DB copy using the supplied Music 1.7.0.146 executable.

No macOS process is launched. Only the identified key derivation routine runs
in an isolated ARM64 emulator with no system-call or external-library support.
Pointer authentication is omitted; this is not a general macOS emulator.
"""
import argparse
import hashlib
import sqlite3
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from inspect_music_library import decode_musicdb
from inspect_music_macho import MachO


SUPPORTED_SHA256 = "a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a"
MAGIC = b"SQLite format 3\0"


def extract_key_header(preferences):
    data = decode_musicdb(preferences)
    matches = []
    pos = data.find(b"boma")
    while pos >= 0:
        if pos + 20 > len(data):
            raise ValueError("Truncated BOMA header")
        header, total, kind = struct.unpack_from("<III", data, pos + 4)
        if header < 20 or total < header or pos + total > len(data):
            raise ValueError("Invalid BOMA bounds")
        if kind == 502:
            matches.append(data[pos + header:pos + total])
        pos = data.find(b"boma", pos + total)
    if len(matches) != 1 or len(matches[0]) != 20:
        raise ValueError("Expected one 20-byte Genius key header (BOMA 502)")
    header = matches[0]
    if int.from_bytes(header[:2], "big") != 1 or int.from_bytes(header[2:4], "big") >= 64:
        raise ValueError("Unsupported Genius key header version or selector")
    return header


def derive_key(executable, header):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_FP, UC_ARM64_REG_LR, UC_ARM64_REG_PC

    image = MachO(executable)
    if hashlib.sha256(image.data).hexdigest() != SUPPORTED_SHA256:
        raise ValueError("Unsupported Music binary: routine addresses are build-specific")
    if len(header) != 20:
        raise ValueError("Expected 20-byte key header")
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    base = 0x100000000
    for segment in image.segments:
        if segment["name"] == "__PAGEZERO" or not segment["size"]:
            continue
        start = segment["address"] & ~4095
        end = (segment["address"] + segment["size"] + 4095) & ~4095
        cpu.mem_map(start, end - start)
        if segment["file_size"]:
            cpu.mem_write(segment["address"], image.blob(segment["offset"], segment["file_size"]))

    # dyld_chained_ptr_arm64e_* with DYLD_CHAINED_PTR_ARM64E_USERLAND24.
    # Rebase pointers to their unslid image addresses. Bind pointers go to an
    # inert mapped area: any attempt to execute there fails closed below.
    external_base = 0x600000000
    pos = 32
    for _ in range(image.unpack("I", 16)[0]):
        command, length = image.unpack("II", pos)
        if command == 0x80000034:
            offset, size = image.unpack("II", pos + 8)
            image.blob(offset, size)
            fixup = image.unpack("7I", offset)
            if fixup[0] != 0 or fixup[5] != 2 or fixup[6] != 0:
                raise ValueError("Unsupported chained fixup format")
            starts = offset + fixup[1]
            count = image.unpack("I", starts)[0]
            for relative in image.unpack(f"{count}I", starts + 4):
                if not relative:
                    continue
                _, page_size, fmt, segment_offset, _, pages = image.unpack("IHHQIH", starts + relative)
                if fmt != 12:
                    raise ValueError("Unsupported chained pointer format")
                for page, start in enumerate(image.unpack(f"{pages}H", starts + relative + 22)):
                    if start == 0xFFFF:
                        continue
                    if start & 0x8000:
                        raise ValueError("Multiple fixup chains per page are unsupported")
                    address = base + segment_offset + page * page_size + start
                    page_end = base + segment_offset + (page + 1) * page_size
                    while True:
                        if address + 8 > page_end:
                            raise ValueError("Fixup chain exceeds page")
                        raw = struct.unpack("<Q", cpu.mem_read(address, 8))[0]
                        next_delta = (raw >> 51) & 2047
                        if (raw >> 62) & 1:
                            ordinal = raw & 0xFFFFFF
                            if ordinal >= fixup[4]:
                                raise ValueError("Invalid bind ordinal")
                            value = external_base + ordinal * 16
                        else:
                            value = base + (raw & 0xFFFFFFFF if raw >> 63 else raw & ((1 << 43) - 1))
                        cpu.mem_write(address, struct.pack("<Q", value))
                        if not next_delta:
                            break
                        address += next_delta * 8
        pos += length

    cpu.mem_map(external_base, 0x100000)
    memory = 0x700000000
    cpu.mem_map(memory, 0x200000)
    output, capacity = memory + 0x1000, memory + 0x2000
    stop = external_base + 0xFFFF0
    cpu.mem_write(memory, header)
    cpu.mem_write(capacity, struct.pack("<I", 16))
    for register, value in [(UC_ARM64_REG_SP, memory + 0x1F0000),
                            (UC_ARM64_REG_X0, memory), (UC_ARM64_REG_X1, 20),
                            (UC_ARM64_REG_X2, output), (UC_ARM64_REG_X3, capacity),
                            (UC_ARM64_REG_LR, stop)]:
        cpu.reg_write(register, value)
    disassembler = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    register_ids = {f"x{i}": UC_ARM64_REG_X0 + i for i in range(29)}
    register_ids.update(x29=UC_ARM64_REG_FP, x30=UC_ARM64_REG_LR)
    cache = {}
    failure = []
    steps = 0

    def step(cpu, address, size, unused):
        nonlocal steps
        steps += 1
        if not base <= address < base + len(image.data):
            failure.append("Derivation attempted to execute outside Music image")
            cpu.emu_stop()
            return
        if address not in cache:
            cache[address] = next(disassembler.disasm_lite(bytes(cpu.mem_read(address, 4)), address), None)
        instruction = cache[address]
        if instruction is None:
            failure.append("Unsupported instruction")
            cpu.emu_stop()
            return
        _, _, mnemonic, operands = instruction
        if mnemonic in ("blraa", "blrab", "braa", "brab", "blraaz", "blrabz", "braaz", "brabz"):
            destination = cpu.reg_read(register_ids[operands.split(",")[0]])
            if mnemonic.startswith("bl"):
                cpu.reg_write(UC_ARM64_REG_LR, address + 4)
            cpu.reg_write(UC_ARM64_REG_PC, destination)
        elif mnemonic in ("retab", "retaa"):
            cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))
        elif mnemonic.startswith(("pac", "aut", "xpac")):
            cpu.reg_write(UC_ARM64_REG_PC, address + 4)

    cpu.hook_add(UC_HOOK_CODE, step)
    try:
        cpu.emu_start(0x1011B39AC, stop, timeout=10_000_000, count=1_000_000)
    except UcError as error:
        raise ValueError("Derivation could not complete in isolated emulator") from error
    if failure or cpu.reg_read(UC_ARM64_REG_PC) != stop:
        raise ValueError(failure[0] if failure else "Derivation exceeded execution limit")
    if cpu.reg_read(UC_ARM64_REG_X0) != 0 or struct.unpack("<I", cpu.mem_read(capacity, 4))[0] != 16:
        raise ValueError("Music key derivation rejected the header")
    return bytes(cpu.mem_read(output, 16)), steps


def decrypt_pages(encrypted, key):
    if len(key) != 16 or len(encrypted) < 4096 or len(encrypted) % 4096:
        raise ValueError("Expected AES-128 key and complete 4096-byte pages")
    if encrypted[16:24] != bytes.fromhex("100001010c402020"):
        raise ValueError("Unsupported Genius page header")
    result = bytearray()
    for number in range(1, len(encrypted) // 4096 + 1):
        page = encrypted[(number - 1) * 4096:number * 4096]
        iv = struct.pack("<I", number) + page[-12:]
        cipher = Cipher(algorithms.AES(key), modes.OFB(iv)).decryptor()
        clear = bytearray(cipher.update(page[:-12]) + cipher.finalize())
        clear.extend(page[-12:])
        if number == 1:
            if clear[:16] != MAGIC:
                raise ValueError("Key did not recover the SQLite header")
            clear[16:24] = page[16:24]
        result.extend(clear)
    return bytes(result)


def validate_database(clear):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(clear)
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Decrypted SQLite database failed integrity check")
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {"genius_metadata", "genius_similarities", "genius_config"}
        if not expected <= names:
            raise ValueError("Missing expected Genius tables")
        return {name: connection.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
                for name in sorted(names) if name.startswith("genius_") and name.replace("_", "").isalnum()}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bundle, executable, output = args.bundle.resolve(), args.executable.resolve(), args.output.resolve()
    if output.is_relative_to(bundle) or output.is_relative_to(executable.parent):
        parser.error("Output must be outside the source library and executable directory")
    if output.exists():
        parser.error("Output already exists; refusing to overwrite")
    header = extract_key_header((bundle / "Library Preferences.musicdb").read_bytes())
    key, steps = derive_key(executable, header)
    encrypted = (bundle / "Genius.itdb").read_bytes()
    clear = decrypt_pages(encrypted, key)
    counts = validate_database(clear)
    with output.open("xb") as file:
        file.write(clear)
    print(f"Verified decrypted copy: {len(clear)} bytes; {steps} emulated instructions")
    for name, count in counts.items():
        print(f"{name}: {count} rows")
    # Key material is deliberately neither logged nor saved.


if __name__ == "__main__":
    main()
