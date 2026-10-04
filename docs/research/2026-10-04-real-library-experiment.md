# 実LibraryとYTMusic関連データの互換コピー

## 実行条件と保存検査

本番HDDのFinal Target Library.musicdbを読み、保存済み対応表と同じSHAを確認した。
全2,974曲のGenius IDは0、元Genius DBの5テーブルは全て空だった。
元Library、Preferences、Genius DBを変更せず、data/内の新しい実験コピーへ保存した。
本番情報・音源・認証情報のアップロード、追加APIリクエストはしていない。

build_real_library_experiment.pyは以下を検査する。

- 実Libraryのハッシュと対応表、観測のLibrary provenanceが一致する。
- 検索候補を推薦辺として拒否し、実際のYTMusic radio/relatedだけを使用。
- 既存Genius IDがあるLibrary・非空のGenius DBはこの実験経路では拒否。
- 元DBのSQLスキーマを保持し、空configを初期化してmetadata/similaritiesを追加。
- Libraryの展開内容はGenius IDの4byte slot以外を維持。保存後全曲のmetadataを比較。
- Preferencesはバイト一致。実際の鍵をRAM内で導出して暗号化→復号し、全テーブル一致を確認。
- 保存したデータからMusic1.7.0.146の選曲コアをエミュレーションし、全選曲IDが観測の有向辺で到達可能。
- 出力直前と実行後の原本3ファイルのSHAが一致。

Music.app全体での実Library読み込み・Genius生成、iPod生成を確認した結果ではない。
前段のActions検査は合成曲128曲のネイティブ保存検査で、今回の実曲検査とは区別する。
空の補助テーブルを使ったため、他Library由来のfingerprint等を転用していない。
類似関係が取得できた曲だけに実験用IDを割り当て、残りの曲のIDは0のまま。
参加状態・CUID・アカウント関連フィールドは変更しない。

## 2つの規模

| 実験 | Library全曲 | 起点 | 関係に含まれる曲 | 有向辺 | M3U8 |
|---|---:|---:|---:|---:|---:|
| real-library-experiment-v01 | 2,974 | 8 | 93 | 90 | 8、欠落0 |
| real-library-experiment-v02 | 2,974 | 29 | 214 | 723 | 29、欠落0 |

v01は作品を分散したcoverage-diverse-en-v01。
v02はそれにcoverage-v06とtsubasa-authenticated-03を加えた。
対応表はfinal-library-identity-map-v04。
どちらも元Libraryのhashを今回のバイナリで確認し、割り当てた全IDと全DBテーブルの往復が成功。
論理監査器で90/723辺を読み、存在しないmetadataを指す辺は0。
後者は実験用Mac DBの平文をRAM内で調べた結果で、端末形式の互換性を証明しない。

## 実際の選曲と偏り

曲数は起点を含む。通常条件はジャンル条件だけ除外。
今回はartist/albumの最小距離を1にする既存の比較profileを使い、song距離等は保持した。

| 起点 | 通常条件 | 今回 | 推薦videoのうちmetadata優先照合 |
|---|---:|---:|---:|
| Welcome to Cafe Stella! | 4 | 13 | 12/42 |
| 陽だまり道 | 3 | 16 | 15/49 |
| 追想 - Tsuisou | 3 | 7 | 6/69 |
| Only My Railgun | 8 | 10 | 9/71 |
| Beautiful | 4 | 4 | 3/73 |
| Sweden | 1 | 14 | 13/62 |
| A Walk | 1 | 4 | 3/68 |
| 星の声 | 25 | 25 | 29/63 |

Sweden → Moog City 2、Moog City、Blind Spots、Wet Hands、Beginning 2、Haggstromなど。
Only My Railgun → InFINITE Line、Reflection、星の声 (Game Size)、Snow halation、
Hacking to the Gate、Sparkling Daydreamなど。
Beautiful → 星の声 (Game Size)、Reflection、Sincerely。

Beautifulの73推薦videoのうちLibraryへ優先照合できたのは3。
残った候補の偏りを距離条件だけで直すことはできない。
関連性の取得・Libraryの保有曲・照合の保留が結果を絞る。
YUZUの起点には光 (Karaoke Version)、sweet treasureのInstrument Versionが含まれた。
BGM/歌唱/Off Vocalの区分は曲名・作品範囲の別設定が必要で、artist/albumを緩めても解決しない。
YT動画とLibrary曲の録音同一性はmetadata照合のみで未検証。

全曲一覧はdata/real-library-experiment-v02/playlists.md。
関係の由来・時刻・候補照合はrelation-provenance.jsonへ残す。
追加21起点はアイマス中心で、結果が偏りなくなったとは言わない。

## 実音源を再生できる出力

export_real_playlists.pyはLibraryのBOMA11のUTF-8 file URLを読み、
Media.localized以下の相対パスでHDDへ付け替える。
相対パスで見つからない場合だけ一意のファイル名を使い、曖昧・欠落は省いて部分出力と表示する。
音源の内容をコピー・改変せず、実ファイルを参照するM3U8を作る。
29リスト・延べ535曲分の参照が全て解決できた。曲はリスト間で重複する。
出力README.mdに起点の曲名付きリンクがある。

実験用Music Library.musiclibraryの参照先は元Macのfile URLのまま。
Linux上で実音源を試聴する用途には、付け替え済みM3U8を使う。
M3U8は普通のプレイリストで、純正Geniusデータの同期や生成成功を意味しない。

## 再実行

```sh
.venv/bin/python scripts/build_real_library_experiment.py \
  --bundle '/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Music Library.musiclibrary' \
  --identity-map data/final-library-identity-map-v04.json \
  --observations-directory data/ytmusic/coverage-diverse-en-v01 \
  --observations-directory data/ytmusic/coverage-v06 \
  --extra-observation data/ytmusic/tsubasa-authenticated-03.json \
  --genius-reference data/genius-decrypted.itdb \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output-directory data/real-library-experiment-new

.venv/bin/python scripts/export_real_playlists.py \
  --bundle '/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Music Library.musiclibrary' \
  --media-root '/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Media.localized' \
  --report data/real-library-experiment-new/report.json \
  --output-directory data/real-library-experiment-new/playlists
```

iPodの出力経路は2026-10-04-ipod-genius-path.mdへまとめた。
端末を受け取った後の読み取り用にinspect_ipod_genius.pyも用意した。
checksum・mhit内部・端末のGenius BLOB変換は未解決で、未知データへ書き込む機能は追加していない。

代表8起点の各先頭3曲から重複を除いた21曲について、ローカルffprobeで音声codecと長さを確認し、
ffmpegで最初の1秒をnull出力へデコードした。21/21成功、Libraryとの最大長さ差は71ms。
ネットワークプロトコルはfile/pipeだけを許可し、音源は変更していない。
このサンプル確認は聴感による推薦品質の評価や、全535参照のデコード検査ではない。
記録はdata/real-library-experiment-v02/audio-sample-audit.json。

ローカル全137テスト成功。空DB初期化の保存・非空DBの拒否・未知関係IDの拒否、
M3Uの曖昧パス/欠落/別Library/再上書きの拒否、iPod DBの境界/秘密値の非出力を含む。
