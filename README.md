# OpenGenius

YouTube Musicの関係性データを使い、純正iPodのGenius機能で利用できる
互換データの生成を目指す個人用研究プロジェクト。
最終検証対象はFW 2.0.4。実機は未到着で、現在はMusic.appのライブラリを解析中。
Apple Music／iTunes側への読み込みも検証対象。

現時点でLibrary.musicdbの読み取りと、提供されたMusic.appの特定ビルドを
使ったGenius.itdbのオフライン復号に成功している。
Apple形式のrank生成、互換DB書き込み、純正iPod上での動作は未検証。

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

## 調査記録

[初期調査と参照資料](docs/research/2026-10-01-genius.md)

[曲ID候補・補助設定の構造とYTMusic取得実験](docs/research/2026-10-01-followup.md)

[Music.appの静的解析とGenius DBの復号成功](docs/research/2026-10-02-static-decryption.md)
