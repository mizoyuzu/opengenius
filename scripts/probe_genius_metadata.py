"""Test synthetic metadata ID groups against the supplied Music selection core.

All fixtures exist only in emulator memory. No library or DB is rewritten.
This tests equality and spacing behavior, not real-world metadata identities.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

from decrypt_genius import derive_key, decrypt_pages, extract_key_header
from emulate_genius import MusicCore, database_rows
from genius_format import parse_config, parse_metadata, pack_similarities


def run_probe(executable, config, genre_id):
    parsed = parse_config(config)
    compatible = [f for f in parsed['filters'] if f['type'] == 2 and f['metadata_index'] == 0]
    if not compatible or not any(key == genre_id for f in compatible for key, _ in f['records']):
        raise ValueError('Synthetic genre ID is not in the observed index-0 compatibility map')
    core = MusicCore(executable, config, {}, {})
    observations = []
    cases = [('six_unique', 6, None), ('six_shared_index1', 6, 1),
             ('six_shared_index2', 6, 2), ('six_shared_index3', 6, 3),
             ('thirty_unique', 30, None)]
    for label, count, shared in cases:
        identifiers = list(range(0x70000001, 0x70000001 + count))
        metadata = {}
        for position, identifier in enumerate(identifiers):
            words = [genre_id, 10001 + position, 20001 + position, 30001 + position]
            if shared is not None:
                words[shared] -= position
            metadata[identifier] = struct.pack('<4Q', *words)
        core.metadata = metadata
        core.similarities = {identifier: pack_similarities(0, identifiers) for identifier in identifiers}
        result = core.generate(identifiers[0], 100)
        result.update(case=label, available_tracks=count, shared_metadata_index=shared)
        observations.append(result)
        print(f'{label}: {len(result["result_genius_ids"])} selected', flush=True)
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(args.bundle.resolve()) or output.is_relative_to(args.executable.resolve().parent):
        parser.error('Output exists or is inside a source directory')
    key, _ = derive_key(args.executable, extract_key_header((args.bundle / 'Library Preferences.musicdb').read_bytes()))
    encrypted = (args.bundle / 'Genius.itdb').read_bytes()
    config, metadata, _ = database_rows(decrypt_pages(encrypted, key))
    if not metadata:
        parser.error('Source has no observed metadata IDs')
    genre_id = Counter(parse_metadata(blob)[0] for blob in metadata.values()).most_common(1)[0][0]
    results = run_probe(args.executable, config, genre_id)
    report = dict(schema_version=1, source_genius_sha256=hashlib.sha256(encrypted).hexdigest(),
                  synthetic_metadata=True, synthetic_genre_id=f'{genre_id:016X}',
                  other_metadata_indices='synthetic equality groups; attribute names unknown',
                  config_policy='all original filters preserved', results=results,
                  callback_policy=dict(history=0, time=0, random_seed=0, eligibility='fixture metadata exists'),
                  music_app_acceptance_verified=False, ipod_acceptance_verified=False)
    with output.open('x') as file:
        json.dump(report, file, indent=2)
        file.write('\n')


if __name__ == '__main__':
    main()
