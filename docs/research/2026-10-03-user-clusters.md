# 作品タグ・曲の種類とBGM選曲条件

2026-10-03。保存済み本番Libraryの2,974曲と、分散取得の8起点を使ったオフライン実験。
ネットワーク取得は0。Music ARM64コアの実コードをエミュレートした結果であり、
Music.appでの読み込みやiPod実機動作は未確認。

## ユーザーが指定する分類

`music_clusters.py`は作品・系統の複数タグと曲の種類を分離する。
たとえば「アイマス」「シャニマス」を同じ曲に付け、kindを
`vocal` / `bgm` / `off_vocal` / `unknown`から選ぶ。
アニソンとノベルゲーなどのタグの重複も許容する。ジャンルは必須ではない。

これは音響特徴による自動クラスタリングではなく、ユーザーが推薦範囲を指定する基盤。
アルバム名の部分一致、artist完全一致、曲名の部分一致、PIDでまとめて指定できる。
条件間はAND、同じ条件の値リストはOR。Unicode NFKC/casefoldで照合する。
タグは累積し、kindは後のルールが優先。初期kindはunknown。
曲名のOff Vocal/カラオケ表記を検出した後、個別PIDの訂正を最後に適用する。
InstrumentalだけではOff Vocalと断定しない。
混在サントラを作品名だけから全部BGM扱いしない。

設定はLibrary SHAに結び付け、未知PIDや不正な条件を拒否する。
[公開設定例](../../examples/music-clusters.example.json)と
[設定方法](../../examples/music-clusters.README.md)を参照。
現在はJSONとCLIで指定する。曲一覧でタグ・kindを選ぶUIはまだない。

## 推薦への適用

`evaluate_ytmusic_batch.py`に`--cluster-config`、繰り返せる`--cluster-tag`、
`--track-kind`を追加。複数タグはAND、kindは一致条件。
全起点と全targetを共有グラフ作成前に制限し、二段関係による範囲外への流出を防ぐ。
元観測は保持し、削除した起点・辺数と設定SHAを出力に記録する。
存在しないタグ・対象起点ゼロはエラー、対象内だが候補ゼロの場合は空結果として
ネイティブ処理を呼ばない。タグから新しい推薦関係を捏造しない。

## 距離条件の比較

同じ8起点のグラフで8設定を比較した。以下の曲数は起点を含む。

| 起点 | 通常条件（genreを除く） | artist/album最小距離1 | relations-only |
|---|---:|---:|---:|
| Welcome to Cafe Stella! | 4 | 13 | 13 |
| 陽だまり道 | 3 | 16 | 16 |
| 追想 - Tsuisou | 3 | 7 | 7 |
| Only My Railgun | 8 | 10 | 10 |
| Beautiful | 4 | 4 | 4 |
| Sweden | 1 | 14 | 14 |
| A Walk | 1 | 4 | 4 |
| 星の声 | 25 | 25 | 25 |

`artist-album-minimum-one`は観測されたtype3 index1/2の第2wordを2→1に変更する。
song距離、重複回避、skip、jitterなどは保持する。パラメータ意味は一部推定であり、
この試料での実測結果から同artist/albumの選曲制限を緩める候補とした。
全曲へ自動適用せず、CLIで明示的に選ぶ。履歴・再生時刻はエミュレータの固定値であり、
実アプリの履歴がある条件に一般化できない。推薦品質の向上はまだユーザー評価が必要。

ローカル比較結果: `data/ytmusic/distance-profile-sweep-v01.json`。
再実行は`scripts/sweep_genius_profiles.py --help`の入力を指定する。

## 範囲指定の実測

実験設定でartist C418とalbum Minecraft Volume Alphaを指定して
タグMinecraft、kind bgmを付与。同じ設定の範囲指定前は14曲、範囲指定後は9曲になった。

Sweden → Moog City → Wet Hands → Haggstrom → Dry Hands →
Subwoofer Lullaby → Clark → Danny → Excuse。

これは今回選んだアルバムの実験用分類であり、他作品のBGMを自動識別した結果ではない。
報告は`data/ytmusic/cluster-minecraft-bgm-v01.json`。設定と全曲分類は
`data/music-clusters-experiment-v01.json`、`data/music-cluster-classifications-v01.json`。
個人データはgitignored、Library/Genius DB/認証情報は変更していない。

検証: 実Musicバイナリを使う統合チェックを含む82テスト成功。
範囲外ノードをネイティブ処理前に除くこと、Off Vocalと個別訂正の優先順位、
設定のSHA/PID検証、距離設定の他フィールド保持を確認した。
