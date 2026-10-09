# 接続中iPodでのGenius同期欠落

2026-10-06。実MacのMusic 1.5.6と接続中iPodを読み取り調査した。
現在のチェックアウトはmacos-probe。目的と既存監査器はorigin/mainの
README、iPod経路、iTunesDBレコード、前後比較の研究記録から確認した。
ユーザー提供の会話抜粋により、両DBの有効化状態を揃えた後にMusicで
OpenGenius由来の純正Genius生成と再起動後の有効状態保持が成功した経緯を確認した。

## 実測

- MacのLibrary.musicdbとLibrary Preferences.musicdbのplma+0x0cは両方1。
- Macも端末も2,973曲。全PIDが端末mhitのdbidと一致した。
- Macの286曲の非ゼロGenius IDは、端末の624-byte mhitの+0x1e4に
  すべて一致した。今回の観測プロファイルに限った対応で、他機種へ一般化しない。
- MacのGenius.itdbは生成成功時のバックアップとbyte-for-byteで一致。
- 生成成功時の2,974曲との差はGenius IDを持たない1曲の削除だけ。
  共通PIDの曲名・アーティスト・アルバム・Genius IDは一致した。
  この比較は音源ファイルの音声内容や全タグを比較したものではない。
- 端末iTunesDBには32-byteのtype-9 CUIDが存在。
  大小文字・UTF-16・hexのbinary表現を考慮すると、その値は両Mac DB内に見つかった。
  単純なASCII検索で見つからなかったことをCUID不一致と解釈しない。
- 端末に独立したGenius.itdbはなかった。Extras.itdbに3テーブルがあり、
  genius_metadata=0、genius_similarities=0、genius_config=1。
  configはversion=2、default=25、minimum=10、data=132 bytes。
  その選曲パラメータは設定済みのmoderate条件と一致した。
- 端末の75 MiBのiTunesControlは全て0で、関係DBの代替格納先ではなかった。
- iTunesDBの通常曲・プレイリスト構造の読み取り検証は成功、参照切れは0。
  type 2/3の各20プレイリストを既存監査器が40として合算するため、
  ユーザーに見える独立プレイリストが40あるとは解釈しない。

端末iTunesディレクトリの8ファイルを退避し、取得後と調査終盤に元とのhash一致を確認した。
個人DB・識別子・生ログ・バイナリ・検証コピーはGitへ追加していない。
ローカル作業先は/private/tmp/opengenius-ipod-20261006/。

## 同期ログ

2026-10-06 12:32 JSTのAMPDevicesAgentの2回の処理で、
`genius sync> 0 tracks`、config書き込み成功、`done (0)`を確認した。
曲ごとのcopyメッセージ、同期無効、SQL破損、Build/Allocate/Finishの失敗メッセージは
取得範囲にはなかった。Musicプロセスだけに絞った最初の検索は0件で、
実際の転送担当はAMPDevicesAgentだった。

0 tracksは転送コンテキスト内のリスト数であり、Libraryの全曲数ではない。
ログの不在だけから全ての分岐の未実行を断定しないが、空のExtrasテーブルと整合する。

## 同期コードの更新判定

実行はせず、インストール済みAMPDevicesAgentのARM64スライスを静的解析した。
アドレスはASLR前。対象fat binaryのSHA-256は
8387ce4b6a1b20aac35515c44d1419e2d0f145f715951aa8225bcb3a2dfea1cf。

- 内部track+0x190は64-bitのGenius ID。
  0x1002f63b4のGenius ID map構築がこの欄を読み、非ゼロIDを登録する。
- 内部track+0x198は32-bitのgenius_checksum。
  0x1003274f8でこの欄を読み、CFString 0x100847f10をキーとして出力する。
  CFStringの文字列ポインタを解決すると`genius_checksum`。
  直前の0x1003274e0は+0x190と`genius_id`を対応付けていた。
- 0x100618c30–0x100618c3cではGenius用の索引登録にID非ゼロと
  checksum非ゼロの両方を要求する。IDだけあってもこの索引には登録されない。
- 0x100506fc4–0x100506fd8では対応曲のIDとchecksumを比較し、
  両方が同じ場合は変更ありの返値へ進まない。
- 0x10050cc94以降の転送コンテキスト構築でも対応する曲の比較がある。
  0x10050cd58–0x10050cd64で双方のchecksumを比較し、等しい場合は
  更新IDを+0x78のリストへ追加する処理を通過しない。
  0x10050cd70–0x10050cd84が追加と値の反映。
- 0 tracksのログは0x10050d510でコンテキスト+0x88の値を読んで出力する。

従って、曲間データの同期はIDだけで決まるものではない。
ただし内部オブジェクトの+0x198をディスク上のmhit/itmaの同じoffsetへ移植しない。

## Library内の候補欄と実機検証

376-byte itmaの+0x68は、別の既存Mac LibraryのGenius ID付き479曲で
全て非ゼロ、IDなし4,568曲で全て0だった。+0xccのGenius ID以外で、
この強い対応を示すheader内の32-bit欄は+0x68のみだった。
一方、OpenGeniusの286曲では+0x68が全て0。

この欄の意味を、接続中の同じiPodで1曲だけ検証した。
Music終了後の完全バックアップを取り、Swedenの+0x68だけを0から1へ変更した。
音楽同期が有効で全曲ライブラリを対象にしていることを画面で確認して同期したところ、
AMPDevicesAgentのログは`1 tracks`、続いて`copy m 32 s 116`になった。
端末Extras.itdbはgenius_metadata=0/similarities=0から各1へ増え、
両行のIDはSwedenのGenius IDと一致した。PIDそのものではない。
端末mhitでは+0x1f0が0から1へ変わった。
このため、観測したMusic 1.5.6.11 / iTunesDB 117では、
`itma+0x68 -> mhit+0x1f0`の更新値対応と、値の変更が同期トリガーになることを確認した。

Appleの元checksum算法そのものは未確定である。IDの直後の+0xd0/+0xd4は既存479曲でも0だったため、
隣接しているという理由でこれらをchecksumとは呼ばない。
別の提供済み復号DBとの共通IDは1件だけで、CRC32の単純な候補式は一致しなかった。
そのDBと現在のLibraryの関係が確立していないため、Appleのchecksum算法の検証とは扱わない。

Musicを通常終了後、完全なbundleコピーを作成した。
Swedenの1曲のitma+0x68だけを0から1へ変え、再圧縮・暗号化した。
展開後の変更は1 byte。許可した4-byte欄を元へ戻すと全展開データが一致する。
全曲の解析済みメタデータとGenius ID、Genius.itdb、両有効化状態を保持している。
0→1は診断用の値だったが、同期対象化と端末保存値の対応は確認できた。
試験後、元Libraryは展開後の曲情報・Genius ID・Genius.itdbと一致する状態へ戻し、
端末Extras.itdbも試験前の空テーブルへbyte-for-byteで復元した。

## OpenGenius出力への反映

検証前の通信確認ではLuLuのextensionは稼働していたが、MusicとAMPDevicesAgentの
ルールはALLOW、AMPLibraryAgentはBLOCKだった。
Appleへ送信しないという条件のため、前2つを一時BLOCKにしてよいかユーザーに確認した。
確認前に検証コピーのMusic起動や同期は行わず、Musicは終了状態。
LuLuのaction 0=BLOCK、1=ALLOWは公式ソース
[consts.h](https://github.com/objective-see/LuLu/blob/master/LuLu/Shared/consts.h)で照合した。

OpenGeniusの出力側には`scripts/genius_sync_revision.py`を追加した。
metadata/similaritiesの行BLOBから安定した非ゼロCRC32 tokenを作り、
Genius IDとともにLibraryの+0x68へ保存する。Appleの元checksumを再現したとは扱わず、
関係行の内容が変わればtokenも変わるOpenGenius用変更検出値とする。
実Libraryの286曲へ適用した一時出力では、286曲すべてが非ゼロかつ重複なしになり、
曲情報とGenius IDは入力と一致した。

端末上のGenius生成そのものは別検証であり、1曲転送の成功だけで類似曲10曲条件を満たすとは扱わない。

scripts/audit_ipod_genius_sync.pyはこの観測プロファイルの読み取り専用監査器。
PID/GIDの対応と候補欄の0/nonzero件数、端末Extrasのテーブル件数だけを表示する。
欄のネイティブ確認とFW生成の確認はfalseと明示する。
ID、パス、CUID、鍵、BLOBはレポートへ出さない。

```sh
python3 scripts/audit_ipod_genius_sync.py /path/to/Music.musiclibrary /path/to/device-snapshot
python3 -m unittest discover -s tests -p test_ipod_genius_sync_audit.py
```

5テストが成功。元Library・検証コピー・端末スナップショットの実データ監査も成功。
現在の結論は「ID対応は正常。Genius行の同期には曲ごとの更新値が必要で、
OpenGeniusは全Genius対象曲へ安定した非ゼロrevisionを出力する」。
