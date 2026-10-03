# ローカルで分類と推薦を確認する画面

2026-10-03。`scripts/serve_music_review.py`と`web/music-review.html`を追加。
Python標準ライブラリで起動し、画面の外部ライブラリやCDNは使わない。
既定で127.0.0.1にbindし、Host名は制限しない。変更操作は同一Originとセッショントークンを検証する。
`--host`で待ち受けアドレスを指定できる。

## 起動

本プロジェクトの保存済みデータで分類・推薦再生成を使う例:

```sh
.venv/bin/python scripts/serve_music_review.py \
  --track-snapshot data/final-library-tracks.json \
  --config data/music-clusters-experiment-v01.json \
  --evaluation data/ytmusic/diverse-evaluation-v02.json \
  --identity-map data/final-library-identity-map-v04.json \
  --observations-directory data/ytmusic/coverage-diverse-en-v01 \
  --genius-reference data/genius-decrypted.itdb \
  --executable ~/Music.app/Contents/MacOS/Music
```

`http://127.0.0.1:8765`を開く。Host名の異なるプロキシ経由でも使用できる。
`--host`と`--port`で待ち受けを変更可能。
`--track-snapshot`だけでも分類・保存は使える。`--config`を省略すると
作品タグなし・kind不明（Off Vocal検出のみ）の状態で始める。
推薦再生成には最後の4入力がすべて必要。取得済みの起点だけを使える。

## 操作と保存

ライブラリで検索・アルバム・種類を絞り、曲を選択してタグや種類を変更する。
タグは追加と置換を選べ、種類は独立して変更する。
変更はPID overrideとして保持し、元のルールは残す。
個別訂正を解除すると元ルール・Off Vocal検出へ戻る。
曲ごとの分類根拠も確認できる。

保存ボタンは`data/music-review/clusters-日時-乱数.json`に新規保存する。
再開時はそのファイルを`--config`に指定する。
未保存の変更がある間はページを離れる際にブラウザの確認を出す。
元Library、Genius DB、入力の分類JSON、認証情報を書き換えない。

推薦画面では作品タグ・種類・選曲条件を指定して再生成する。
対象外の起点と候補を全共有グラフから除去するため、間接関係でも範囲外に広がらない。
一つの起点を表示する場合も、範囲内の他起点の観測された辺を残してから評価する。
前回の結果を比較用に画面内で保持する（ページを再読み込みすると比較履歴は消える）。
生成結果は`data/music-review/evaluation-日時-乱数.json`に新規保存。
分類設定のコピー・canonical JSONのSHA-256・観測入力のハッシュ・元Library SHAを記録し、
未保存の分類で生成した場合も設定を追跡できる。
`--evaluation`で保存済みの結果を開けるが、現在の分類との対応が確認できない結果は
再生成を案内する。

## 現段階の制約

音源HDDがないため、この画面は曲一覧による確認用で、音声再生はまだない。
YTMusicの追加取得は行わず、取得済み8起点の保存データを使用する。
分類を変えても推薦関係を新しく作るわけではない。
録音の同一性、Music.appでのDB読み込み、iPod実機動作は未確認。
この画面でいう通常条件も、互換genreデータがないためgenreフィルタを除いた比較条件。

検証: 全88テスト成功（実MusicバイナリとローカルHTTPを含む）。
ブラウザで2,974曲の読み込み、24曲のアルバム一括タグ付け、新規設定保存、
Minecraft BGM範囲のSweden起点9曲生成を確認。
画面はライブラリと推薦の2タブ。選択時だけ編集欄を表示し、推薦条件と前回結果は折りたたむ。
複数起点の結果も曲一覧を折りたたんで表示し、前回比較は現在表示する起点に揃える。
ブラウザで標準1曲→連続許容9曲の切替と比較を確認した。
JavaScript構文検証とブラウザエラー確認は成功。390px幅でページ全体の横溢れなし。
