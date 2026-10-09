# Genuine YT Music outgoing-root expansion

`scripts/expand_genuine_roots.py` grows the accepted directed recommendation graph from real YT Music observations. Its target counts Library PIDs with accepted outgoing `radio` or `related` edges. It does not count Genius-indexed tracks, incoming-only targets, search hits, album-catalog candidates, or artist-name aliases as outgoing roots.

The initial source is the 3,004-track rebased Library with 177 existing outgoing roots. Its accepted graph has 403 incoming-only PIDs that can be tried as roots. Current strict saved-video evidence has 69 PID-to-video hints, 17 of which already have outgoing edges; the other 52 are useful starting hints. These catalog hints are unverified metadata candidates. They can start a genuine radio/related collection, but the catalog itself contributes no recommendation edge. A root counts only after the collected radio and related observations pass the existing identity and provenance checks and the merge accepts its outgoing targets.

## Start a run

The paths below use the current rebased snapshot and v02 accepted graph. Replace the auth path with the local YT Music credential file. Keep all run artifacts under a new private `data/` directory; collector summaries and snapshots contain personal-library evidence.

```sh
.venv/bin/python scripts/expand_genuine_roots.py \
  --track-snapshot data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json \
  --identity-map data/new-library-rebased-20261009-v03/zero-id-source/identity-map.json \
  --baseline-state data/new-library-rebased-20261009-v03/state.json \
  --baseline-graph-export data/new-library-rebased-20261009-v03/accepted-baseline-graphs.json \
  --rebase-report data/new-library-rebased-20261009-v03/report.json \
  --previous-growth data/genuine-radio-graphs-20261009-v02 \
  --seed-video-evidence data/ytmusic/strict-catalog-seed-video-evidence-20261009-v02.json \
  --target-outgoing-roots 300 \
  --global-request-budget 450 \
  --max-attempts 300 \
  --batch-size 20 \
  --batch-request-budget 90 \
  --candidate-limit 100 \
  --interval 3 \
  --auth /path/to/browser.json \
  --output data/ytmusic/genuine-root-expansion-20261009-v01
```

The runner pauses at least three seconds between HTTP request starts. It caps each batch at 90 requests and the whole run at the supplied global budget. The candidate limit sets the radio observation window; the service may paginate, return fewer rows, or produce no accepted local matches. The runner does not retry unresolved searches in the same run, and it does not turn failed searches into placeholders or inferred edges.

The optional `--seed-video-evidence` file must match the exact Library and identity-map hashes. It helps the collector skip a seed search when strict saved evidence supplies a current video ID. Whole-credit aliases can improve metadata matching after explicit review, but alias agreement alone is not recording identity and does not create an outgoing root or recommendation edge.

## Plan and resume

Use `--plan-only` to inspect candidate selection without network access. It reports planned PIDs but does not mark them attempted or consume the request budget. You can use the same output directory for a later live run; the saved progress still binds the same input paths and limits.

For a live run, rerun the same command with the same output directory to continue from its hash-checked progress and batch receipts. Keep every input path, limit, and option unchanged during a resume. Before collection resumes, the runner replays the prior typed growth chain against the bound Library, identity map, baseline state, and graph export. It verifies collector results against the ordered seed plan, counts only returned result PIDs and an explicitly recorded partial seed as attempted, and recomputes the active-root metric from the accepted graph.

Each new batch writes a durable intent before collection. If the process stops after writing that intent, resume validates its config, source files, plan, manifest, collector summary, snapshots, and any typed merge. A completed batch is replayed and promoted without repeating HTTP calls; an incomplete, ambiguous, unauthenticated, or changed batch fails closed and preserves its artifacts for inspection. An older unreceipted batch without an intent cannot be automatically recovered. A request-budget stop records only result PIDs (and any explicitly recorded partial seed) as attempted, so planned tail PIDs remain eligible for a later batch.

## Read the result

`progress.json` separates attempted seeds, HTTP requests, accepted outgoing roots, and unresolved or rejected PIDs. The accepted root count is derived from the merged graph's nonempty outgoing rows; it is not the indexed-track total. Each `batch-*/growth/accepted-graphs.json` preserves source receipts for the collector summary, seed manifest, and snapshots. The final `growth_output` points to the latest receipt-checked graph checkpoint.

The collector may stop at an authenticated request-budget boundary. That stop is eligible for merging only when the summary records an exact positive request count equal to its batch budget, a `RuntimeError` at the request boundary, and no HTTP error status. Other authentication, network, or HTTP failures stop the run without accepting that batch as complete growth.

## Audited run checkpoint

The completed run used 450 HTTP requests across five automatic batches. Independent replay in `data/ytmusic/automatic-genuine-roots-20261009-v01/actual-audit-final.json` verified 177 → 249 active outgoing roots (+72), 649 indexed tracks, and 6,576 directed recommendation edges. Per-batch root increases were 14, 17, 13, 15, and 13. All new roots and accepted targets have hash-verified authenticated recommendation evidence; every merge reports zero catalog, local-album, and unattributed edges. The run stopped at its request budget before reaching its 300-root target.

The resulting bundle copy is `data/genuine-radio-bundle-20261009-v03/Music Library.musiclibrary`. Its database contains 2,980 more genuine recommendation edges than the starting bundle. Artist/album affinity remains a separate application ordering policy. No Music.app GUI or iPod validation was performed in this run.

## Reviewed whole-credit aliases and state rebinding

Eight Japanese character-plus-CV credits now have reviewed English whole-credit aliases. For example, the official [Mano Sakuragi profile](https://shinycolors.idolmaster-official.jp/idol/illuminationstars/mano/) pairs MANO SAKURAGI with CV 関根 瞳, and the [I'm Enterprise profile](https://www.imenterprise.jp/profile.php?id=43) gives the actor's Latin display as SEKINE HITOMI. The official [Kiriko Yukoku profile](https://shinycolors.idolmaster-official.jp/idol/lantica/kiriko/) and [VIMS profile](https://www.vims.co.jp/talent/375/) similarly support the complete KIRIKO YUKOKU / CV Mizuki Yuina credit. The review binds all eight decisions to the saved proposal file, its source report, the original map revision, the local PID and credit, and the cited official sources.

These aliases resolve a complete remote credit string to an existing local credit for candidate matching. They leave every local artist field and canonical equivalence label unchanged. They establish credit-name equivalence only; they do not verify recordings or add recommendation edges. Existing growth runs are replayed against the original map before the state is rebound. The typed receipt records both map hashes and verifies unchanged PID/GID assignments, metadata bytes, and relation bytes. New observations can then use the reviewed map while the old runs remain bound to the original map.

To rebind the latest accepted growth checkpoint, pass its `growth/` directory to `--growth-dir`:

```sh
.venv/bin/python scripts/rebind_radio_growth_identity.py \
  --track-snapshot data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json \
  --old-identity-map data/new-library-rebased-20261009-v03/zero-id-source/identity-map.json \
  --new-identity-map data/new-library-rebased-20261009-v03/zero-id-source/identity-map-aliases-v01.json \
  --alias-application-receipt data/new-library-rebased-20261009-v03/zero-id-source/identity-map-aliases-v01-receipt.json \
  --review-decisions data/artist-credit-alias-review-20261009-v01.json \
  --proposals data/artist-credit-alias-proposals-20261009-v01.json \
  --baseline-state data/new-library-rebased-20261009-v03/state.json \
  --baseline-graph-export data/new-library-rebased-20261009-v03/accepted-baseline-graphs.json \
  --rebase-report data/new-library-rebased-20261009-v03/report.json \
  --growth-dir data/ytmusic/automatic-genuine-roots-20261009-v01/batch-0005/growth \
  --output data/ytmusic/automatic-genuine-roots-20261009-v01/alias-rebound
```

The rebound directory's `state.json`, `accepted-baseline-graphs.json`, and `identity-map-rebinding-report.json` form a typed baseline accepted by `merge_rebased_radio_growth.py`. Use the new identity map for later snapshots and seed manifests. Keep the old growth directory intact: its receipts are replayed with the original map during rebinding, and no historical raw snapshot is retagged with the new map.
