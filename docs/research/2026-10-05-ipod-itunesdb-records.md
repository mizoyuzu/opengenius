# iPod iTunesDB track identity and playlist membership

2026-10-05. Read-only inspection of the upstream libgpod implementation. No iPod or FW 2.0.4 device database was available, so this records libgpod-supported iTunesDB structure, not firmware acceptance or Genius-specific behavior.

## Confirmed record fields in libgpod

In `get_mhit`, libgpod requires an `mhit` header of at least `0x9c` bytes. It reads the 32-bit iPod track ID at `mhit+0x10`, the child `mhod` count at `mhit+0x0c`, and the 64-bit `dbid` at `mhit+0x70`. For headers of at least `0xf4` bytes, it reads a second 64-bit value named `dbid2` at `mhit+0xa8`. The corresponding writer emits these same fields. The parser validates the child-record lengths without decoding any title, path, or other text.

These names do not establish an Apple Persistent ID mapping. The public `itdb.h` documentation describes `dbid` as a unique database ID used to identify a song across iPod databases, including joining an iTunesDB `mhit` to an ArtworkDB `mhii`. It explicitly says the purpose of `dbid2` is unclear; libgpod initializes it to `dbid` when adding a track, while values written by iTunes from database version `0x12` differ. Therefore neither field can be equated with a Genius ID from source evidence alone.

Source: [libgpod `get_mhit` and writer](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L2294-L2450), [track-field documentation](https://github.com/fadingred/libgpod/blob/master/src/itdb.h#L1360-L1442), [mhit writer](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L3739-L3817).

## Confirmed playlist membership layout

For the standard track list, `parse_tracks` reads an `mhlt` track count and walks that many `mhit` records. For playlist sections, `parse_playlists` reads the playlist count from `mhlp+8` and walks `mhyp` records. `get_playlist` requires an `mhyp` header of at least 48 bytes, reads its total record size at `+8`, child `mhod` count at `+0x0c`, and membership (`mhip`) count at `+0x10`. Each `mhip` requires at least a 36-byte header; its child count is at `+0x0c`, and its referenced 32-bit iPod track ID is at `+0x18`. The parser resolves that value against `mhit+0x10`, which is direct source evidence for the ordinary iTunesDB playlist graph.

libgpod documents type 2 as normal playlists and type 3 as the special Podcasts playlist section. Its reader also handles older `mhip` records whose total-size field equals the header size despite following child `mhod`s; it advances using the declared child lengths. The audit parser validates that fallback but does not interpret playlist names, smart-playlist conditions, podcast metadata, or Genius semantics.

Source: [track and playlist readers](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L1974-L2063), [`mhyp` reader and membership resolution](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L2066-L2123), [playlist-section reader](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L2799-L2857), [playlist writer and section types](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c#L5174-L5335).

## What this says about Genius

libgpod parses iTunesDB `mhsd` type 9 as an opaque Genius CUID whose expected size is 32 bytes. That is not a track-relation graph. Its classic `mhit` reader/writer exposes the fields listed above, but does not expose a Genius ID field; the separate SQLite schema's `item.genius_id` is not evidence for an iTunesDB offset. A search finding no `mhgd` handler in this implementation only bounds the claim to this codebase—it does not prove that Apple firmware has no other Genius representation.

So the current evidence supports auditing normal playlist membership and correlating records by `mhit+0x10`; it does not support writing a Genius graph or treating `dbid2` as a Genius or persistent track identifier. That requires paired observations from a real device: a stock-sync database before and after enabling/syncing Genius, then an isolated change to one known seed and its result. The iPod model and exact database profile must be recorded before any writer work.

## Parser changes and limits

`scripts/inspect_ipod_genius.py` now validates the supported nested records in mhsd types 1, 2, and 3. It reports only counts: track-ID uniqueness, nonzero/unique/duplicate `dbid` and `dbid2` statistics, playlist membership counts, and dangling membership references. It never returns identifier values or MHOD text. `nested_records_validated` is false when a recognized section uses an unrecognized payload layout; checksum and on-device acceptance remain unverified.

Tests cover valid record traversal, unique and duplicated values, a resolved and dangling playlist reference, legacy `mhip` child traversal, malformed `mhit`/`mhip` sizes, unrecognized nested payload, trailing bytes, and output redaction even if a later section is malformed. These tests verify this parser against source-described shapes; they do not establish that a stock FW 2.0.4 device accepts a generated database.
