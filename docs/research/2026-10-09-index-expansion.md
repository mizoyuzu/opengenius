# Genius indexの拡張

曲の作品コンテキスト収集に先立ち、Genius DBの登録範囲と出方向の関連性を広げる。

## 起点とindexを区別する

2026-10-05の増分DBはLibrary 2,974曲に対して292 PIDのmetadata/similarities行を持ち、Genius IDが割り当てられている。そのうち53曲が観測起点、239曲は関連先としてのみ登録されていた。起点と関連先の和集合がindexの登録曲数であり、観測数や推薦出力数ではない。

最初の追加バッチは、保存済みの10曲の計画を使う。各曲は別の歌手表記・アルバムで、短いプレイリストの起点から到達できる登録済みの関連先を優先する。5秒間隔、最大45 HTTPリクエスト、認証確認後に収集する。今回は36リクエストで完了し、8曲で観測を取得、2曲は検索未解決として保留した。1曲はrelatedの解析に失敗したが、radio観測は残っている。

同名の複数ローカルPIDに対応する保存済み検索候補を、登録曲数を増やすためだけに全件追加しない。検索候補は推薦の辺ではない。今回の保存データ監査では39件の未登録候補のうち38件が選好PIDに選ばれておらず、同名・別版の曖昧さを残していた。

## 継続用の計画と監査

`plan_index_expansion.py` はLibrary snapshot・synthetic state・全関係行が一致するときだけ次の1〜20起点を選ぶ。登録済みのincoming-only曲を優先し、その後に未登録曲を選ぶ。歌手とアルバムの元表記で分散し、Off Vocal・カラオケ・Instrumental表記は既定で除外する。既存の作品推定やジャンル分類を推薦関係に変換しない。

`--exclude-pid` は検索未解決などの試行済み曲を次のバッチから外すために使える。計画と理由・入力hashの監査を別ファイルへ保存し、既存ファイルを上書きしない。

```sh
.venv/bin/python scripts/plan_index_expansion.py \
  --track-snapshot /path/to/tracks.json \
  --state /path/to/state.json \
  --relations /path/to/bound-relations.json \
  --count 20 --output /path/to/seed-plan.json \
  --audit-output /path/to/seed-plan-audit.json
```

relations envelopeは `schema_version:1`, `source_library_sha256`, `source_state_sha256`, `relation_rows` を持つ。全辺の順序はstateの保存BLOBと照合する。stateの自己hashは破損検出に使うが、それだけで実DBの一致を保証したとは扱わない。

`audit_index_expansion.py` は書き出した実DBを復号し、元と出力のSQL行・state・Library snapshotを照合する。旧PID/GIDとmetadata、設定、schema、補助テーブル、Preferencesと他のbundleファイルの保持を確認する。指定した最大5曲は保存後のSQLをMusic選曲コアへ渡し、出力PIDが保存された関係グラフで到達可能か確認できる。

既存の受理済み関係は保持する。既存の生観測のhashとenvelopeを検証し、保存された受理済みグラフがbaseline関係行と一致することを確かめる。過去の観測を新しい版判定ルールで再マッチした結果へ一括置換しない。追加の生観測だけを現在のマッチャーで検証し、全関係行を渡して `update_genius_bundle.py --baseline-state` で別コピーを作る。

Music.appとiPodの検証はユーザーが別環境で実施済みと報告している。今回の新しい出力コピーについて、その実機結果を引き継いだ受け入れ済みフラグは付けない。原本の置換やMacへの接続は行わない。

## 実データの結果

|項目|更新前|更新後|
|---|---:|---:|
|登録曲（PID/GID割当）|292|323|
|出方向の関連性を持つ起点|53|61|
|有向関係|969|1,053|

新規31曲のID割当を追加し、旧292曲のIDとmetadataの全バイトを保持した。旧起点の全関係と順序も保持した。保存したDBを別のCLIで復号し、SQL全行とstate、Library全2,974曲の元メタデータ、設定・schema・補助テーブル・Preferencesの保持、SQLite integrityを検証した。保存SQLをMusic選曲コアに読み込んだ5曲の全出力PIDが関係グラフで到達可能だった。

出力は `data/incremental-bundle-20261009-v01/Music Library.musiclibrary`。独立監査は `data/incremental-bundle-20261009-v01-independent-audit.json`。全61起点のオフライン評価は `data/ytmusic/index-expansion-evaluation-20261009-v01.json` に保存した。

次の20起点は `data/ytmusic/index-expansion-next-plan-20261009-v01.json`。すべて登録済みでincoming-onlyの曲を選び、検索未解決などの試行済みPIDを除外した。この時点ではcollectorのplan-onlyによる受理まで確認した。その後の連続取得・実DB保存は[大量拡張の記録](2026-10-09-large-index-expansion.md)にまとめた。

新規計画と監査の14テスト、既存のDB updaterの5テストが通過。個人の観測・DB・認証情報はGitの追跡対象外に置く。
