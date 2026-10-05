# Paired iPod snapshot comparison workflow

Operational appendix for [iPod iTunesDB track identity and playlist membership](2026-10-05-ipod-itunesdb-records.md). This prepares a controlled, read-only before/after comparison for when the stock device arrives. It does not describe a Genius database writer or prove Genius acceptance.

## Capture plan

First record the model and firmware from the device's About screen and its `SysInfoExtended` file. The user's FW 2.0.4 target is known, but the model remains unknown; do not select a model-specific interpretation until this is recorded. Keep a byte-for-byte snapshot of the device's known iTunes database files for each state in separate locations, without editing the device database.

Capture one state after an ordinary stock sync with host Genius disabled. Then sync the same device/library from the locally enabled host, keeping the song selection and other sync settings fixed; capture the second state. The [Music 1.5.6 local-state experiment](2026-10-05-native-genius-state-persistence.md) verified native host generation after aligning both database flags. It did not verify how that host transforms the graph for iPod. This comparison does not require clicking the official Turn On Genius or Update Genius commands, signing in again, or sending the library to Apple. Record the host version, local state, and sync event explicitly; a host whose Genius menu is enabled is not proof of device support. If later testing on-device Start Genius or refreshing a playlist, preserve that as a separate third event and snapshot rather than mixing it into this pair. Store reports outside the source snapshot directories.

After the second sync, safely eject the device and test one known seed without a host connection. Record whether Start Genius appears, whether generation succeeds, and the ordered result. Keep the transferred ordinary/Genius playlist test separate from on-device generation: seeing an existing playlist does not show that the device used the relationship graph. For a small controlled test, use the existing 128-track fixture with at least 32 known candidates per seed, rather than the two-track fixture. A failed menu or seed test is an observation; record the exact state before changing database files. No iPod database writer is currently justified by the available evidence.

Run the existing read-only inventory once per snapshot, then compare only those reports:

```sh
python3 scripts/inspect_ipod_genius.py /path/to/before-snapshot --output /tmp/ipod-before-report.json
python3 scripts/inspect_ipod_genius.py /path/to/after-snapshot --output /tmp/ipod-after-report.json
python3 scripts/compare_ipod_genius_snapshots.py /tmp/ipod-before-report.json /tmp/ipod-after-report.json --output /tmp/ipod-delta.json
```

The inventory and comparison outputs use exclusive-create behavior; choose new report paths for each run. The comparator accepts only version-1 inventory reports and caps each report input at 2 MiB. It ignores unexpected filenames and fields, emits only allowlisted names and numeric aggregates, and reports hash equality as a boolean rather than exposing either digest.

## Reading the comparison

`same_db_profile` compares structural signatures for supported inputs: iTunesDB header/database versions and the ordered mhsd type/header-length sequence; readable SQLite user/schema versions, table counts, and known-table presence. `drift` means an observed signature changed. Added or missing known files are listed separately and make the comparison partial; they do not alone mean a supported profile changed. Either result is a reason to inspect the pair, not evidence that Genius is enabled.

The report separately shows changed file hashes and sizes, iTunesDB track and playlist counts, type-9 CUID presence/length aggregates, and known SQLite table row counts. Track/playlist aggregates are compared only when both iTunesDB reports say their supported nested records were fully validated. Similarity-envelope metrics are compared only when both reports contain the complete observed Music-v1 uncompressed envelope; the output labels these observations as Music-v1-only, not as a verified iPod profile.

Encrypted or otherwise unsupported files can still show a changed hash, but their schema and graph metrics remain `not_comparable`. Missing/added known files and unknown file-entry counts make the comparison partial. A row-count or hash change never establishes recommendation validity, a generated Genius playlist, or device acceptance; even identical profiles can hide unsupported data. Use the stock application/device event and, later, an isolated seed-generation test to establish those behaviors.

For readable SQLite using the observed Music-v1 envelope, the inventory additionally reports `relations.anomalies`: duplicated metadata/seed IDs, zero metadata/seed/target IDs, self edges, and repeated targets within each row. These are numeric diagnostics only. Their permissibility in Apple's format is unverified, so they do not change envelope completeness or claim an invalid database. Unknown BLOB fields, compressed formats, and firmware semantics remain uninterpreted. The comparator emits these as a separate `sqlite_relation_anomalies` block only for comparable supported inputs; older version-1 reports retain their existing relation comparison and show each unavailable diagnostic as `not_comparable`.

This workflow is grounded in the local inventory contract and the upstream libgpod structures summarized in the linked record note. There is no device snapshot yet, so the model-specific Genius file locations and semantics remain open.

## Offline diagnostic verification

The 23 inventory/comparison tests pass, including duplicated IDs, a zero ID, self edges, repeated targets, signed SQLite IDs, redaction, malformed diagnostic fields, and compatibility with older version-1 reports. A read-only reinspection of the saved native Music snapshot found 286 metadata/seed rows and 885 edges, with no dangling references or any of the new anomalies. An older 2980-row experimental dataset contains 61 edges and three self edges; these remain reported observations, rather than being rejected. Neither result demonstrates an iPod envelope or device acceptance. The native encrypted database was decrypted in memory using its existing local Preferences header; no keys, ID values, or source databases were exported.
