# Genius indexの連続拡張

単発の起点追加から、予算付きの連続取得へ進めた。登録数はLibraryのPIDに割り当てたGenius IDの数であり、取得候補数とは区別する。Appleへの通信とMusic.appの起動は行わない。

## 取得方法

`expand_index_batches.py` は保存済みの受理済みグラフを検証してから、次の起点計画、認証済みYTMusic取得、追加観測のマッチング、増分state計画を順番に実行する。5秒間隔でHTTPリクエストを送り、総リクエスト数と試行曲数に上限を設ける。認証・制限・通信のエラーでは停止し、自動再試行しない。途中停止したバッチの生観測は保存されるが、自動で受理済みチェックポイントには統合しない。停止時の試行数はそのバッチの計画曲を含むため、完了曲数はcollectorの結果行で確認する。試行済みPIDは次の自動計画から外す。

既定の20曲バッチでは、未登録曲を5曲確保し、残りは登録済みの関連先から選ぶ。これまで観測が少ない歌手表記を優先して、特定の歌手が名前順で選ばれ続けることを避ける。作品・ジャンルの推測から推薦辺を作らない。

各バッチは入力hash、生観測の出典、受理・除外理由、全関係行、増分stateを保存する。stateは書き込み計画である。実DBへの保存は別途 `update_genius_bundle.py` でバッチ順に行い、保存後のstateと計画の一致を確認する。バッチを飛ばして新規IDを一括で割り当て直さない。

## 保存済み動画IDの再利用

検索未解決を減らすため、`seed_video_evidence.py` で保存済みradio観測からPIDと動画IDの対応を抽出できる。生観測のLibrary hashとidentity map hashが現在の入力と一致し、動画IDがローカル曲の一意の候補になり、歌手の全クレジット・長さ・バージョン表記が一致する場合だけ採用する。元ファイルの内容hashも保存・再検証する。

この対応は録音同一性が未検証のメタデータ候補であり、検索候補や対応表から推薦辺は作らない。collectorは指定された動画IDを起点として実際のradio/relatedを取得する。`--candidate-limit 100` でradioの取得範囲を広げられるが、ページ送りのHTTPも予算と間隔に含む。既定値は25。

明示的な起点計画を渡す場合は、全PIDに厳密な保存済み動画IDの根拠が必要になる。自動計画とは別に10曲ずつ進められる。

```sh
.venv/bin/python scripts/seed_video_evidence.py \
  --track-snapshot /path/to/tracks.json \
  --identity-map /path/to/identity-map.json \
  --graph-provenance /path/to/accepted-snapshots.json \
  --output /path/to/seed-video-evidence.json

.venv/bin/python scripts/expand_index_batches.py \
  --track-snapshot /path/to/tracks.json \
  --identity-map /path/to/identity-map.json \
  --baseline-state /path/to/state.json \
  --baseline-relations /path/to/relations.json \
  --baseline-evaluation /path/to/accepted-evaluation.json \
  --accepted-snapshots /path/to/accepted-snapshots.json \
  --seed-plan /path/to/known-video-plan.json \
  --seed-video-evidence /path/to/seed-video-evidence.json \
  --candidate-limit 100 --batch-size 10 --unindexed-per-batch 0 \
  --max-attempts 40 --max-requests 200 \
  --auth /private/path/browser.json \
  --output /path/to/new-run
```

## 最初の100曲の取得

5バッチで100曲を試行し、245 HTTPリクエストを使った。45曲の生観測を取得し、55曲は検索未解決。取得できた45曲のうち40曲で要求PIDを起点とするグラフを受理した。残りは起点のマッチングが曖昧だったため採用しない。

|項目|開始時|100曲試行後|
|---|---:|---:|
|登録曲|323|377|
|出方向の関連性を持つ起点|61|101|
|有向関係|1,053|1,488|

保存した5個の実DBコピーが各バッチの計画stateと完全一致した。独立した復号監査で旧323曲のID・metadata、Library全2,974曲の元メタデータ、設定・schema・補助テーブル・Preferencesを検証した。旧61起点の全関係と順序も保持している。

この新しいコピーのMusic.app・iPodによる受け入れは未確認。保存SQLと選曲コアの検証結果を、実機での動作確認とは区別する。

## 広いradio取得と最終コピー

現在のidentity map hashに結び付いた厳密な動画ID対応は250 PID分あった。このうち起点未観測でメタデータ条件を満たすものから40曲を選んだ。取得前の検証でカラオケ等の4曲を除外・差し替えた。通信は発生していない。差し替え後は20種類の歌手表記・31アルバム、未登録3曲と登録済み関連先37曲で、10曲ずつ取得した。

radioの指定limitを100にした。YTMusicのページ送りによって実際の返却行数は100を超える場合がある。返却行数を登録曲数に足さず、重複とローカル候補の曖昧さを除いたグラフのみ採用した。

最初の3バッチは各52 HTTPリクエストで完了した。最後は残り49リクエストの予算に達して停止し、9曲の完了済み生観測を残した。認証確認済み・RuntimeErrorの発生箇所がrequest・HTTP error statusなし・実計数と予算が同値、という予算境界を確認し、`--prefetched-batch` によって保存済みの9曲だけを追加通信なしで取り込んだ。オフライン回復ではlive collectorを呼ぼうとすると失敗するrunnerを使った。関連データが完成していない最後の1曲は取り込んでいない。

この段階は205リクエスト、先の100曲分と合計450リクエスト。再利用バッチの計数49は、既に取得した同じ49リクエストを表すため実通信に二重加算しない。全140曲を試行し、84曲の完成した生観測を取得、そのうち79曲の新規起点グラフを採用した。

|項目|今回の開始時|最終保存後|増分|
|---|---:|---:|---:|
|登録曲|323|504|181|
|出方向の関連性を持つ起点|61|140|79|
|有向関係|1,053|2,883|1,830|
|歌手の元表記|76|101|25|
|アルバムの元表記|108|125|17|

Libraryは全2,974曲であり、登録曲504はその一部。歌手表記の数は正規化された実人数ではない。検索未解決や曖昧な候補を埋めるためのIDだけの登録、逆向きの関係、作品分類からの合成辺は追加していない。

最終保存先は `data/large-index-final-bundle-20261009-v01/Music Library.musiclibrary` と同ディレクトリの `state.json`。最後の全受理済み関係・生観測出典は `data/ytmusic/large-index-budget-recovery-20261009-v01/batch-001/`。認証情報・生観測・Libraryの私的データは公開用のcommitに含めない。

関連する計画・collector・動画ID根拠・連続バッチ・DB updater・独立監査の56テストが通った。

最終の独立復号監査は `data/large-index-final-independent-audit-20261009.json` に保存した。旧323曲のPID/GID・metadata、設定、schema、補助テーブル、Preferencesを保持し、SQLite integrityは元・出力とも `ok`。変更されたbundleファイルは `Library.musicdb` と `Genius.itdb` のみ。旧61起点の全関連先と順序を正確に保持し、最終実stateは受理済みチェックポイントとバイト単位で一致した。

保存SQLをMusic選曲コアに渡し、Sweden / C418、Comfortable Tiredness / YUZUSOFT、とまどい / 水谷広実、SOLAR WAY / Team.Sol、Cycle / Takeshi Aboの5起点で各25曲を生成した。全出力PIDが保存グラフで到達可能だった。この独立監査の通信は0件で、Music.appは起動していない。
