# OpenGenius

YouTube Musicの関係性データを使い、純正iPodのGenius機能で利用できる
互換データの生成を目指す個人用研究プロジェクト。
最終検証対象はFW 2.0.4。実機は未到着で、現在はMusic.appのライブラリを解析中。
Apple Music／iTunes側への読み込みも検証対象。

現時点でLibrary.musicdbの読み取りと、提供されたMusic.appの特定ビルドを
使ったGenius.itdbのオフライン復号に成功している。
既存Genius IDの曲との対応付けと、既存の関係リストだけを変更した
暗号化DBの生成にも対応している。Music.app／純正iPodによる受け入れ、
新しい曲のGenius化、Apple形式のrank生成は未検証。

## Genius DBのオフライン復号

提供されたMusic 1.7.0.146のARM64実行ファイルが必要。
対応する実行ファイルのSHA-256を固定し、異なるビルドは拒否する。
macOS、GitHub Actions、Appleアカウント、ネットワーク通信は不要。

```sh
python3 scripts/decrypt_genius.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/genius-decrypted.itdb
```

`Library Preferences.musicdb`のBOMA 502にある20バイトの鍵ヘッダーを取得し、
Music内の鍵導出関数だけをUnicorn上でエミュレーションする。
得られた16バイト鍵でAES-128-OFBの各ページを復号し、SQLiteの
`integrity_check`とGeniusテーブルの存在を検証してからコピーを保存する。
鍵は表示・保存しない。元ライブラリと実行ファイルは読み取りのみで、
既存の出力は上書きしない。復号したDBにも個人のライブラリ情報があるためGit対象外。

静的調査には`inspect_music_macho.py`を使える。
アドレスはASLR適用前の仮想アドレスで、文字列参照の検索は候補抽出。
間接呼び出し全般を解決するツールではない。

```sh
python3 scripts/inspect_music_macho.py /home/mizoyuzu/Music.app/Contents/MacOS/Music
python3 scripts/inspect_music_macho.py /home/mizoyuzu/Music.app/Contents/MacOS/Music --function 0x100c16fa0
```

## ライブラリの読み取り調査

Python 3と`cryptography`が必要。研究用依存関係は
`python3 -m pip install -r requirements-research.txt`で導入できる。

```sh
python3 scripts/inspect_music_library.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary'
```

ファイルのサイズ・ハッシュ、展開後の曲数、メタデータ種別、
Genius.itdbのヘッダーに基づく形式候補をJSONで標準出力に表示する。
元ファイルへの書き込み、外部通信、認証情報の読み取りは行わない。
Genius.itdbの形式候補は復号や互換性の検証結果ではない。

`--include-tracks`を付けると曲PID・タイトル・アーティスト・アルバムと、
提供サンプルで確認できたレイアウトの曲の長さも表示する。
個人用の出力を保存する場合はGit対象外の`data/`を使う。
このプローブは提供された形式で検証済み。他のアプリ・バージョンでは未検証。

## 既存曲の関係リストを変更する実験

まず復号したDBとライブラリのGenius IDを照合する。
タイトルはデフォルトでは出力しない。

```sh
python3 scripts/inspect_genius.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --database data/genius-decrypted.itdb --output data/genius-inspection-new.json
python3 scripts/rewrite_genius.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --export-relations data/genius-relations-new.json
```

JSONの`ordered_genius_ids`を編集して、別ファイルに暗号化DBを生成する。
対象は元DBのmetadataに存在するIDに限る。新規IDの割り当てや
Library.musicdbの変更は行わない。JSONの元DBハッシュも照合する。

```sh
python3 scripts/rewrite_genius.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --relations data/genius-relations-new.json --output data/genius-experiment-new.itdb
```

指定したsimilarities以外の論理レコードを保持し、再暗号化後の復号、
SQLite整合性、全テーブルの論理レコード一致を検証する。
検証結果は出力の隣に`.report.json`として保存する。
出力は実験用で、Music.app／iPodでの動作保証はまだない。
将来の読み込み実験には元ライブラリのコピーと対応する鍵ヘッダーが必要。
元のライブラリへの置き換えはこのツールでは行わない。

## YTMusicの少数seed実験

検索結果を確認してvideo IDを明示的に選択し、radioとRelatedを採取する。
例は提供ライブラリに1件だけ存在する「ツバサグラビティ」。

```sh
python3 scripts/probe_ytmusic.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --seed-title 'ツバサグラビティ' --video-id 6E9Kx_VMm5g \
  --output data/ytmusic/tsubasa-new-observation.json
```

認証設定はデフォルトで`../browser.json`を読み込む（`--auth`で変更可能）。
出力ファイルが既存の場合は上書きしない。個人用の観測結果はGit対象外。
このプローブはアカウント上のプレイリスト・評価を変更するAPIを呼ばない。

取得日時、セッションID、元の順位、関係の種類を保存する。
Related内の曲以外の項目は曲間エッジにしない。
順位はGenius rankとは別で、genius_rankは未確定のnull。
APIのlimitは最低取得数で、指定数より多く返ることがある。

タイトルとアーティストが一致し、双方で長さが読める場合は差3秒以内の
ローカル曲を候補として残す。別バージョン名は削除しない。
複数候補や長さが欠けるケースも未確認として扱い、音源の同一性を断定しない。
browser認証設定を使った検索成功だけでは、ログイン状態の確認成功とは扱わない。

検証: `python3 -m unittest discover -s tests`（研究用依存関係が必要）。

## Music選曲コアの限定実行

提供されたMusic実行ファイル内の選曲コアをUnicornで実行し、
元DBと書き換えた暗号化DBの候補列を比較できる。
DB取得、曲の存在確認、履歴、時刻、乱数はPythonの実験用callbackで供給する。
再生／スキップ履歴と時刻は0、乱数seedは0に固定している。
Music.app全体やiPodを起動するものではなく、実環境の選曲結果とは区別する。

```sh
python3 scripts/emulate_genius.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --experiment data/genius-experiment-reversed-02.itdb \
  --output data/genius-emulation-new.json
```

元のconfigでは6曲ともseed自身だけが返った。
対照実験の`--profile relations-only`では、メモリ上のconfigに既存の
重複回避フィルターだけを残し、全6曲で関係リストの変更が選曲結果に反映された。
`--profile without-distance`と`--profile without-compatible-genre`でも
フィルターの影響を調べられる。これらは実験用の設定で、DBファイルには書き込まない。
未知の外部関数やシステムコール、実行上限超過はエラーとして停止する。

提供バイナリを使う追加の統合テスト:

```sh
OPENGENIUS_MUSIC_EXECUTABLE=/home/mizoyuzu/Music.app/Contents/MacOS/Music \
  python3 -m unittest discover -s tests
```

この環境変数を指定しない通常のテストでは、バイナリが必要な4件をスキップする。

## 新しいIDとmetadataの実験

元のconfigを保ち、合成したmetadata IDの同一性だけを変えて選曲を比較できる。
このプローブはmetadataをエミュレーター内で作り、DBには書き込まない。

```sh
python3 scripts/probe_genius_metadata.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/genius-metadata-probe-new.json
```

`rewrite_music_ids.py`は、未対応の曲に新しいGenius IDを付けたLibrary.musicdbの
実験用コピーを生成する。指定JSONの元Libraryハッシュと曲PIDを検証し、
既存IDの置き換えやID衝突は拒否する。

```sh
python3 scripts/rewrite_music_ids.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --assignments data/library-genius-id-assignments.json \
  --output data/Library-genius-id-new.musicdb
```

これはLibrary側のID保存だけの実験で、対応するGenius DBの新規metadata／関係行を
作成する機能はまだ含まない。コピーを単体でGenius対応済みとは扱わない。
JSON形式と確認した制約は下記の調査記録に記載している。

## 調査記録

[初期調査と参照資料](docs/research/2026-10-01-genius.md)

[曲ID候補・補助設定の構造とYTMusic取得実験](docs/research/2026-10-01-followup.md)

[Music.appの静的解析とGenius DBの復号成功](docs/research/2026-10-02-static-decryption.md)

[Genius IDの対応と関係リストの再暗号化実験](docs/research/2026-10-02-compatibility.md)

[configの選曲フィルターと関係データの利用側](docs/research/2026-10-02-config-consumer.md)

[Music選曲コアの限定実行と関係リストの比較](docs/research/2026-10-02-core-emulation.md)

[metadataの再選択間隔とLibrary側の新規ID保存](docs/research/2026-10-02-new-track-ids.md)

## 認証ありYTMusicの少数曲取得

アーティスト・アルバムが重ならない10曲のローカル種曲一覧を作る:

```sh
python3 scripts/probe_ytmusic_batch.py '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary' \
  --output data/ytmusic/coverage-01 --plan-only
```

認証確認後に検索と推薦取得を進める場合は`--plan-only`を外す。
既定では`../browser.json`を使い、HTTP呼び出しを5秒以上空け、最大45回で停止する。
エラー時は自動再試行しない。同じ出力ディレクトリは成功済みの応答を再利用する。
別の時点の推薦を観測する場合は新しいディレクトリを指定する。
検索の一致は未確認の対応候補として扱い、互換DBへの書き込みは行わない。

追加で提供されたCookieを使い、アカウント確認と既選択の1曲の推薦取得に成功した。
10曲の検索では表記差などで厳密照合が成立せず、自動取得には進めなかった。
`--auth`で認証JSON、`--language en|ja`で応答言語を指定できる。
言語は種曲マニフェストにも保存し、異なる言語のキャッシュ混用を拒否する。
[取得方針・キャッシュ・認証確認の記録](docs/research/2026-10-02-ytmusic-source.md)

## 表示タグを変えないメタデータ対応表

Libraryのハッシュとpersistent IDを軸に、別名をGit管理外のJSONで持てる。
和英併記から作った曲名候補は無効の状態で保存し、確認した項目だけ有効にする。
アーティスト別名を使っても、録音の同一性は未確認の候補として扱う。

```sh
python3 scripts/music_identity_map.py '/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Music Library.musiclibrary' \
  --create --artist-evidence data/final-library-identity-audit.json \
  --output data/final-library-identity-map-new.json

python3 scripts/music_identity_map.py '/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Music Library.musiclibrary' \
  --identity-map data/final-library-identity-map-new.json \
  --observations data/ytmusic/tsubasa-authenticated-03.json \
  --output data/final-library-matched-relations-new.json
```

`--artist-evidence`を省略すると、アーティスト別名を空にした対応表を作る。
ネットワークは使わず、入力と既存出力の上書きを拒否する。
現時点では一括取得スクリプトへの組み込み、複数クレジットの分解、
確認済みvideo IDの直接登録は含まない。
[対応表の形式・初版の結果](docs/research/2026-10-02-identity-map.md)

## HDDを外した状態での再照合

保存済みの曲一覧を使えば、元Libraryを読み込まずに別名照合を続けられる。
元LibraryのSHAは保存時の値を使い、現在のバイナリを再確認したとは扱わない。

```sh
python3 scripts/music_identity_map.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-reviewed.json \
  --observations data/ytmusic/tsubasa-authenticated-03.json \
  --review-proposed --output data/final-library-offline-new.json
```

表記レビューの判断JSONを新しい対応表に反映する場合:

```sh
python3 scripts/music_identity_map.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map.json \
  --review-decisions data/final-library-title-review-decisions.json \
  --output data/final-library-reviewed-new.json
```

判断JSONは対象Libraryと入力対応表のSHA、曲PID、元曲名、別名、理由を持つ。
新しい対応表に書き出し、表示タグと元対応表を保持する。
`--review-proposed`の候補は別欄に出すだけで、有効な照合に自動採用しない。
7曲分の表記を確認して11トラックの別名を追加した結果、保存済み推薦の
対応候補は31から38 video IDになった。録音同一性は未確認のまま。
[オフラインの表記レビュー・結果・制約](docs/research/2026-10-03-offline-alias-review.md)

## 複数クレジットと保存済み検索の再利用

`artist_credit_sets`は全文ラベルと構成員を明示する。
ローカルかリモートで宣言したクレジットは全構成員の一致を要求し、
名前中のスラッシュや括弧を機械的に分割しない。
観測ごとの根拠を保持し、アルバム一致などからメタデータ上の優先候補を示す。
優先候補があっても録音同一性は未確認で、他の候補も残す。

```sh
python3 scripts/replay_ytmusic_search.py data/ytmusic/coverage-01/requests \
  --output data/ytmusic/search-replay-new.json

python3 scripts/music_identity_map.py --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --observations data/ytmusic/search-replay-new.json \
  --review-proposed --output data/final-library-search-new.json

python3 scripts/probe_ytmusic_batch.py --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --plan-only --output data/ytmusic/final-library-plan-new
```

検索結果は`search_candidate`として曲の同定に使う。推薦関係はradio/relatedから作る。
保存済み検索の再照合では63 video IDに候補があり、54に優先候補が出た。
保存済み推薦側では38 video IDに候補があり、28に優先候補が出た。
[検討した制約と複数クレジット・アルバムの扱い](docs/research/2026-10-03-credit-and-album.md)

## YTMusic候補をMusicの選曲コアに渡す

HDDなしで、候補照合から実際のARM64選曲コアまでを試せる。
使用するのは保存済み曲一覧・対応表・radio/related観測・復号済みGenius DBのconfigと、
提供済みMusicバイナリ。IDとmetadataはメモリ内の実験用で、DBファイルに保存しない。

```sh
python3 scripts/emulate_ytmusic_candidates.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --observations data/ytmusic/tsubasa-authenticated-03.json \
  --genius-reference data/genius-decrypted.itdb \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/ytmusic-core-experiment-new.json
```

ジャンルだけを外した条件と、重複回避だけを残した条件を比較する。
「ツバサグラビティ」＋28候補から両条件で25曲を生成できた。
検索結果は関係情報として拒否し、録音同一性・Music.app／iPod受け入れは未確認とする。
[選曲コアへの接続・生成結果・制約](docs/research/2026-10-03-ytmusic-core-bridge.md)

## 実験用DBとLibrary ID割り当て案を保存する

選曲候補を平文SQLiteの実験用コピーへ追加し、同じIDを使うLibrary割り当て案を作る。
元DBの行と補助テーブルを保持するが、補助テーブルの本番Libraryへの対応付けは未完了。
SQLite、使い捨て鍵によるAES往復、保存後DBでの実際の選曲コアを検証してから保存する。

```sh
python3 scripts/build_genius_dataset.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --observations data/ytmusic/tsubasa-authenticated-03.json \
  --genius-reference data/genius-decrypted.itdb \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output-directory data/genius-dataset-new
```

出力は`Genius.experimental.sqlite`、`library-assignments.json`、`report.json`。
本番Libraryを書き換えず、実際の鍵による暗号化も保留する。
[保存形式・検証結果・残る互換性の課題](docs/research/2026-10-03-genius-dataset.md)

## データ規模と複数起点を試す

DB生成に`--include-library-metadata`を追加すると全曲のmetadataを登録する。
追加曲の関係は空で、実際のradio/related関係だけを使う。推薦を捏造せずにDB容量を検証する。
2,974曲の試験ではSQLiteとAES往復が通り、29曲版と同じ25曲を生成できた。

保存済み検索から一意の起点候補を選ぶ場合は、取得側に`--seed-observations`を指定する。
曲一覧と対応表で再照合し、複数の録音候補や複数video IDがある起点は保留する。
認証を読み込む前の`--plan-only`で計画を確認できる。

```sh
python3 scripts/probe_ytmusic_batch.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --seed-observations data/ytmusic/final-library-search-replay.json \
  --auth data/ytmusic/auth-session.json --seeds 20 \
  --language ja --interval 5 --request-budget 100 \
  --plan-only --output data/ytmusic/coverage-new
```

認証更新後、同じ引数から`--plan-only`を外すと取得を開始する。
全20起点についてrelatedがある場合は認証確認と最大60回の取得で約5分。
HTTPエラー・認証確認失敗時は停止し、自動再試行しない。
保存済み検索の選択範囲には偏りがあり、今回の20起点はシャニマス中心。

取得後は共通ID空間で各起点を評価する。起点ごとの生成一覧、候補照合率、
アーティスト数、同じアーティストの連続、プレイリスト同士の集合の重なりを保存する。
取得できなかった起点はスキップとして明示し、検索結果から関係は作らない。

```sh
python3 scripts/evaluate_ytmusic_batch.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v03.json \
  --observations-directory data/ytmusic/coverage-new \
  --extra-observation data/ytmusic/tsubasa-authenticated-03.json \
  --genius-reference data/genius-decrypted.itdb \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/ytmusic/batch-evaluation-new.json
```

Nightly再ログイン後、20起点の取得と既存1起点を合わせた評価が完了した。
151曲・633関係の共通グラフで、16起点は25曲、BGMの5起点は1〜3曲を生成。
`--profile relations-only`ではBGMの5起点が5〜13曲になった。
生成曲には直接関係だけでなく2段の関係で到達するものもあるため、
`observed_relation_hops`に有向グラフの最短距離を記録する（コア内部の実行経路ではない）。
最新の全曲一覧はローカル`data/ytmusic/batch-evaluation-v06.md`、
再照合対応表は`data/final-library-identity-map-v04.json`。
[追加取得と21起点の比較結果](docs/research/2026-10-03-large-dataset.md)

本番Library全体から比較枠を明示して選ぶ場合は`--seed-plan`を使う。
計画は`schema_version: 1`、`source_library_sha256`、
`seeds: [{persistent_id, series, sample_kind}]`の形式。件数は`--seeds`と一致させる。
曲名・artist・長さを計画から上書きせず、現在の曲一覧を参照する。
`--seed-observations`とは同時に使えない。

```sh
python3 scripts/probe_ytmusic_batch.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --seed-plan data/final-library-diverse-seed-plan-v01.json \
  --seeds 20 --language en --plan-only \
  --output data/ytmusic/diverse-new
```

検索を伴う取得は現行SDKの棚見出し処理に合わせて`--language en`を使う。
任意related欄の解析失敗は明示してradioを保持し、HTTP・認証エラーは停止する。
複数の検索動画からalbum名が一意に一致する候補をmetadata上で優先できるが、録音同一性は未確認。
今回の20起点は8取得・12保留。8起点の通常条件と比較条件の曲一覧は
`data/ytmusic/diverse-library-results-v01.md`に保存した。
[分散20起点の結果と残る偏り](docs/research/2026-10-03-diverse-library-seeds.md)

作品・系統のタグと歌唱/BGM/Off Vocalは別々に指定できる。
[設定例と説明](examples/music-clusters.README.md)のJSONを`data/`へコピーし、
Library SHAと自分のルールを設定する。混在サントラのkindは確認したPIDで補う。
分類プレビューは`music_clusters.py`で出力できる。

```sh
python3 scripts/music_clusters.py \
  --track-snapshot data/final-library-tracks.json \
  --config data/my-clusters.json \
  --output data/my-classifications.json
```

上の`evaluate_ytmusic_batch.py`コマンドに
`--cluster-config data/my-clusters.json --cluster-tag ノベルゲー --track-kind bgm`
を追加すると、指定範囲の起点・候補だけを共有グラフに渡す。
`--cluster-tag`を繰り返す場合は全タグに一致する曲を選ぶ。
BGM向け比較条件は`--profile artist-album-minimum-one`で明示指定する。
[作品分類と8設定の実測結果](docs/research/2026-10-03-user-clusters.md)

分類と推薦はローカル画面でも確認できる。
`serve_music_review.py`で曲一覧を開き、アルバム一括タグ付けと曲ごとの種類訂正、
新規設定ファイルへの保存、保存済み関係からの推薦再生成を行う。
[起動方法・保存場所・検証範囲](docs/research/2026-10-03-music-review-ui.md)
