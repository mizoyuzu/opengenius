# iPod の純正 Genius へつなぐ経路

2026-10-04。公開一次資料の読解。実機・端末DBの取得、ファームウェアの実行はしていない。
ユーザーから分かっているのは FW 2.0.4 で、モデル番号・世代は未確認。
以下の classic 経路は候補であり、到着した実機の About と SysInfoExtended で確定する。

## 公式の到達条件

Apple の [iPod classic User Guide](https://cdsassets.apple.com/live/6GJYWVAV/user/ma1195_ipod_classic_160gb_user_guide.pdf)
pp.17,20,27 は、Genius を iTunes 側で設定して同期すると、接続していない端末でも
曲を起点に Genius プレイリストを生成できると説明する。長押しメニューの Start Genius は、
未設定・未同期、曲を認識できない、ライブラリに類似曲が10曲未満のとき表示されない。
保存済み Genius プレイリストの転送と、端末がその場で関係データから生成する動作は
別の検証項目になる。Genius Mixes は自動同期で転送する。
これは当時の公式フローであり、現在のサービス・FW 2.0.4 の実機動作を確認した結果ではない。

2曲 fixture は保存・ID対応の試験には使えるが、純正端末の生成成功を判定する曲数として
不十分。128曲 fixture の各 seed に32候補を持つ構成を端末検証にも使うのが妥当。

## 公開実装が解いている部分

| 対象 | ソースで確認できること | 未確認の境界 |
| --- | --- | --- |
| classic の通常ライブラリ | libgpod の機種表は iTunesDB と hash58 を指定。hash58 は FirewireID を使う | FW 2.0.4 の Genius補助ファイル一覧は機種表にない |
| iTunesCDB / SQLite 一式 | nano5G・iPhoneOS3.x の経路。CDB は圧縮した iTunesDB | classic へこの経路を適用する根拠にならない |
| iTunesDB の Genius CUID | `parse_genius_mhsd` / `write_genius_mhsd` は mhsd type 9 の文字列を読み書き。期待長は32 bytes | この section 自体は曲間の関係配列ではない |
| SQLite Genius.itdb | metadata / similarities / config の3基本テーブルを作成 | BLOBの生成・移植と純正端末の受け入れは実装されていない |
| iTunesSD | libgpod の writer は shuffle 対象で簡易DBを生成 | classic Genius の出力先とする根拠はない |

出典: libgpod の [README.overview](https://github.com/fadingred/libgpod/blob/master/README.overview)
（機種表・チェックサム）、[itdb_itunesdb.c](https://github.com/fadingred/libgpod/blob/master/src/itdb_itunesdb.c)
（`parse_genius_mhsd`, `write_genius_mhsd`, `write_db`）、
[itdb_sqlite.c](https://github.com/fadingred/libgpod/blob/master/src/itdb_sqlite.c)
（`mk_Genius`）、[itdb_sqlite_queries.h](https://github.com/fadingred/libgpod/blob/master/src/itdb_sqlite_queries.h)
（`Genius_create`, `Library_create`）。

`mk_Genius` は既存ファイルを保持し、無い場合に SQLite schema を作る。
3テーブルの削除・再挿入はコメントアウトされた TODO で、推薦データ生成の完成実装ではない。
SQLite版 Library schema には `item.genius_id`、`db_info.genius_cuid`、
`container.smart_is_genius`、`container_seed` がある。ただし classic の mhit への
Genius ID の格納位置をこの schema から決めることはできない。
調べた libgpod の itdb_itunesdb.c には `mhgd` の処理は見つからなかった。
存在しないこと一般の証明、別形式での意味の確定にはならない。

## Music.app の形式との関係

本プロジェクトで調べた Music の `itma +0xCC` は Genius ID と一致したが、
iPod の `mhit` は別レコード。数値オフセットだけを移植しない。
Music の Genius DB はページ単位で暗号化され、Preferences の鍵ヘッダーと対応する。
libgpod の SQLite生成は通常の `sqlite3_open` を使うため、同名ファイルでも
Mac版の暗号化DBをそのまま端末へコピーできる証拠にはならない。
3基本テーブルの一致は論理データ変換の手掛かりに留める。
ローカルの確定事項は [ID/BLOB解析](2026-10-02-compatibility.md) と
[128曲のネイティブ保持試験](2026-10-04-larger-native-library.md) を参照。

FW 2.0.4 を直接調べた公開研究の
[仕様・文字列一覧](https://github.com/giek2000/ipod-classic-firmware-research/blob/main/specs/iPod_Classic_7G_35_2_0_4.md)
には `SupportsGenius`、Genius各データの MinVersion / MaxVersion、SQLite文字列、
`StartGenius` 等が記載されている。
これらは機能とversion交渉を調べる入口になるが、この一覧だけでは
実際の値、ファイル配置、暗号化VFS、BLOBの互換性は確定しない。
今回閲覧した当該仕様ファイルには `Genius.itdb` / `genius_similarities` / `mhgd` の
文字列掲載は見つからなかった。以前の調査記録にある広い表現は、
この仕様ファイル単体で確認できる範囲と区別する。

[Rockbox の tagcache.c](https://github.com/Rockbox/rockbox/blob/master/apps/tagcache.c)
は `database_idx.tcd` / `database_%d.tcd` と独自ヘッダーを使う。
Rockboxで音源・推薦リストを再生できても、純正FWの Genius互換性の判定にはならない。

## 次に実装・検証できること

1. 端末が来るまでは、読み取り専用の出力監査器を準備する。
   ファイル名・magic・サイズ・SHA、iTunesDB の section type/長さ、
   曲のID対応、SQLiteなら schema/version と関係の参照整合性を検査する。
   CUID・鍵・端末IDの実値はレポートへ出さない。未知ファイルは書き換えない。
2. 到着後、純正同期でできた端末DBを退避し、Geniusなし／あり、
   関係を変更したLibraryの同期後、の差分をローカルで比べる。
   同じPIDの曲が端末側でどのGenius IDに対応するか、補助DBのページ形式・
   version制約・チェックサム範囲を確定する。一般プレイリストの転送も別に確認する。
3. 最初の端末生成試験は128曲の合成音源と既知グラフを使う。
   seed・Refresh前後・保存結果・候補集合を記録し、未知ID、候補数不足、
   正常グラフを対照にする。本番Libraryや本人のCUIDをActionsへ送る必要はない。

現在の128曲試験は Music が曲IDと全Geniusテーブルを保存後も保持した証拠。
Geniusメニューは無効だったため、純正生成や端末への変換成功とは扱わない。
Music側の有効化が未解決でも、推薦の具体的な曲名と候補列の評価はローカルで進められる。
