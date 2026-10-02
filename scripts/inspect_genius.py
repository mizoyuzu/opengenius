"""Inspect observed Genius BLOBs and cross-check IDs against a local library."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from decrypt_genius import validate_database
from genius_format import CONFIG_FILTER_NAMES, parse_config, parse_metadata, parse_similarities, unsigned_id
from inspect_music_library import decode_musicdb, parse_tracks


def inspect(bundle, database, include_titles=False):
    clear = database.read_bytes()
    counts = validate_database(clear)
    tracks, _ = parse_tracks(decode_musicdb((bundle / "Library.musicdb").read_bytes()))
    by_pid = {int(t["persistent_id"], 16): t for t in tracks}
    by_genius = {}
    unknown_profiles = 0
    for track in tracks:
        if "genius_id" not in track:
            unknown_profiles += 1
            continue
        gid = int(track["genius_id"], 16)
        if gid:
            by_genius.setdefault(gid, []).append(track)
    rows = []
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(clear)
        metadata = {}
        for gid, version, blob in connection.execute("SELECT genius_id,version,data FROM genius_metadata"):
            if version != 1:
                raise ValueError("Unsupported metadata version")
            metadata[unsigned_id(gid)] = parse_metadata(blob)
        for gid, version, blob in connection.execute("SELECT genius_id,version,data FROM genius_similarities ORDER BY genius_id"):
            if version != 1:
                raise ValueError("Unsupported similarity version")
            seed = unsigned_id(gid)
            word, ids = parse_similarities(blob)
            local = by_genius.get(seed, [])
            row = dict(genius_id=f"{seed:016X}", local_persistent_ids=[t["persistent_id"] for t in local],
                       mapping_status="unique" if len(local) == 1 else "missing" if not local else "ambiguous",
                       metadata_words=metadata.get(seed), metadata_word_meanings="unknown",
                       similarity_header_word=word,
                       ordered_similarities=[dict(position=i, genius_id=f"{value:016X}", is_seed=value == seed,
                           local_persistent_ids=[t["persistent_id"] for t in by_genius.get(value, [])])
                           for i, value in enumerate(ids)])
            if include_titles:
                row["local_titles"] = [t.get("title") for t in local]
            rows.append(row)
        fingerprints = {unsigned_id(r[0]) for r in connection.execute("SELECT item_id FROM genius_fingerprint")}
        configs = []
        for i, v, default, minimum, data in connection.execute("SELECT * FROM genius_config ORDER BY id"):
            config = dict(id=i, version=v, default_num_results=default, min_num_results=minimum, data_bytes=len(data))
            if v == 2:
                parsed = parse_config(data)
                filters = []
                for item in parsed["filters"]:
                    if item["type"] != 2:
                        filters.append(dict(item, role=CONFIG_FILTER_NAMES[item["type"]]))
                        continue
                    entries = item["records"]
                    graph = {key: set(values) for key, values in entries}
                    filters.append(dict(type=2, role="compatible_genre", metadata_index=item["metadata_index"],
                                        records=len(entries), total_list_entries=sum(len(values) for _, values in entries),
                                        keys_with_self=sum(key in values for key, values in entries),
                                        unknown_target_ids=len({x for _, values in entries for x in values} - set(graph)),
                                        asymmetric_entries=sum(key not in graph.get(x, set()) for key, values in entries for x in values)))
                config.update(filters=filters, flags=parsed["flags"], result_words=parsed["result_words"],
                              parameter_meanings="partially_known", ipod_profile_verified=False)
            configs.append(config)
        return dict(schema_version=1, database_sha256=hashlib.sha256(clear).hexdigest(), table_counts=counts,
                    library_tracks=len(tracks), unsupported_track_header_profiles=unknown_profiles,
                    tracks_with_stored_genius_id=sum(len(v) for v in by_genius.values()),
                    unique_genius_ids=len(by_genius), metadata_ids_missing_from_library=len(set(metadata) - set(by_genius)),
                    library_genius_ids_missing_metadata=len(set(by_genius) - set(metadata)),
                    fingerprint_ids_matched_to_library=len(fingerprints & set(by_pid)),
                    fingerprint_ids_missing_from_library=len(fingerprints - set(by_pid)),
                    config=configs, records=rows, music_app_acceptance_verified=False, ipod_acceptance_verified=False)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-titles", action="store_true")
    args = parser.parse_args()
    if args.output and (args.output.resolve().is_relative_to(args.bundle.resolve()) or args.output.exists()
                        or args.output.resolve() == args.database.resolve()):
        parser.error("Output exists or would overwrite a source file")
    report = inspect(args.bundle, args.database, args.include_titles)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as file:
            file.write(encoded)
        print(f"Mapped {report['tracks_with_stored_genius_id']} stored Genius IDs; "
              f"{report['fingerprint_ids_matched_to_library']} fingerprints matched")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
