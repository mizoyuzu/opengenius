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
