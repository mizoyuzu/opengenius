# 増分更新へのiPod Genius sync revision反映

2026-10-09。実機同期の1曲検証では、Genius IDだけではExtras.itdbのGenius行が転送されず、
Libraryの`itma+0x68`を変更すると1曲が転送され、端末`mhit+0x1f0`へ値が保存された。
Music.appとiPodで動作を確認済みという利用者報告は別途受け取っている。
この記録はその報告を今回の生成コピーの受け入れ試験と混同しない。

## 更新処理

`update_genius_bundle.py`は新しいGenius IDを付ける曲だけでなく、既存ID曲も含めて、
stateにある全曲のmetadata/similarities行BLOBから安定した非ゼロCRC32 revisionを計算し、
`itma+0x68`へ保存する。アルゴリズムは`opengenius-crc32-v1`であり、Apple独自checksumの
再現を主張しない。関係行の内容が変わればrevisionも更新され、同じ入力で再実行したときは
既存値と一致してLibraryを変更しない。対象外曲のrevision、既存GID、トラックメタデータは保持する。

`patch_expanded_library()`は観測済み376-byte `itma` header profileだけを受け付ける。
許可する変更は`+0x68`のrevisionと`+0xcc`のGenius IDに限り、出力から両欄を戻した全展開Libraryが
入力とbyte-for-byte一致することを確認する。

独立監査器は出力Libraryから`itma+0x68`を読み、復号したGenius SQLのmetadata/similarities BLOBから
期待revisionを再計算する。既定では過去のrevision未設定コピーも監査でき、不一致数を記録する。
`--require-sync-revisions`は対象曲すべてが非ゼロかつ一致することを要求する。
さらに、baselineと出力の展開Libraryを比較し、対象曲のGenius ID/revision欄だけを戻すと
全バイトが一致することを検証する。対象外曲のrevision差や他の不透明領域の差も拒否する。

## 2026-10-09の独立コピー

入力は`data/half-coverage-final-bundle-20261009-v03/`のMusic Libraryとstate。
zero-ID source snapshot/identity mapと`data/half-coverage-catalog-graphs-20261009-v03/accepted-evaluation.json`
を使い、同一stateを再入力するno-op計画でコピーを作成した。入力bundle、state、snapshot、identity map、
evaluationは変更していない。既存SQLを保持し、関係変更0行、新規ID 0件のまま2,377対象曲へrevisionを保存した。
Library内の既存GID、曲メタデータ、対象外曲のrevision、Library以外の補助ファイルは保持した。

初回v01はno-op SQLの再serializeでGenius.itdbのファイルbytesまで変わったため、最終成果物に採用しなかった。
更新器を修正し、SQLの全rowが入力と同じ場合は復号SQLとGenius.itdbの元bytesを保持する。
最終v02ではstate hashとSQLテーブル内容を保持し、bundle内で変わったファイルはLibrary.musicdbだけ。

出力は[`data/half-coverage-ipod-sync-bundle-20261009-v02/`](../../data/half-coverage-ipod-sync-bundle-20261009-v02/)。
転送用ZIPは[`Music Library.opengenius-2377.zip`](../../data/half-coverage-ipod-sync-bundle-20261009-v02/Music%20Library.opengenius-2377.zip)、
更新レポートは[`report.json`](../../data/half-coverage-ipod-sync-bundle-20261009-v02/report.json)、
独立strict監査は[`independent-audit.json`](../../data/half-coverage-ipod-sync-bundle-20261009-v02-independent-audit.json)。
これらの個人ライブラリデータはGit追跡対象外。

strict監査で対象2,377曲中2,377曲のrevision一致、非ゼロ2,377件、不一致0件を確認した。
Libraryの許可外バイト差も0で、SQLite integrityは`ok`。計画レポートは2,377既存曲、
関係63,879本、新規ID 0件、relation row更新0件。Music.appは起動せず、外部通信も行っていない。
この出力コピーのMusic.app/iPod受け入れと、端末自身のGenius生成は未確認。

実行したテストは`.venv/bin/python -m unittest tests.test_genius_sync_revision tests.test_update_genius_bundle tests.test_audit_index_expansion tests.test_genius_format tests.test_music_id_writer tests.test_ipod_genius_sync_audit tests.test_ipod_genius tests.test_genius_emulation tests.test_plan_genius_update`。
55件を実行し51件成功、4件skip。relation-only変更で新規IDなしでもrevisionが更新されること、
同一stateの再実行でSQL bytesが維持されること、未対象曲のrevision保持、revision不一致のstrict拒否、
許可外Library差分の拒否を含む。
