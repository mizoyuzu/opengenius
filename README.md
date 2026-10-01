# OpenGenius

YouTube Musicの関係性データを使い、純正iPodのGenius機能で利用できる
互換データの生成を目指す個人用研究プロジェクト。
最終検証対象はFW 2.0.4。実機は未到着で、現在はMusic.appのライブラリを解析中。
Apple Music／iTunes側への読み込みも検証対象。

現時点でできることはLibrary.musicdbの読み取りと形式の調査。
Genius.itdbの復号、Apple形式のrank生成、互換DB書き込みは未実装。

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
