"""Bounded multi-seed Genius core experiment using the supplied Music binary.

The entry point is reached by Music's Genius cluster wrapper; its specific
linkage to Mix playback is still under investigation. Mix serialization,
UI activation, automatic queue refill, and iPod acceptance are not emulated.
"""
import argparse
import json
from pathlib import Path

from emulate_genius import MusicCore
from genius_format import pack_config, pack_similarities

MULTISEED_CONSTRUCTOR = 0x101872384
NEXT_TRACK = 0x100365718
DESTROY_CLUSTER = 0x100365D94
MATCHES_DISTINGUISHED_KIND = 0x100AD5CE4
GENIUS_MIX_KIND = 32


def probe_native_mix_kind(core):
    """Test the in-memory predicate, not an LPMA or iPod serialization value."""
    playlist = core.alloc(0x40)
    core.write(playlist, 'I', 0x706C7374)  # Native playlist object's 'plst' magic.
    observations = []
    for kind in (0, 26, GENIUS_MIX_KIND, 33):
        core.write(playlist + 0x10, 'H', kind)
        matched = core.run(MATCHES_DISTINGUISHED_KIND, playlist, GENIUS_MIX_KIND)
        if matched not in (0, 1):
            raise ValueError('Unexpected native playlist-kind predicate result')
        observations.append(dict(in_memory_kind=kind, matches_genius_mix=bool(matched)))
    return dict(predicate_entry=hex(MATCHES_DISTINGUISHED_KIND),
                object_kind_offset=0x10, native_genius_mix_kind=GENIUS_MIX_KIND,
                observations=observations, storage_kind_mapping_verified=False)


def bounded_integer(value, maximum, label):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{label} must be an integer in 1..{maximum}')


class MixCore(MusicCore):
    def open_mix(self, candidates, candidate_cap=100):
        """Keep one native context alive across bounded successive batches."""
        if getattr(self, '_active_mix_session', None) is not None:
            raise ValueError('A mix context is already open on this core')
        candidates = list(candidates)
        bounded_integer(len(candidates), 4096, 'candidate count')
        bounded_integer(candidate_cap, 4096, 'candidate cap')
        if any(type(value) is not int or not 0 < value < 1 << 64 for value in candidates):
            raise ValueError('Candidates must be nonzero uint64 Genius IDs')
        if len(set(candidates)) != len(candidates):
            raise ValueError('Duplicate candidate IDs')
        if self.library is None:
            self.library = self.run(0x100364D04, self.callbacks, 0, 0)
            if not self.library:
                raise ValueError('Music core rejected configuration')
        self.random.seed(self.random_seed)
        start_steps, start_calls = self.steps, self.calls.copy()
        pointer = self.alloc(len(candidates) * 8)
        self.write(pointer, f'{len(candidates)}Q', *candidates)
        cluster = self.run(MULTISEED_CONSTRUCTOR, self.library, pointer,
                           len(candidates), candidate_cap)
        if not cluster:
            raise ValueError('Music core rejected candidate context')
        session = MixSession(self, cluster, candidates, candidate_cap,
                             start_steps, start_calls)
        self._active_mix_session = session
        return session

    def generate(self, seed, limit):
        if getattr(self, '_active_mix_session', None) is not None:
            raise ValueError('Close the mix context before generating a new playlist')
        return super().generate(seed, limit)

    def generate_mix(self, candidates, limit=25, candidate_cap=100):
        bounded_integer(limit, 1000, 'result limit')
        with self.open_mix(candidates, candidate_cap) as session:
            session.next_batch(limit)
        return session.report(limit)


class MixSession:
    """Own a native context; preserve its RNG/filter state until close()."""
    MAX_NEXT_CALLS = 1000

    def __init__(self, core, cluster, candidates, candidate_cap, start_steps, start_calls):
        self.core = core
        self.cluster = cluster
        self.candidates = candidates
        self.candidate_cap = candidate_cap
        self.start_steps = start_steps
        self.start_calls = start_calls
        self.results = []
        self.exhausted = False
        self.closed = False
        self.next_calls = 0
        self.batch_count = 0
        self.final_steps = None
        self.final_calls = None

    def __enter__(self):
        if self.closed:
            raise ValueError('Mix context is closed')
        return self

    def __exit__(self, *unused):
        self.close()

    def next_batch(self, limit=25):
        if self.closed:
            raise ValueError('Mix context is closed')
        bounded_integer(limit, self.MAX_NEXT_CALLS, 'batch limit')
        if self.exhausted:
            return []
        if self.next_calls + limit > self.MAX_NEXT_CALLS:
            raise ValueError('Mix session next-call budget exceeded')
        batch = []
        try:
            for _ in range(limit):
                self.next_calls += 1
                result = self.core.run(NEXT_TRACK, self.cluster)
                if not result:
                    self.exhausted = True
                    break
                if result not in self.core.metadata:
                    raise ValueError('Core returned an unknown Genius ID')
                self.results.append(result)
                batch.append(f'{result:016X}')
        except Exception:
            self.close()
            raise
        self.batch_count += 1
        return batch

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.core.run(DESTROY_CLUSTER, self.cluster)
        finally:
            self.final_steps = self.core.steps
            self.final_calls = self.core.calls.copy()
            self.core._active_mix_session = None

    def report(self, requested_total):
        output = self.results
        steps = self.final_steps if self.closed else self.core.steps
        calls = self.final_calls if self.closed else self.core.calls
        return dict(candidate_genius_ids=[f'{value:016X}' for value in self.candidates],
                    candidate_cap=self.candidate_cap, random_seed=self.core.random_seed,
                    result_genius_ids=[f'{value:016X}' for value in output],
                    exhausted=self.exhausted, stopped_at_limit=len(output) == requested_total,
                    duplicate_result_count=len(output) - len(set(output)),
                    results_outside_input_count=sum(value not in self.candidates for value in output),
                    instructions=steps - self.start_steps,
                    host_calls=dict(calls - self.start_calls),
                    batch_count=self.batch_count, next_calls=self.next_calls,
                    context_closed=self.closed,
                    entry_point=hex(MULTISEED_CONSTRUCTOR),
                    native_mix_playback_verified=False,
                    automatic_refill_tested=False)


def synthetic_probe(executable):
    """Two disjoint pools in 128 synthetic tracks; deliberately isolated filters."""
    config = pack_config(dict(version=2,
                              filters=[dict(type=1, parameters=[20, 50, 10, 10])],
                              flags=0, result_words=[10, 20]))
    pools = [list(range(0x70000001, 0x70000041)),
             list(range(0x70000041, 0x70000081))]
    metadata = {identifier: bytes(32) for pool in pools for identifier in pool}
    relations = {identifier: pack_similarities(0, pool)
                 for pool in pools for identifier in pool}
    results = []
    kind_probe = None
    batch_result = None
    for pool in pools:
        for seed in (0, 1):
            core = MixCore(executable, config, metadata, relations, random_seed=seed)
            result = core.generate_mix(pool, limit=80)
            selected = {int(value, 16) for value in result['result_genius_ids']}
            if not selected <= set(pool):
                raise ValueError('Synthetic multi-seed pool isolation failed')
            results.append(result)
            if kind_probe is None:
                kind_probe = probe_native_mix_kind(core)
                with core.open_mix(pool) as session:
                    batched = session.next_batch(25) + session.next_batch(25) + session.next_batch(30)
                if batched != result['result_genius_ids']:
                    raise ValueError('Preserved context batches differ from a single request')
                batch_result = session.report(80)
    return dict(schema_version=1, fixture='synthetic_128_two_disjoint_pools',
                profile='relations_only', results=results,
                preserved_context_batches=batch_result, native_kind_probe=kind_probe,
                mix_storage_format_verified=False, ipod_acceptance_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = synthetic_probe(args.executable)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'experiments': len(result['results']),
                      'result_counts': [len(row['result_genius_ids']) for row in result['results']],
                      'native_mix_playback_verified': False}))


if __name__ == '__main__':
    main()
