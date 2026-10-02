# Music選曲コアの限定実行

## 得られた結果

Linux上で提供Music実行ファイルのARM64選曲コアを実行し、
関係リストを変更した暗号化DBがコアの出力を変えることを確認した。
Pythonで推薦アルゴリズムを書き直した結果ではなく、Music内の命令を実行した結果である。
ただしDBアクセス、履歴、時刻、乱数などは実験用callbackを与えており、
Music.app全体の実行、実ユーザー環境の再現、iPodでの受け入れは検証していない。

| 実験用config | 元DBの出力件数 | 関係を逆順にしたDBの出力件数 | 対象 |
| --- | --- | --- | --- |
| 元の7フィルター | 各1件（seedのみ） | 各1件（seedのみ） | 全6seed |
| 重複回避のみ | 各6件 | 各6件、関係列の逆順化が反映 | 全6seed |
| distanceを除去 | 5件 | 未実施 | 1seed |
| compatible_genreを除去 | 3件 | 未実施 | 同じ1seed |

重複回避だけを残した実験では、seedを先頭に置き、その後に保存された関係列を
自分自身を重複させずに追加した列と一致した。元DB／変更DBの12ケースすべて一致した。
元の7フィルターの場合は、関係列の逆順化だけでは追加の曲が返らなかった。
distanceとジャンル適合を個別に外すと返る曲が増えたため、
この条件下ではフィルターの組み合わせが候補の選択に影響する。
これは元環境でGeniusが動かなかった原因を特定したという意味ではない。

選曲の候補を増やすには、関係列だけでなくmetadata／configとの整合性も重要だと分かった。
フィルター除去を本番設定として採用したわけではなく、因果関係を確かめる対照実験である。

## 呼び出した処理

対象ビルドはSHA-256を固定し、別の実行ファイルは拒否する。

| アドレス | 処理 |
| --- | --- |
| 0x100364d04 | callback tableとcontextからlibrary contextを作成しconfigを読む |
| 0x10036532c | seedのmetadataを読みcluster contextを作成 |
| 0x100365718 | 次の曲のGenius IDを返す。0で終了 |
| 0x100365d94 | cluster内の資源を解放 |

0x100365718の戻り値はオブジェクトのポインターではなく、
選択した曲オブジェクト+8にあるIDである。
このIDはsimilaritiesから渡された8バイトの値に対応する。

callback tableのABIも機械語で確認した。

| offset | 実験用実装 |
| --- | --- |
| 0x00 | configポインターを返し、長さu64とversion u32を出力引数に書く |
| 0x08 | Genius IDに対応するmetadataを返す |
| 0x10 | metadata内に曲IDがあるかを返す |
| 0x18 | Genius IDに対応するsimilaritiesを返す |
| 0x20 | 実験ヒープ内の解放ポインターを検証 |
| 0x28 / 0x30 | 再生／スキップ時刻と件数をすべてu32の0として返す |
| 0x38 | 実験用の現在時刻0 |
| 0x40 | Pythonの固定seed乱数で指定された上限未満の値を返す |
| 0x48 | little endianのため何も変換しない |

元のcallbackはLibrary側の曲存在確認や実際の履歴、時刻、arc4randomを利用する。
本実験はその環境を再現していない。特に履歴を0にした結果をユーザーの履歴と取り違えない。
同じseedを試すたびに乱数をリセットして比較条件を揃える。

## エミュレーターの範囲

Mach-Oのsegmentsをメモリへ配置し、対応するchained fixupsを解決する。
PAC関連の署名／認証命令は省略し、認証付き分岐・復帰は通常の分岐として実行する。
これは保存されたポインターを利用する限定実験で、PACの再現ではない。

メモリ確保／解放、コピー、初期化とlibc++のnext-prime補助をPythonで供給する。
next-primeの境界動作は[LLVM libc++の実装契約](https://github.com/llvm/llvm-project/blob/main/libcxx/src/hash.cpp)
を参照し、上限を付けた素数探索で実装した。
ヒープは32MiB、1呼び出しは最大1,000万命令／30秒。
解放した領域は再利用しないため、長時間の大量生成には使わない。
対応していないimport、外部コード実行、syscallやtrapはエラーで停止する。
鍵、Musicバイナリ、個人のDBを外部へ送信しない。

元configの6seed実験は各DBで約275万命令、重複回避だけの実験は各DBで約6万命令だった。
これらは設定読み込みと選曲処理の実行数で、実環境の速度を表す数字ではない。

## 検証と保存物

13件のテストが成功した。追加3件は提供バイナリが必要なので、
`OPENGENIUS_MUSIC_EXECUTABLE`を指定して実行する。

- 手書きの小さなmetadata／関係データを実コアへ渡し、関係の逆順化とseedの重複回避を確認。
- metadataが存在しない候補が返らないことを確認。
- 未実装importとsyscallを拒否することを確認。syscall注入はエミュレーター内のメモリだけで行う。

個人のレポートはGit対象外:

- `data/genius-emulation-original-config.json`: 元設定、6seed、元DB／変更DB。
- `data/genius-emulation-relations-only.json`: 重複回避のみ、同じ12ケース。
- `data/genius-emulation-filter-ablation.json`: 1seedのフィルター除去比較。

`emulate_genius.py --profile`で選ぶ設定変更はエミュレーター内だけで行う。
元のLibrary.musicdb、Genius.itdb、Music.appと最終対象ライブラリは変更しない。

## 次の課題

metadata index 1〜3とdistanceパラメータの意味を確定し、YTMusicの関係を入れた時に
選曲を成立させるための条件を絞る。
その後、ライブラリのコピーへ新規曲IDとmetadataを加える実験に進む。
純正iPodへの同期形式と受け入れは、同期後DBまたは実機で別途検証する。
