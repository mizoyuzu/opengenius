# 複数クラスタの純正形式と再検証手順

`prepare_native_cluster_filter.py --scope-policy` は、一つのGenius DBに複数のユーザー指定範囲を保存する。作品・ブランド・歌唱/BGMの条件を別のscopeに置ける。分類そのものは既存のLibraryに拘束した `music_clusters` 設定を使い、選曲境界の設定と分離する。

## 重複と不明曲の扱い

起点が属する**すべてのscope**を推薦曲も満たす。アイマス＋シャニマスの起点は両方の範囲に留まり、シャニマス＋CANVASの起点はCANVASにも留まる。共通曲を選んだあと別scopeへ広がることを許可しない。これはユーザーが明示した境界を厳密に保持する方針で、soft preferenceではない。

どのscopeにも属さない起点は、除外した種類以外の通常の関係を使う。不明を歌唱やBGMへ推測で分類しない。除外種類は所属に関係なく許可対象から外す。除外曲を起点にした場合は、観測したコアではその曲自身だけが残る。

各scopeのタグはAND、kindは指定値との一致条件。異なるscopeに同時所属することは許容する。全割当PIDの所属の組を作り、正のmetadata分類IDを割り当てる。許可表は起点の所属をすべて含む分類IDだけを持つ。所属組が空の分類はすべての非除外分類を許可し、除外分類は空の許可表を持つ。元のGenius ID、関係とその順序、artist/album/song欄は維持する。

設定のschemaは1、scopeは1〜16、所属組は最大256。名前の重複、未知タグ、不正kind、空の条件、未知フィールド、割当曲のないscopeを拒否する。単一範囲の従来CLIと混ぜて使えない。[公開設定例](../../examples/native-cluster-policy.example.json)は自分の分類と割当曲に合わせて編集する必要がある。

## 実データでの比較

元Libraryは2,974曲、合成baselineは292割当曲・969有向関係。保存済み観測53起点を同じ設定、metadata空間、上限25、固定履歴と乱数条件で比較した。使用したのはピン留めMusic 1.7 ARM64バイナリのUnicorn実行で、MacやAppleへ接続していない。

個人用の別設定に、Minecraftの既存明示ルールと、アルバム表記によるシャニマス・CANVAS・喫茶ステラの実験ルールを保存した。元分類設定とLibraryは変更していない。新しい作品タグのルールはkindを設定せず、アルバム表記だけで歌唱/BGMを断定しない。

| 所属組 | 割当曲 | 検証起点 | 曲順が不変の起点 | 延べ曲数 前→後 |
| --- | ---: | ---: | ---: | ---: |
| scopeなし | 122 | 29 | 29 | 613→613 |
| Minecraft | 10 | 1 | 0 | 18→9 |
| シャニマス | 131 | 20 | 5 | 417→417 |
| シャニマス＋CANVAS | 12 | 1 | 0 | 25→5 |
| 喫茶ステラ | 17 | 2 | 0 | 42→34 |
| 合計 | 292 | 53 | 34 | 1,115→1,078 |

シャニマスscope全体は143割当曲で、そのうち12曲はCANVASにも属する。曲数が同じでも曲順や選ばれた曲が変わる場合があり、作品制限による推薦品質の向上とは断定しない。

- Sweden：18→9曲、Minecraft内。
- Reflection：25→5曲。Reflection、無自覚アプリオリ、White Story、青とオレンジ、くだらないや。
- Welcome to Cafe Stella!：21→17曲、喫茶ステラ内。
- 星の声：25→25曲。シャニマス内の曲へ置き換わり、曲順も変化した。

全53起点で起点保持、重複なし、上限内、既知ID、全所属条件の保持を確認した。実baselineには除外曲がないため、Off Vocal除外と歌唱/BGM分離は別の明示7曲の対照データで検証した。対照ではA歌唱・B歌唱・両方所属・未分類・A Off Vocal・A BGMを同じ有向グラフに入れ、実Musicコアで起点別の許可集合を確認した。scopeの記述順を変えても許可PID集合は同じだった。

成果物はGit管理外の `data/native-multiple-clusters-20261007-v01/`、53起点の結果は `data/native-multiple-clusters-20261007-verification-v01.json`。最終検証CLIの追加schema検査、進行表示、実行コードhashも、Sweden・星の声・Reflectionの3起点で再確認した。前回の単一Minecraft範囲コピーにも同じ3起点の再検証を実行し、Swedenだけ18→9曲、残り2起点は曲順一致となった。

## 書き出し・更新・再検証

通常の合成baselineから新しいコピーを作る。

```sh
.venv/bin/python scripts/prepare_native_cluster_filter.py \
  --bundle 'data/incremental-bundle-20261005-v01/Music Library.musiclibrary' \
  --baseline-state data/incremental-bundle-20261005-v01/state.json \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --cluster-config data/music-clusters-multiple-experiment-20261007.json \
  --scope-policy data/native-multiple-scope-policy-20261007.json \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/native-multiple-clusters-20261007-v01
```

関係を更新する場合は、`update_genius_bundle.py` をフィルターなしのbaselineに適用する。その出力bundleと新しい `state.json` を上のコマンドへ渡して、分類フィルターのコピーを作り直す。フィルター適用済みDBを増分更新器のbaselineへ戻さない。既存の拒否条件は維持している。

再検証コマンドは、保存済みreportの入力・bundle全ファイルhash、元state、分類設定を検査し、同じ条件からSQLを独立に再生成して全テーブル・schema・integrityを照合する。その後、元とフィルター付きDBの実Musicコア出力を比較する。出力に起点ごとの前後PID、曲名、順序一致、件数、実行バイナリ・検証コードhash、送信0件、Music.app／iPod未確認を記録する。結果を書き出す前に入力が変わっていないことも確認する。

```sh
.venv/bin/python scripts/verify_native_cluster_filter.py \
  --baseline-bundle 'data/incremental-bundle-20261005-v01/Music Library.musiclibrary' \
  --prepared-directory data/native-multiple-clusters-20261007-v01 \
  --baseline-state data/incremental-bundle-20261005-v01/state.json \
  --track-snapshot data/final-library-tracks.json \
  --cluster-config data/music-clusters-multiple-experiment-20261007.json \
  --scope-policy data/native-multiple-scope-policy-20261007.json \
  --evaluation data/ytmusic/expansion-live-20261005-v01-expanded-evaluation.json \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/native-multiple-clusters-new-verification.json --progress
```

`--evaluation` を省くと全割当PIDを検証する。指定する場合は同じLibraryの、重複しない割当済み起点を持つ評価が必要。単一範囲のコピーも再検証でき、その場合は `--scope-policy` を省く。出力はすべての入力から独立した新しいファイルに限る。

全294テスト通過（環境条件18件スキップ）、ピン留め実行ファイルを指定した新規機能の8テストも通過。通常の増分更新器を含む既存挙動を保った。Music.appへの読み込み・参加状態の再保存、iPod転送、この境界をWebで編集するUI、soft preferenceのDB符号化は未確認・未実装である。
