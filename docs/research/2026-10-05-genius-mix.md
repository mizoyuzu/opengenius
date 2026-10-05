# Genius Mix の保存形式・選曲経路の調査とテスト

2026-10-05。提供済み Music 1.7.0.146 ARM64 バイナリを読み取り、
限定エミュレーションとコピー不要のライブラリ監査を実施した。
Music.app の起動、アカウント通信、同期、iPod への書き込みはしていない。

## 到達点

複数 seed を渡すネイティブ Genius 関数を実行し、入力・乱数・上限・
関係データを変えるテストを追加した。これは Genius の複数 seed 経路の検証で、
その入口が Genius Mix の実際の再生から呼ばれることはまだ確定していない。
内部オブジェクトの Mix 種別32と、保存ファイルの playlist リスト構造は
追加調査で確認できた。Mix 専用の保存フィールド、表示、有効化、
アプリによる再生キュー補充、iPod 互換性は未検証。

公式の最終動作は、iTunes が作った Mix を自動同期し、端末の
Music > Genius Mixes で再生すること。各 Mix は再生のたびに異なる選曲を楽しめる。
これは当時の仕様で、現在のサービスや対象実機での成功を示さない。
出典: [Apple iPod classic User Guide](https://cdsassets.apple.com/live/6GJYWVAV/user/ma1195_ipod_classic_160gb_user_guide.pdf)
pp.17,20,27 と [iTunes Genius Help](https://help.apple.com/itunes/mac/12.7/en.lproj/itns22073.html)。

## バイナリ内の証拠

対象 SHA-256 は既存の `decrypt_genius.SUPPORTED_SHA256` と一致するビルドに固定。
`probe_genius_mix.py` は NUL 区切りの文字列全体の先頭を特定し、
`__cstring` と `__oslogstring` を対象に、関数境界・レジスタ上書き・分岐を
越えない最大12命令の ADRP/ADD 参照候補と、既存コアへの直接 BL を出力する。
間接呼び出し、ポインタ経由の参照、経路の完全な到達性は解析しない。

| 関数 | 確認した処理 |
| --- | --- |
| `0x100390af0` | GeniusMix 専用 playlist 種別への assertion。`0x100390cb8` で種別32を predicate に渡す |
| `0x100ad5ce4` | native playlist の `+0x10` を16ビットとして読み、指定種別と比較する |
| `0x10072ccf8` | “Genius Mix Make New Context” の要求フィールド記述子を登録。対象オブジェクト内 `+0x4f4`。選曲関数ではない |
| `0x10144227c` | seed 集合を作り、GeniusCreateClusterContext / GeniusCreateTracksListFromClusterContext のエラーを記録する経路 |
| `0x10039f644` | library context を取り出し、複数 seed 関数へ渡す。試行上限100 |
| `0x101872384` | `(library, uint64_seed_array, uint32_count, uint32_attempt_cap)` を受けるネイティブ関数 |
| `0x10039f7d4` | 既存の next-track `0x100365718` を繰り返し呼び、実在曲をリストへ格納 |

`0x101442160` → `0x10144227c` → `0x10039f644` → `0x101872384`
の直接呼び出しは確認できた。要求フィールド `+0x4f4` とこの経路の間の
データフローは未確認。フィールド名の存在だけで Mix 専用の ABI と断定しない。

追加のchained pointer / RTTI調査で、vtable address point `0x102093720` の
slot33が thunk `0x10144215c` を経て `0x101442160` を指し、
typeinfoの名前が `21GeniusTrackListSource` であることを確認した。
したがって上記はGeniusTrackListSourceのvirtual methodに属する経路。
一方、`+0x4f4` の要求フィールド記述子はITShRequestParser側に属する。
異なるオブジェクトのフィールドを直接同一視しない。
GeniusTrackListSourceのconstructorは `0x101441cf0`、factoryは
`0x101441bd0`（`0xf8` バイトを確保）、その直接呼び出し元は `0x1012d0628`。
constructorで `this+0xd0` のvectorを初期化し、destructor `0x101441fd0` で破棄する。
このfactory経路にも、調べた範囲でparserの `+0x4f4` への接続は見つからなかった。

## 限定実行で確認した挙動

`emulate_genius_mix.MixCore` は上記複数 seed 関数をそのまま呼び、既存の
next-track と destructor を使う。DB・履歴・時刻・乱数の callback は
既存 MusicCore と共通。実行失敗はエラーにし、生成後の context は finally で破棄する。
反復選曲はエラーにせず回数を記録する。入力重複、ゼロID、範囲外ID、
過大な試験上限は Python 側で拒否する。

- **複数 seed は曲の許可リストではない。** 入力 `[17,34,51,68]` から、
  関係データにだけ含まれる85も選ばれた。乱数0では `[68,34,85,17,51]`。
- **seed試行上限と生成曲数は別。** 試行上限2でも関係データ経由で3曲が返った。
- **1 seed は既存の単曲 constructor に委譲する。** 関係データを辿る点も一致する。
- **最初に抽選した seed の不適格は context 生成を失敗させる。**
  乱数0、99に metadata がない条件では `[17,99]` は失敗、`[99,17]` は成功。
  逆アセンブルでも最初の抽選時だけ primary seed を設定することを確認した。
- **再選択が起きる。** 128合成曲を64曲ずつの2群に分け、各群で全曲を相互の
  関係候補とした。乱数0/1の4試験とも80曲を返し、60曲が一意、20曲が再選択。
  他群の曲は出なかった。群内に関係を閉じたfixtureの結果であり、
  複数seed入力だけで群を閉じ込められるという意味ではない。

初回の4試験は重複回避だけを残した実験 config。標準 config や実際の履歴を含む
Mix 品質の判定には拡張が必要。固定乱数では同じ結果が再現し、乱数を変えると
小規模fixtureの曲順が変わる。追加の比較試験ではartist距離フィルタも有効にした。
アプリのキュー補充や再起動後の履歴保持は試験していない。

### 同じ context からの継続取得

`MixCore.open_mix()` と `MixSession.next_batch()` を追加した。
乱数の初期化と constructor 呼び出しは session 開始時だけで、batch 間では
ネイティブ context・フィルタ状態・乱数状態を維持する。
64曲fixtureで25+25+30曲を取得した順序は、一度に80曲要求した結果と一致した。
artist距離フィルタ `[1,2,6,0]` を使う別fixtureでも、7+7+10曲の取得が
一度に24曲要求した結果と一致することをテストした。

1コアでは同時に1つのMix contextだけを開ける。開いている間は別のcontext生成や
普通のplaylist生成を拒否し、乱数のリセットによる干渉を防ぐ。
session全体のnext呼び出しは最大1000回。終了・例外時はcontextを破棄し、
閉じたcontextからの取得を拒否する。0を返したcontextには追加nextを要求しない。
これはコアの継続取得の試験で、アプリの自動補充を再現したものではない。

### 内部Mix種別の実行確認

native playlist object のmagicと `+0x10` にkindを置いた合成メモリから
`0x100ad5ce4(object,32)` を実行した。kind 0/26/32/33で
返値は false/false/true/false。Genius playlist 26とMix 32を区別できる。
この結果は内部オブジェクトの値であり、LPMAへの保存位置を確定するものではない。

## 保存形式の調査

`inspect_music_playlists.py` は展開した Music DB の LPMA magic 候補について、
header / total / child count と BOMA 子レコードの境界を検証する。
生の名前・曲ID・payloadを出さず、サイズ・種類・SHAを報告する。
追加で、観測した `lPma` リストの92バイトヘッダー、`+8` の宣言件数と、
連続するLPMAレコードを検証する。root DBからリストへ至る全framingは未確定で、
magic候補は検証済み候補として報告する。
未対応の Mix 検出結果は `null` として、存在しないという判定と区別する。

提供ライブラリにはLPMA候補7件、全件の境界検証が成功した。
既知の Genius playlist は header 368 / total 704 / BOMA 3件、
`+79` の生バイト26。これは既存資料で Genius playlist に対応する値であり、
Genius Mix の識別値として流用しない。Mix を含むと確認できたfixtureは未取得。

追加調査では、提供済みFinal Targetの2つのLibraryも監査した。
3つの入力それぞれに1つの正常な `lPma` リストがあり、宣言件数は7/23/14で、
全LPMA候補を順に参照した。元サンプルではリストの直後に `hsma` があるため、
リスト末尾とDB末尾を同一視しない。他の2入力ではリストが展開DB末尾で終わった。

バイナリのdispatcher `0x1000dcdd4` は `lPma` / `lpma` のFourCCを扱う。
LPMA branch `0x1000dd63c` はヘッダー368バイトを要求し、
`0x1000dd704–720` では `+80` が非ゼロならそちら、ゼロなら `+79` を選び、
`&0xfe` の結果が `0x42` でないかを検証する。
このため監査器は両方の生バイトを報告する。3つの入力では `+80` は全件ゼロで、
`+79` は0/4/26/47/63/64/65のいずれか。32はなかった。
このfallback検証と内部objectの `+0x10` への転送を結ぶ処理は未確定。
同じauthenticated callback tableの `0x101efc628` と `0x101efc630` に
`0x1000d8618` / `0x1000dcdd4` の候補を確認した。前者はwriter候補だが、
LPMA書き出しとkind変換をまだ特定できていないため、Mix生成writerは追加していない。

## 再実行

```sh
.venv/bin/python scripts/probe_genius_mix.py \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/genius-mix-static-new.json
.venv/bin/python scripts/emulate_genius_mix.py \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/genius-mix-synthetic-new.json
.venv/bin/python scripts/inspect_music_playlists.py \
  '/mnt/temp-hdd/Geniused Music Library/Music Library.musiclibrary/Library.musicdb' \
  --output data/genius-mix-playlists-new.json
OPENGENIUS_MUSIC_EXECUTABLE=/home/mizoyuzu/Music.app/Contents/MacOS/Music \
  .venv/bin/python -m unittest discover -s tests
```

出力は新規ファイルに限定し、既存出力を上書きしない。
追加19件を含め、実Musicバイナリを指定した全234件のテストが成功した。
静的参照の誤検出防止、LPMA/BOMA境界、複数seedの実選曲を検証している。
継続取得・内部Mix種別・artist距離状態・親リスト境界の追加後は、全244件が成功した。
今回のレポートはGit管理外の `data/genius-mix-static-oslog-20261005.json`、
`data/genius-mix-synthetic-128-20261005.json`、
`data/genius-mix-playlist-inventory-20261005.json`。
追加の実行レポートは `data/genius-mix-context-batches-20261005.json`、
親リスト監査は `data/genius-mix-list-framing-{original,final,txt}-20261005.json`。

## 次の実装条件

1. 純正Mixを含むライブラリのコピーを観測し、通常playlist / Genius playlist / Mix
   のLPMAヘッダーとBOMAを比較する。rootから親リストへのframing、
   メンバー参照、保存kindから内部objectのkindへの変換を確定する。
2. 観測できた形式のreader/writerにlossless往復テストを追加する。
   未知フィールドと無関係なレコードの保持、壊れた長さと未知参照の拒否を検証する。
3. Mix要求の入力から複数seed経路までの接続を確認し、実曲・標準config・
   再生開始/補充/再開の比較を行う。ソート済みの固定リストだけで成功と判定しない。
4. 最後に対応する純正環境で同期前後のDB差分、専用メニュー表示、切断後再生、
   再起動後の保持を確認する。普通のplaylistとして再生できることと分けて判定する。
