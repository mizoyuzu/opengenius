# Genius IDの対応と関係リストの再暗号化実験

## 到達点

提供されたMusicライブラリの6曲をGenius DBの6つのIDに対応付け、
既存の関係リストを変更して暗号化DBを生成できた。
確認したのは保存形式と暗号化の往復、SQLite整合性、他のテーブルの保持。
Music.appの読み込み、プレイリスト生成、iPodでの動作は未検証。

元のライブラリ、Music.app、最終対象のライブラリは変更していない。
個人のID対応表、復号DB、編集JSON、生成DBはGit対象外の`data/`に保存する。

## Library.musicdbとの対応

提供サンプルの925曲はすべて`itma`ヘッダー長が376（0x178）だった。
このレイアウトでレコード先頭から+0xCCのLE uint32を読み取ると、
919曲は0、6曲は互いに異なる非ゼロ値となる。
6つの値は`genius_metadata.genius_id`と完全に一致し、1曲ずつ対応する。

`inspect_music_library.py`はこのヘッダー長の曲に`genius_id`を出力する。
別のヘッダー長では当該フィールドを出力しない。
この位置の意味を他のMusicビルドでも保証するものではない。
0は保存されたIDがないことを示す観測であり、Genius適性の判定ではない。

`genius_fingerprint.item_id`の823件はすべて曲のPersistent IDに対応した。
Genius IDとは別のID空間である。6つのseedのうち1曲にはfingerprint行がなく、
fingerprintの存在だけではseed可否を説明できない。

## 観測したBLOB

`genius_similarities`の6行はversion=1。以下の構造で全行を損失なく往復できる。

| オフセット | 型 | 観測した内容 |
| --- | --- | --- |
| 0 | LE uint32 | 意味未確定。サンプルは0 |
| 4 | LE uint32 | BLOB全長から8を引いた値 |
| 8 | LE uint32 | 続くIDの個数 |
| 12 | LE uint64の配列 | 順序のあるGenius ID列 |

全長は`12 + 8 * count`。各行は5個または6個のIDを含み、
3行は自分自身のIDを含んでいた。全IDは既存metadata内に存在する。
明示的なエッジごとのrank値はこの形式では観測していない。
配列順序が実行時の推薦順位として使われるかは、まだ確認できていない。

`genius_metadata`は6行、version=1、dataは32バイト。
4つのLE uint64として読み取れるが、各値の意味は未確定。
小さな整数であることだけから値を合成して新しい曲に適用しない。

`genius_config`は1行、version=2、default_num_resultsとmin_num_resultsは0、
dataは151,212バイト。先頭の3つのLE uint32は(7,2,0)。
12バイトの前置部と12,600個の12バイトレコードという解釈は候補であり、
形式の確定ではない。調査したMusicの取得関数0x1003a12a0では
dataとversionを別々に取得し、dataをCFDataに渡す処理が見える。
この関数内にBLOBの各フィールドを読む処理は確認できなかった。
この実験ではconfigとmetadataをそのまま保持する。

## 生成と検証

`rewrite_genius.py`は元DBのSHA-256を含む関係JSONを出力する。
編集時は既存seedと既存metadata IDだけを許可し、重複IDや異なる元DBを拒否する。
すべての指定を検証した後、SQLiteトランザクションでsimilaritiesを更新する。
意味未確定の先頭ワードとversionは保持する。

生成時は復号に使った同じ鍵でAES-128-OFBを適用する。
ページごとに新しい12バイトnonceを生成し、ページ番号と組み合わせてIVにする。
先頭ページの平文窓16〜23バイトでは、暗号ストリームの位置を維持する。
予約領域のnonceが変わるため、DB全体のバイト一致ではなく論理行を比較する。

提供DBの6つの関係列を逆順にした実験では、6行だけの内容変更を確認した。
逆順化は保存形式の実験であり、推薦品質の改善を意図したものではない。
テーブル件数はmetadata=6、similarities=6、config=1、fingerprint=823、
additional_match_ids=40のまま。SQLiteのintegrity_checkはok、
再暗号化→復号後の全論理行も一致した。

テストは、BLOBのサイズ不整合、符号付きSQLite ID、未知ID／重複／元DB違いの拒否、
複数指定の途中に不正指定がある場合の無変更、他テーブルと未知ワードの保持、
複数ページの暗号化と平文窓を対象とする。

## 互換性の次の検証

1. Music.appでライブラリのコピーに実験DBを置き、読み込みとGenius生成を観測する。
   鍵はライブラリの設定と対応するため、DB単体を別ライブラリへ移植しない。
2. metadata／configの利用側と、関係配列の順序の意味を解析する。
   新しい曲をGenius化するにはLibrary側のID、metadata、補助設定の整合性が必要。
3. iPodへの同期時に生成されるDBを比較し、FW 2.0.4での読み込みと生成を検証する。
4. 同一音源を確認したローカル曲とYTMusicの関係を、確定した保存形式へ接続する。

[libgpodのGeniusスキーマ](https://github.com/fadingred/libgpod/blob/master/src/itdb_sqlite_queries.h)
には同名の基本テーブルがある。
[FW 2.0.4の文字列調査](https://github.com/giek2000/ipod-classic-firmware-research/blob/main/specs/iPod_Classic_7G_35_2_0_4.md)
にもGeniusテーブルとSQLite VFSに関する手掛かりがある。
これらの一致だけではMacの暗号化DBをそのままiPodで利用できるとは判断できない。
