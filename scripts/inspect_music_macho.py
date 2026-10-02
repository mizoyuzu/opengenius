"""Read-only static inspection of a thin little-endian ARM64 Music executable.

Addresses are unslid virtual addresses. This is a targeted research tool, not a
general Mach-O loader: chained pointers and indirect calls are not resolved.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import struct


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class MachO:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.sections = []
        self.segments = []
        self.symbols = []
        self.imports = {}
        self.functions = []
        self.uuid = None
        magic, cpu, subtype, _, count, size, _, _ = self.unpack("8I", 0)
        if magic != 0xFEEDFACF or cpu != 0x100000C:
            raise ValueError("Expected thin little-endian ARM64 Mach-O")
        self.subtype = subtype
        self.blob(32, size)
        pos = 32
        symtab = dysymtab = function_data = None
        base = None
        for _ in range(count):
            cmd, length = self.unpack("2I", pos)
            if length < 8 or pos + length > 32 + size:
                raise ValueError("Invalid load command bounds")
            if cmd == 0x19:
                seg = self.unpack("16s4Q4I", pos + 8)
                self.segments.append(dict(name=seg[0].rstrip(b"\0").decode(),
                    address=seg[1], size=seg[2], offset=seg[3], file_size=seg[4]))
                if seg[0].rstrip(b"\0") == b"__TEXT":
                    base = seg[1]
                if 72 + seg[7] * 80 > length:
                    raise ValueError("Invalid section count")
                for n in range(seg[7]):
                    v = self.unpack("16s16s2Q8I", pos + 72 + n * 80)
                    self.sections.append(dict(name=v[0].rstrip(b"\0").decode(),
                        segment=v[1].rstrip(b"\0").decode(), address=v[2], size=v[3],
                        offset=v[4], flags=v[8], reserved1=v[9], reserved2=v[10]))
            elif cmd == 2:
                symtab = self.unpack("4I", pos + 8)
            elif cmd == 0xB:
                dysymtab = self.unpack("18I", pos + 8)
            elif cmd == 0x26:
                function_data = self.unpack("2I", pos + 8)
            elif cmd == 0x1B:
                self.uuid = self.blob(pos + 8, 16).hex()
            pos += length
        if pos != 32 + size or base is None:
            raise ValueError("Inconsistent load commands or missing __TEXT")
        if symtab:
            off, number, strings, string_size = symtab
            names = self.blob(strings, string_size)
            for n in range(number):
                index, kind, section, desc, address = self.unpack("IBBHQ", off + n * 16)
                if index >= len(names):
                    raise ValueError("Invalid symbol string index")
                end = names.find(b"\0", index)
                if end < 0:
                    raise ValueError("Unterminated symbol")
                self.symbols.append((names[index:end].decode(errors="replace"), address, kind))
        if dysymtab:
            off, count = dysymtab[12:14]
            indirect = self.unpack(f"{count}I", off)
            for section in self.sections:
                typ = section["flags"] & 255
                if typ not in (6, 7, 8):
                    continue
                stride = section["reserved2"] if typ == 8 else 8
                if not stride:
                    raise ValueError("Zero symbol stub stride")
                for n in range(section["size"] // stride):
                    index = indirect[section["reserved1"] + n]
                    if index & 0xC0000000:
                        continue
                    self.imports[section["address"] + n * stride] = self.symbols[index][0]
        if function_data:
            raw = self.blob(*function_data)
            value = shift = 0
            address = base
            for byte in raw:
                value |= (byte & 127) << shift
                if byte & 128:
                    shift += 7
                    if shift > 63:
                        raise ValueError("Invalid ULEB128")
                elif value:
                    address += value
                    self.functions.append(address)
                    value = shift = 0
                else:
                    break
        self.text = next(s for s in self.sections if s["name"] == "__text")
        self.blob(self.text["offset"], self.text["size"])

    def blob(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError("Mach-O range outside file")
        return self.data[offset:offset + size]

    def unpack(self, fmt, offset):
        return struct.unpack("<" + fmt, self.blob(offset, struct.calcsize("<" + fmt)))

    def file_offset(self, address):
        for section in self.sections:
            if section["address"] <= address < section["address"] + section["size"]:
                return section["offset"] + address - section["address"]
        raise ValueError(f"Address not in a section: {address:#x}")

    def function(self, address):
        i = bisect.bisect_right(self.functions, address) - 1
        if i < 0:
            raise ValueError("No function start")
        start = self.functions[i]
        end = self.functions[i + 1] if i + 1 < len(self.functions) else self.text["address"] + self.text["size"]
        return start, end

    def cstring(self, address):
        for sec in self.sections:
            if sec["name"] in ("__cstring", "__objc_methname", "__objc_classname") and sec["address"] <= address < sec["address"] + sec["size"]:
                off = self.file_offset(address)
                end = self.data.find(b"\0", off, sec["offset"] + sec["size"])
                if end >= 0:
                    return self.data[off:end].decode(errors="replace")

    def scan(self, needles, call_target=None, addresses=None):
        targets = {a: hex(a) for a in (addresses or [])}
        for sec in self.sections:
            if sec["name"] != "__cstring":
                continue
            raw = self.blob(sec["offset"], sec["size"])
            for needle in needles:
                pos = 0
                while (pos := raw.find(needle.encode(), pos)) >= 0:
                    targets[sec["address"] + pos] = needle
                    pos += len(needle)
        calls, refs = [], []
        raw = self.blob(self.text["offset"], self.text["size"])
        # Adjacent ADRP + ADD only. A wider window needs register-liveness
        # analysis; otherwise overwritten registers produce false references.
        for i in range(0, len(raw) - 3, 4):
            w = struct.unpack_from("<I", raw, i)[0]
            pc = self.text["address"] + i
            if w & 0xFC000000 == 0x94000000:
                dest = pc + signed(w & 0x3FFFFFF, 26) * 4
                if dest in self.imports or dest == call_target:
                    calls.append(dict(pc=hex(pc), target=hex(dest), symbol=self.imports.get(dest, "internal"), function=hex(self.function(pc)[0])))
            if w & 0x9F000000 != 0x90000000:
                continue
            page = (pc & ~4095) + signed(((w >> 5) & 0x7FFFF) << 2 | ((w >> 29) & 3), 21) * 4096
            if not any(page == a & ~4095 for a in targets):
                continue
            rd = w & 31
            for j in range(i + 4, min(i + 8, len(raw) - 3), 4):
                a = struct.unpack_from("<I", raw, j)[0]
                if ((a >> 5) & 31) != rd:
                    continue
                if a & 0xFF000000 == 0x91000000:
                    dest = page + ((a >> 10) & 4095) * (4096 if a & (1 << 22) else 1)
                    if dest in targets:
                        at = self.text["address"] + j
                        refs.append(dict(pc=hex(at), string=targets[dest], address=hex(dest), function=hex(self.function(at)[0])))
        refs = list({(r["pc"], r["address"]): r for r in refs}.values())
        return dict(strings={hex(k): v for k, v in targets.items()}, references=refs, calls=calls)

    def disassemble(self, address):
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        start, end = self.function(address)
        engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        engine.skipdata = True
        print(f"function {start:#x}..{end:#x}, file {self.file_offset(start):#x}")
        previous_page = None
        for pc, size, mnemonic, operands in engine.disasm_lite(self.blob(self.file_offset(start), end - start), start):
            note = ""
            if mnemonic in ("bl", "b") and operands.startswith("#"):
                target = int(operands[1:], 16)
                note = self.imports.get(target, "")
            if mnemonic == "adrp":
                reg, immediate = operands.split(", ")
                previous_page = (reg, int(immediate[1:], 16))
            elif mnemonic == "add":
                args = operands.split(", ")
                if len(args) == 3 and previous_page and args[1] == previous_page[0] and args[2].startswith("#"):
                    value = previous_page[1] + int(args[2][1:], 0)
                    note = self.cstring(value) or ""
                previous_page = None
            else:
                previous_page = None
            print(f"{pc:#x}: {mnemonic:8} {operands}" + (f" ; {note}" if note else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--function", type=lambda x: int(x, 0))
    parser.add_argument("--strings", nargs="+", default=["geniusKeyHeader", "geniusSeed", "Genius.itdb"])
    parser.add_argument("--symbol", default="sqlite3_key")
    parser.add_argument("--call-target", type=lambda x: int(x, 0))
    parser.add_argument("--address", nargs="+", type=lambda x: int(x, 0), default=[])
    args = parser.parse_args()
    obj = MachO(args.executable)
    if args.function is not None:
        obj.disassemble(args.function)
    else:
        result = obj.scan(args.strings, args.call_target, args.address)
        result["calls"] = [c for c in result["calls"] if (c["target"] == hex(args.call_target) if args.call_target is not None else args.symbol in c["symbol"])]
        result.update(sha256=hashlib.sha256(obj.data).hexdigest(), uuid=obj.uuid,
                      size=len(obj.data), function_count=len(obj.functions))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
