"""Run a bounded Music Genius core experiment, with explicit host callbacks.

This emulates the hash-pinned ARM64 core, not macOS/Music.app or an iPod.
Database access and history/eligibility/randomness are supplied by this harness.
Unimplemented imports, instructions outside executable code and syscalls fail.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm64_const as regs

from decrypt_genius import SUPPORTED_SHA256, derive_key, extract_key_header, decrypt_pages, validate_database
from genius_format import parse_config, pack_config, parse_id, parse_similarities, unsigned_id
from inspect_music_macho import MachO


class MusicCore:
    BASE = 0x100000000
    EXTERNAL = 0x600000000
    HEAP = 0x700000000
    HEAP_SIZE = 0x2000000
    STACK = 0x710000000
    STOP = EXTERNAL + 0xFF000

    def __init__(self, executable, config, metadata, similarities, random_seed=0):
        self.image = MachO(executable)
        if hashlib.sha256(self.image.data).hexdigest() != SUPPORTED_SHA256:
            raise ValueError("Unsupported Music binary")
        self.cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        for segment in self.image.segments:
            if segment['name'] == '__PAGEZERO' or not segment['size']:
                continue
            start = segment['address'] & ~4095
            end = (segment['address'] + segment['size'] + 4095) & ~4095
            self.cpu.mem_map(start, end - start)
            if segment['file_size']:
                self.cpu.mem_write(segment['address'], self.image.blob(segment['offset'], segment['file_size']))
        self.cpu.mem_map(self.EXTERNAL, 0x100000)
        self.cpu.mem_map(self.HEAP, self.HEAP_SIZE)
        self.cpu.mem_map(self.STACK, 0x100000)
        self.external_names = dict(self.image.imports)
        self._fixups()
        self.heap_next = self.HEAP
        self.allocations = {}
        self.handlers = {}
        self.calls = Counter()
        self.steps = 0
        self.failure = None
        self.config = config
        self.metadata = metadata
        self.similarities = similarities
        self.random = random.Random(random_seed)
        self.random_seed = random_seed
        self.library = None
        self.engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        self.instructions = {}
        self.registers = {f'x{i}': getattr(regs, f'UC_ARM64_REG_X{i}') for i in range(31)}
        self.cpu.hook_add(UC_HOOK_CODE, self._step)
        self.callbacks = self.alloc(0x50)
        # Offsets are the core's callback ABI, separate from Apple's SQL layer.
        callbacks = {0: self._config, 8: self._metadata, 0x10: self._eligible,
                     0x18: self._similarities, 0x20: self._free,
                     0x28: self._history, 0x30: self._history,
                     0x38: self._time, 0x40: self._random,
                     0x48: self._byte_order}
        for offset, handler in callbacks.items():
            address = self.EXTERNAL + 0x80000 + offset * 16
            self.handlers[address] = handler
            self.write(self.callbacks + offset, 'Q', address)

    def _fixups(self):
        pos = 32
        for _ in range(self.image.unpack('I', 16)[0]):
            command, length = self.image.unpack('II', pos)
            if command == 0x80000034:
                offset, size = self.image.unpack('II', pos + 8)
                self.image.blob(offset, size)
                header = self.image.unpack('7I', offset)
                if header[0] != 0 or header[5:7] != (2, 0):
                    raise ValueError('Unsupported chained fixups')
                bindings = []
                for ordinal in range(header[4]):
                    word, addend = self.image.unpack('Ii', offset + header[2] + ordinal * 8)
                    name_offset = offset + header[3] + (word >> 9)
                    end = self.image.data.find(b'\0', name_offset, offset + size)
                    if end < 0:
                        raise ValueError('Invalid import name')
                    name = self.image.data[name_offset:end].decode()
                    bindings.append(name if not addend else 'unsupported_addend:' + name)
                starts = offset + header[1]
                count, = self.image.unpack('I', starts)
                for relative in self.image.unpack(f'{count}I', starts + 4):
                    if not relative:
                        continue
                    _, page_size, fmt, seg_offset, _, pages = self.image.unpack('IHHQIH', starts + relative)
                    if fmt != 12:
                        raise ValueError('Unsupported chained pointer format')
                    for page, start in enumerate(self.image.unpack(f'{pages}H', starts + relative + 22)):
                        if start == 0xFFFF:
                            continue
                        if start & 0x8000:
                            raise ValueError('Unsupported multiple page chains')
                        address = self.BASE + seg_offset + page * page_size + start
                        end = self.BASE + seg_offset + (page + 1) * page_size
                        while True:
                            if address + 8 > end:
                                raise ValueError('Fixup exceeds page')
                            raw, = struct.unpack('<Q', self.cpu.mem_read(address, 8))
                            delta = (raw >> 51) & 2047
                            if raw & (1 << 62):
                                ordinal = raw & 0xFFFFFF
                                if ordinal >= len(bindings):
                                    raise ValueError('Invalid bind ordinal')
                                value = self.EXTERNAL + ordinal * 16
                                self.external_names[value] = bindings[ordinal]
                            else:
                                value = self.BASE + (raw & (0xFFFFFFFF if raw >> 63 else (1 << 43) - 1))
                            self.cpu.mem_write(address, struct.pack('<Q', value))
                            if not delta:
                                break
                            address += delta * 8
            pos += length

    def read(self, address, fmt):
        return struct.unpack('<' + fmt, self.cpu.mem_read(address, struct.calcsize('<' + fmt)))

    def write(self, address, fmt, *values):
        self.cpu.mem_write(address, struct.pack('<' + fmt, *values))

    def alloc(self, size, content=None):
        if not 0 <= size <= self.HEAP_SIZE:
            raise ValueError('Invalid allocation size')
        address = self.heap_next
        self.heap_next += max(16, (size + 15) & ~15)
        if self.heap_next > self.HEAP + self.HEAP_SIZE:
            raise ValueError('Emulator heap exhausted')
        self.allocations[address] = size
        if content is not None:
            if len(content) > size:
                raise ValueError('Allocation content exceeds size')
            self.cpu.mem_write(address, content)
        return address

    def _config(self, context, size_out, version_out, *unused):
        self.write(size_out, 'Q', len(self.config))
        self.write(version_out, 'I', 2)
        return self.alloc(len(self.config), self.config)

    def _blob(self, rows, identifier, size_out):
        blob = rows.get(identifier)
        if size_out:
            self.write(size_out, 'Q', len(blob) if blob else 0)
        return self.alloc(len(blob), blob) if blob else 0

    def _metadata(self, context, identifier, size_out, *unused):
        return self._blob(self.metadata, identifier, size_out)

    def _similarities(self, context, identifier, size_out, *unused):
        return self._blob(self.similarities, identifier, size_out)

    def _eligible(self, context, identifier, *unused):
        return int(identifier in self.metadata)

    def _history(self, context, identifier, time_out, count_out, *unused):
        # Synthetic history fixture, not the source library's listening history.
        self.write(time_out, 'I', 0)
        self.write(count_out, 'I', 0)
        return 0

    def _time(self, *unused):
        return 0

    def _random(self, context, bound, *unused):
        if not 0 < bound <= 0xFFFFFFFF:
            raise ValueError('Invalid random bound')
        return self.random.randrange(bound)

    def _free(self, address, *unused):
        # Keep allocations mapped for bounded experiments; no host pointers.
        if address and address not in self.allocations:
            raise ValueError('Invalid free pointer')
        return 0

    def _byte_order(self, context, address, width, count, *unused):
        if width not in (4, 8) or count * width > self.HEAP_SIZE:
            raise ValueError('Unsupported byte-order callback')
        # The fixture is little endian, as is the emulated ARM64 core.
        return 0

    def _import(self, name, args):
        if name.startswith(('__Znwm', '__Znam')) or name == '_malloc':
            return self.alloc(args[0])
        if name.startswith(('__ZdlPv', '__ZdaPv')) or name == '_free':
            return self._free(args[0])
        if name == '__ZNSt3__112__next_primeEm':
            if args[0] == 0:
                return 0
            value = max(2, args[0])
            if value > 1_000_000:
                raise ValueError('Oversized prime request')
            while any(value % factor == 0 for factor in range(2, math.isqrt(value) + 1)):
                value += 1
            return value
        if name == '_calloc':
            return self.alloc(args[0] * args[1])
        if name in ('_memmove', '_memcpy'):
            destination, source, size = args[:3]
            if size > self.HEAP_SIZE:
                raise ValueError('Oversized memory copy')
            if size:
                self.cpu.mem_write(destination, bytes(self.cpu.mem_read(source, size)))
            return destination
        if name in ('_bzero', '_memset'):
            destination = args[0]
            value, size = (0, args[1]) if name == '_bzero' else (args[1] & 255, args[2])
            if size > self.HEAP_SIZE:
                raise ValueError('Oversized memory fill')
            if size:
                self.cpu.mem_write(destination, bytes([value]) * size)
            return destination
        raise ValueError(f'Unimplemented import: {name}')

    def _step(self, cpu, address, size, unused):
        self.steps += 1
        try:
            if address in self.handlers or address in self.external_names:
                args = [cpu.reg_read(self.registers[f'x{i}']) for i in range(8)]
                if address in self.handlers:
                    handler = self.handlers[address]
                    name = handler.__name__
                    result = handler(*args)
                else:
                    name = self.external_names[address]
                    result = self._import(name, args)
                self.calls[name] += 1
                cpu.reg_write(regs.UC_ARM64_REG_X0, result & 0xFFFFFFFFFFFFFFFF)
                cpu.reg_write(regs.UC_ARM64_REG_PC, cpu.reg_read(regs.UC_ARM64_REG_LR))
                return
            text = self.image.text
            if not text['address'] <= address < text['address'] + text['size']:
                raise ValueError(f'Execution outside Music code: {address:#x}')
            if address not in self.instructions:
                self.instructions[address] = next(self.engine.disasm_lite(bytes(cpu.mem_read(address, 4)), address), None)
            instruction = self.instructions[address]
            if instruction is None:
                raise ValueError('Undecodable instruction')
            _, _, mnemonic, operands = instruction
            if mnemonic in ('svc', 'hvc', 'smc', 'brk'):
                raise ValueError(f'Unsupported trap at {address:#x}: {mnemonic}')
            if mnemonic in ('blraa', 'blrab', 'braa', 'brab', 'blraaz', 'blrabz', 'braaz', 'brabz'):
                target = cpu.reg_read(self.registers[operands.split(',')[0]])
                if mnemonic.startswith('bl'):
                    cpu.reg_write(regs.UC_ARM64_REG_LR, address + 4)
                cpu.reg_write(regs.UC_ARM64_REG_PC, target)
            elif mnemonic in ('retab', 'retaa'):
                cpu.reg_write(regs.UC_ARM64_REG_PC, cpu.reg_read(regs.UC_ARM64_REG_LR))
            elif mnemonic.startswith(('pac', 'aut', 'xpac')):
                cpu.reg_write(regs.UC_ARM64_REG_PC, address + 4)
        except Exception as error:
            self.failure = str(error)
            cpu.emu_stop()

    def run(self, entry, *args):
        self.failure = None
        self.cpu.reg_write(regs.UC_ARM64_REG_SP, self.STACK + 0xFF000)
        self.cpu.reg_write(regs.UC_ARM64_REG_LR, self.STOP)
        for i in range(8):
            self.cpu.reg_write(self.registers[f'x{i}'], args[i] if i < len(args) else 0)
        try:
            self.cpu.emu_start(entry, self.STOP, timeout=30_000_000, count=10_000_000)
        except UcError as error:
            pc = self.cpu.reg_read(regs.UC_ARM64_REG_PC)
            raise ValueError(f'Emulation failed at {pc:#x}: {error}') from error
        if self.failure or self.cpu.reg_read(regs.UC_ARM64_REG_PC) != self.STOP:
            raise ValueError(self.failure or f'Emulation limit reached after {self.steps} instructions at '
                             f'{self.cpu.reg_read(regs.UC_ARM64_REG_PC):#x}')
        return self.cpu.reg_read(regs.UC_ARM64_REG_X0)

    def generate(self, seed, limit):
        if self.library is None:
            self.library = self.run(0x100364D04, self.callbacks, 0, 0)
            if not self.library:
                raise ValueError('Music core rejected configuration')
        self.random.seed(self.random_seed)
        start_steps, start_calls = self.steps, self.calls.copy()
        cluster = self.run(0x10036532C, self.library, seed)
        if not cluster:
            raise ValueError('Music core rejected seed metadata')
        output = []
        for _ in range(limit):
            result = self.run(0x100365718, cluster)
            if not result:
                break
            if result not in self.metadata or result in output:
                raise ValueError('Core returned an unknown or duplicate Genius ID')
            output.append(result)
        self.run(0x100365D94, cluster)
        return dict(seed_genius_id=f'{seed:016X}', result_genius_ids=[f'{v:016X}' for v in output],
                    stopped_at_limit=len(output) == limit, instructions=self.steps - start_steps,
                    host_calls=dict(self.calls - start_calls))


def database_rows(clear):
    validate_database(clear)
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(clear)
        config = connection.execute('SELECT version,data FROM genius_config WHERE id=1').fetchone()
        if not config or config[0] != 2:
            raise ValueError('Expected one version-2 config')
        rows = []
        for name in ['genius_metadata', 'genius_similarities']:
            data = {}
            for identifier, version, blob in connection.execute(f'SELECT genius_id,version,data FROM {name}'):
                if version != 1:
                    raise ValueError('Unsupported Genius row version')
                data[unsigned_id(identifier)] = blob
            rows.append(data)
        return config[1], *rows
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--seed', type=parse_id)
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--experiment', type=Path, help='Encrypted experimental DB using the same library key')
    parser.add_argument('--profile', choices=['original', 'relations-only', 'without-distance',
                                             'without-compatible-genre'], default='original',
                        help='Controlled filter changes in emulator memory only')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Limit must be 1..100')
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(args.bundle.resolve()) or output.is_relative_to(args.executable.resolve().parent):
        parser.error('Output exists or is inside a source directory')
    key, _ = derive_key(args.executable, extract_key_header((args.bundle / 'Library Preferences.musicdb').read_bytes()))
    encrypted = (args.bundle / 'Genius.itdb').read_bytes()
    datasets = [('source', encrypted)]
    if args.experiment:
        datasets.append(('experiment', args.experiment.read_bytes()))
    observations = []
    for label, encoded in datasets:
        config, metadata, similarities = database_rows(decrypt_pages(encoded, key))
        if args.profile != 'original':
            parsed = parse_config(config)
            if args.profile == 'relations-only':
                parsed['filters'] = [f for f in parsed['filters'] if f['type'] == 1]
            else:
                excluded = 3 if args.profile == 'without-distance' else 2
                parsed['filters'] = [f for f in parsed['filters'] if f['type'] != excluded]
            if not parsed['filters']:
                raise ValueError('Controlled profile has no remaining filters')
            config = pack_config(parsed)
        seeds = [args.seed] if args.seed is not None else sorted(similarities)
        results = []
        core = MusicCore(args.executable, config, metadata, similarities)
        for seed in seeds:
            if seed not in metadata or seed not in similarities:
                raise ValueError('Seed missing from source data')
            result = core.generate(seed, args.limit)
            _, ids = parse_similarities(similarities[seed])
            result['stored_similarities'] = [f'{v:016X}' for v in ids]
            results.append(result)
        observations.append(dict(dataset=label, genius_sha256=hashlib.sha256(encoded).hexdigest(),
                                 results=results, total_instructions=core.steps))
    report = dict(schema_version=1, profile=args.profile, observations=observations,
                  music_app_acceptance_verified=False, ipod_acceptance_verified=False,
                  environment='isolated ARM64 Music core with Python callbacks',
                  callback_policy=dict(eligibility='ID exists in metadata', play_and_skip_history='all zero',
                                       current_time=0, random_seed=0, byte_order='little endian',
                                       allocator='bounded monotonic heap; frees validated but not reused'))
    with output.open('x') as file:
        json.dump(report, file, indent=2)
        file.write('\n')
    print(f'Emulated {len(results)} seeds across {len(observations)} datasets; profile={args.profile}')


if __name__ == '__main__':
    main()
