# Genius configと関係データの利用側

## 確認できたこと

提供Music実行ファイルの静的解析と復号DBを照合し、configが選曲フィルターの設定を
格納することを確認した。単純な固定長レコードではなく、型ごとの可変長構造だった。
全151,212バイトをパースして再エンコードした結果、元のBLOBと完全に一致した。
この一致は保存構造の検証であり、パラメータ値の新規生成やiPodでの動作の検証ではない。

対象ビルドと鍵導出は[復号の記録](2026-10-02-static-decryption.md)と同じ。
元のMusic.app、ライブラリ、最終対象ライブラリは変更していない。

## configを読む処理

0x1003a048cは`genius_config.data`を取得し、ポインターとversionを出力引数に、
BLOBのバイト長を戻り値に返す。
0x1003a12a0だけを見るとCFData作成と解放が見えるが、解釈を担当するのは
後段の0x100364d04と各型のパーサだった。

0x100364d04は先頭のLE u32をフィルター数として読み、
その回数だけLE u32の型を読み取り、1〜5の処理へ分岐する。
処理後、追加でu64とu32二つをcontextに読み込む。
提供サンプルは7個のフィルターと末尾の(0,10,20)だった。
末尾二値とSQL列のdefault_num_results／min_num_resultsとの対応は未確定。

| 型 | バイナリ内の名前 | 保存される内容 | サンプル |
| --- | --- | --- | --- |
| 2 | compatible_genre | u32 metadata index、u32件数、可変長map | index 0、591件 |
| 3 | distance | u32パラメータ4個 | (1,2,6,6)、(2,2,10,6)、(3,3,10,6) |
| 4 | skip_count | u32パラメータ4個 | (2,604800,0,50) |
| 5 | random_jitter | u32パラメータ2個 | (70,100) |
| 1 | already_added | version 2ではu32パラメータ4個 | (20,50,10,10) |

名前は文字列を返す各仮想関数で確認した。パラメータの単位やしきい値の意味は、
名前だけから推定していない。config parserは現在、提供されたversion 2の構造のみ対応する。

型2は0x100367888で読み込まれる。各mapレコードは
`LE u64 key, LE u32 count, count個のLE u64`。
利用側0x100367e68は設定されたindexで曲のmetadata ID配列を読み、
そのIDをこのmapのキーとして検索する。文字列`genre_ids`と`compatible_genres`、
名前を返す0x100368574の`compatible_genre`がジャンル適合処理の証拠になる。

サンプルのmapは591個の異なるキーと17,997個のリスト要素を持つ。
すべてのリストが自分のキーを含み、未知の対象IDやリスト内の重複はない。
すべての関係は対称だが、単純なグループの一覧としてまとめられるとは限らない。
この591個のIDはローカル曲925件やGenius seed6件のID空間とは別。

型3の先頭パラメータも配列の添字として使う命令を確認した。
同じartist／albumの繰り返しを抑えるような役割は考えられるが、
metadata index 1〜3が具体的に何を表すかはまだ断定しない。

## metadataとsimilarities

0x100365b5cでは曲のmetadataポインターとバイト長をコールバックから受け取り、
バイト長を8で割って要素数とし、連続する8バイト値を曲オブジェクトへコピーする。
`_metadata_ids != __null`というアサーション文字列も確認できる。
提供サンプルの32バイトは4つのIDとして使われる解釈と整合する。
ジャンルフィルターのindexが0なので、この経路では最初のIDがジャンルmap検索に使われる。
残るIDの意味と、曲ごとに正しいIDを得る方法は未解決。
DB列の読取ラッパーは実行時のTrackData記述子を経由するため、
各field IDとテーブル名の対応は、この調査では完全に確定していない。
静的な記述子の隣接関係だけでcallbackとDB列の対応を決めない。

0x100366848はsimilarities先頭のu32二つを読み、最初の値が0なら
offset 8のu32を件数として、offset 12から8バイト要素を順番に消費する。
最初の値が非ゼロの場合はoffset 8以降を0x100369548で展開してから読む。
その分岐に非圧縮のID列を書き込むのは不適切なので、現行のcodecとwriterは
圧縮形式を明示的に拒否する。提供DBの6行はすべて非圧縮で、書き換え実験の対象は維持できる。

リスト順序は利用側でも保持される。ただし選曲ループにはconfigの各フィルターがあり、
random_jitterも存在するため、保存順序と最終的なプレイリスト順序は別に検証する必要がある。

## ツールと検証

`genius_format.py`にversion 2のtyped config parser／encoderを追加し、
`inspect_genius.py`のレポートにフィルター名、パラメータ、ジャンルmapの要約を追加した。
これは読み取り調査用で、writerはconfigを変更しない。
個人のレポートは`data/genius-inspection-typed-config.json`に保存した。

テストは手書きのバイト列で可変長map、64ビットID、型別パラメータ、末尾値を検証し、
切断、余分なバイト、不明な型、件数不整合、キー重複を拒否する。
既存similaritiesテストに圧縮形式の拒否も追加し、計10テストが成功した。
実DBのconfigは全バイト一致で往復検証した。

## iPod同期の現在地

[libgpodのmk_Genius](https://github.com/fadingred/libgpod/blob/master/src/itdb_sqlite.c)
はGenius.itdbのスキーマを作るが、config／metadata／similaritiesの更新は未実装として残る。
このソースから選曲パラメータの合成方法を得ることはできなかった。
[FW 2.0.4の文字列調査](https://github.com/giek2000/ipod-classic-firmware-research/blob/main/specs/iPod_Classic_7G_35_2_0_4.md)
には各Genius形式のmin/max versionやSQLite VFSの文字列があるが、
Macのconfig設定や暗号化方式がそのまま使えることは示していない。

現時点で同期後のDBと実機がないため、この枝でiPodの受け入れを検証できていない。
次に有効なのは、曲のmetadata index 1〜3と選曲フィルターの対応を確定すること、
Macの選曲処理を限定エミュレーションして関係列の変更が結果へ与える影響を測ること、
そして実機到着後に同期済みDBとの違いを調べること。
