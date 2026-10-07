# 純正形式のクラスタ制限をローカルで検証する

`prepare_native_cluster_filter.py` は、検証済みのOpenGenius合成DBを土台に、ユーザーの分類をMusicの `compatible_genre` フィルターへ符号化した実験用コピーを作る。AppleのジャンルIDを復元したものではなく、metadataの第0欄に独自の正の分類IDを置く。Mac接続、Appleへの送信、YTMusic取得は行わない。

## 選曲条件

| metadata第0欄 | 分類 | 起点から許可する分類 |
| --- | --- | --- |
| 1 | 指定タグ・種類の範囲内 | 1 |
| 2 | 範囲外・不明 | 1、2 |
| 3 | 明示的に除外した種類 | なし |

タグはすべて一致する条件、種類は指定値に一致する条件で、除外した種類を優先する。未分類曲にも分類2を明示する。範囲外の起点は通常の関係を利用し、範囲内の起点だけを制限する。除外した種類を起点にした場合、今回のMusicコアでは起点自身が残り、追加曲は選ばれなかった。

これはWebの「範囲内を優先して不足を補う」とは別の方針である。また、Webの「範囲内だけ」の評価はグラフとmetadata集合を小さくするのに対し、このDBは全曲のGenius ID、関係、metadataのartist/album/song欄を保持する。曲順の完全一致を一般には保証しない。

## 書き出しと保全

```sh
.venv/bin/python scripts/prepare_native_cluster_filter.py \
  --bundle 'data/incremental-bundle-20261005-v01/Music Library.musiclibrary' \
  --baseline-state data/incremental-bundle-20261005-v01/state.json \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --cluster-config data/music-clusters-experiment-v01.json \
  --cluster-tag Minecraft --exclude-kind off_vocal \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/native-cluster-minecraft-20261007-v02
```

入力は通常の増分更新器と同じ厳密な合成baselineである。実LibraryのPID/GID・全曲metadata、元snapshotと対応表のhash、DB全行、キーと参加状態を検査する。baselineから再構成した関係の差分がゼロであることも確認する。

変更するのは `genius_metadata.data` の先頭8bytesと `genius_config.data` のみ。Configにはmetadata index 0のtype 2レコードを追加し、既存フィルターの順序と値、Configの他のSQL列を維持する。その他のSQLテーブル、schema、関係の順序、Genius ID、metadata末尾24bytesは保持する。SQL integrity、暗号化後の復号全テーブル一致、保存直前の全入力一致を検査する。Library／Preferences／補助ファイルはbyte単位で同じである。

出力先は新しく入力から独立した場所に限定し、元DBを上書きしない。個人データ、分類とPID、暗号化DB、hashレポートはGit管理外の `data/` に保存する。

**フィルター適用済みコピーを通常の増分更新器へ戻すことはできない。** metadata第0欄が非ゼロになりtype 2も追加されるため、従来の拒否条件は維持する。関係を更新するときは通常baselineへ更新を適用してから、分類フィルターのコピーを作り直す。合成baselineの `state.json` をフィルター適用後の状態として流用しない。

## Music 1.7コアの実測

Linux上で、保存済みMusic 1.7バイナリのARM64選曲ルーチンをUnicornで実行した。対象SHA-256は `a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a`。履歴コールバックは0、乱数は固定条件であり、Music.app全体や実際の再生履歴とは異なる。

4曲の完全な有向関係を使った対照実験では、分類 `[1,1,2,3]` に対して起点1/2は同じ分類の2曲、起点3は分類1+2の3曲、起点4は自身のみを選んだ。全分類を許可する対照では全4曲を選び、許可リストを `[1,2,3]` から `[3,2,1]` へ逆順にしても曲順は同じだった。許可リストの順序をsoft preferenceとして扱う根拠は得られていない。

metadata第0欄が0の起点をtype 2付きで実行した初期実験は、エミュレーションの未割当メモリー読取で失敗した。実Musicでのクラッシュとは断定しない。今回の出力では全割当曲に正の分類IDを付け、この条件を回避する。

2,974曲のLibraryに対する実験は、割当292曲・969有向関係を保持した。明示ルール「Minecraft Volume AlphaかつC418」はLibrary上24曲に一致し、そのうち割当済み10曲を分類1、残り282曲を分類2とした。このbaselineに分類3はなく、種類除外の動作は4曲の対照実験で確認した。

53起点を同じmetadata空間・設定・件数上限25で比較した。範囲内の起点Swedenは18→9曲となり、全追加曲が分類1だった。残り52起点は曲数もPID順も完全に一致した。延べ選曲数は1,115→1,106。重複・不明ID・起点の欠落・許可範囲外の追加曲はなかった。実データでの範囲内起点は1曲であり、ほかの作品クラスタまで同じ結果になるとはまだ確認していない。

実験結果は `data/native-cluster-minecraft-20261007-v01/core-verification.json`。最終コードで作ったv02は、v01と暗号化前の全SQLテーブルおよびコアへ渡すConfig/metadata/similaritiesの全bytesが一致した。比較を `data/native-cluster-minecraft-20261007-v02/equivalence-to-core-tested-v01.json` に保存した。暗号化の乱数による暗号文の違いは曲順の比較に使っていない。

全290テストが通過し、環境に依存する17件はスキップした。別途、ピン留めMusic実行ファイルを指定した新規4テストがすべて通過した。対象はSQL変更範囲、未知分類・除外優先、入力不整合・trigger副作用の拒否、実コアによる範囲制限／空許可／許可リスト順序の対照である。従来の増分更新テスト5件も通過した。

Music.appへのこのフィルター付きコピーの読み込み、参加状態の再保存、純正メニューによる生成、iPod転送は未確認。次の実環境検証では、フィルターなしのbaselineと分けて、設定と分類欄の保持、起点別の生成結果を確認する。
