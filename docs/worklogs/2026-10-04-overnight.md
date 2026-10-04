# Overnight validation checkpoint

User scope: defer Apple Account login, continue useful development and native
validation until 2026-10-05 07:00 Asia/Tokyo (2026-10-04 22:00 UTC). Actual media,
authenticated YouTube Music observations, parallel agents and macOS Actions are
authorized. Preserve original HDD files. This file contains no credentials.

## Verified baseline

- Local baseline commit: `4c795ce`.
- Isolated public publisher: `/tmp/opengenius-macos-probe-publish`, branch
  `macos-probe`, baseline `21df525`. Never push the local main history.
- Actions run `37199265990` completed successfully: native full Library loads
  2,974 tracks, retains 214 Genius IDs and all 723 observed directed relations;
  47 media locations and three advancing native playback positions verified.
- Native Genius menu remains disabled without account setup. Native Genius
  generation and stock iPod acceptance remain unverified.
- Earlier completed runs: original control `37198779875`, fresh real import
  `37198400336`, native 47-track rebase `37198788035`.
- Temporary Actions transport secret was removed after the baseline jobs.

## Current work, 23:30 JST

- Root added `scripts/prepare_native_playlist_plan.py`: requires the source
  Library digest and refuses to truncate recommendations when audio is missing.
  Four tests pass, including foreign-source and incomplete-media refusal.
- Private `data/native-playlist-overlay-v01` contains the verified full native
  rebase plus three complete observed playlist plans (49 entries). All audio is
  already in the existing 47-track encrypted fixture; no extra media is needed.
- `native_fixture_probe` owns optional ordinary-playlist creation/reopen checks
  in the native harness and its tests. Root owns transport, Actions dispatch and
  independent evidence auditing. Ordinary playlists are not Genius generation.
- `review_ytmusic_batch` owns bounded paced authenticated coverage collection and
  aggregate quality analysis; no automatic retry after auth/rate-limit failure.
- `library_identity` owns source-backed iPod parser/research improvements.

## Resume procedure

1. Inspect `git status`, agent states and actual Actions run status. Do not start
   duplicate work based only on an old checkpoint or observation timeout.
2. Review completed agent changes and run targeted tests.
3. Seal the new private overlay with the existing local transport key. Copy only
   reviewed code, tests, workflows and ciphertext into the isolated publisher.
4. Re-register the existing transport key through stdin for dependent native
   jobs, dispatch the real-library workflow with the rebased overlay, and record
   its run ID here. Never print the key or transfer cookies/account preferences.
5. Download encrypted output, decrypt locally, audit before/after Genius tables
   and exact requested playlist ordering after Music restart. Delete the Actions
   secret once dependent jobs are terminal.
6. Update this checkpoint with evidence and next actions before interruptions.

No scheduled automatic session restart has been configured. A resumed session
can continue from this file; the user's mentioned 12:51 reset is not treated as
proof of a scheduler or of a running job.

## Updates, before dispatch

- Private three-playlist overlay sealed locally; same temporary Actions transport
  secret re-registered for the upcoming native job. Remove it after jobs finish.
- Independent ordinary-playlist audit compares requested names, playlist IDs,
  complete ordered members, ordinary flags and both clean exits; eight plan/audit
  tests pass.
- Bounded YouTube Music batch stopped at account confirmation with
  `AuthenticationUnconfirmed`; zero new radio/related observations. No retry.
  Cached-data evaluation and non-IMAS seed preparation continue offline.

## Live native job, 23:33 JST

- Actions run `37209669955` confirmed queued, public publisher commit
  `9e56aa6a407e2410ea074b266109350fd18ea445`. Uses the same encrypted audio
  fixture and the newly sealed recommendation-plan overlay.
- 14 native library/import tests and eight independent plan/audit tests pass.
- Poll this exact run before any redispatch; on terminal completion download its
  encrypted artifact into `data/macos-actions/run-37209669955`, decrypt locally,
  audit using `data/native-playlist-overlay-v01/manifest.json`, then remove the
  temporary Actions secret if no dependent native job is live.

- Latest authoritative poll: run `37209669955` is **in_progress**, native
  collector step executing; dependency installation and input assembly passed.
  Job ID `111458095050`. This is a live handle, not a checkpoint inference.
- iPod parser review found potential private-reference leakage on malformed
  later sections and insufficient trailing-byte validation; agent is fixing
  both before acceptance. Do not treat its current draft as finished.

## Verified results, 23:38 JST

- Run `37209669955` terminal success. Encrypted output downloaded and decrypted
  locally; independent audit saved under its private probe directory.
- All three ordinary playlists persist with the same playlist IDs, names,
  ordered 10/14/25 members and `genius=false` after a verified Music exit/reopen.
- Full 2,974-track PID and Genius-ID sets and all Genius SQL tables preserved;
  47 relinked locations and three advancing native playback positions verified.
- Genuine native Genius playlist count remains zero; generation and iPod
  acceptance remain unverified. Do not equate ordinary persistence with Genius.
- Actions transport secret deleted; `gh secret list` returned an empty list.
- iPod parser review fixes applied: internal ID sets removed before any early
  error output; trailing section bytes reported as incomplete validation.
  Ten targeted parser tests pass. Source-backed research note completed.
- Cached-only quality evaluation completed: 29 roots, 222 metadata candidates,
  737 directed edges. Minimum artist/album interval increases output diversity
  but also same-artist adjacency. Native tested dataset remains 214/723; these
  two observation collections must not be conflated.
- Fresh Nightly auth file prepared locally, but primary signing/session cookies
  are unchanged. Login validity unproven. Account-response diagnostic is being
  implemented offline to distinguish SDK shape errors; no further requests yet.
