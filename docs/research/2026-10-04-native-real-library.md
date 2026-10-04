# 実音源を使うネイティブMusic検証

## 推薦プレイリストの保存と再起動

Actions run `37209669955`で、実観測から選曲した3リストを通常プレイリストとして
Musicに作成した。10曲・14曲・25曲の計49項目が、Musicの正常終了・明示的な
再起動後も同じプレイリストID、名前、全曲の順序で保持された。全て
`genius=false`で、Geniusプレイリストは作成後・再起動後とも0件だった。

入力計画は元LibraryのSHAに結び付けた選曲レポートから作り、全曲の実音源が
既存47曲の転送対象に含まれる場合だけ採用した。音源不足によるリストの切り詰めは
行っていない。再起動後の監査は入力manifestと実際の観測を独立して比較し、
collector内の成功フラグだけでは合格にしない。

終了後も2,974曲、全Persistent ID、214件のGenius ID、Geniusの全SQLテーブルが
一致した。47曲のlocationと3曲の再生進行も確認した。通常プレイリストの
作成・保存を確認した結果であり、MusicのGenius機能やiPod上での生成は未確認。
結果をローカルへ回収して監査した後、一時Actions secretは削除した。

`prepare_native_playlist_plan.py`が完全な選曲計画を作り、
`audit_macos_real_library.py --manifest <input-manifest>`が再起動後の保存を監査する。
作成前、最初の終了後、再起動して終了した後のDBは暗号化結果内で回収する。

## 拡張観測と曲種フィルターの適用

Actions run `37210984228`では、追加した10起点を含む39起点の実観測を使用した。
全体の271曲候補・826本から、明示的なOff Vocal除外で270曲・825本をDBへ保存した。
Musicで2,974曲を読み込み、正常終了後も全PID・Genius ID・全SQLテーブルが一致した。
825本の参照は全てmetadata内で解決し、欠落先・欠落起点・未対応BLOBは0件だった。
通常プレイリスト3件・49項目の曲順、47音源のlocation、3起点の再生進行も確認した。

入力した3コアファイルとrunnerの読み込み前snapshotはバイト単位で一致した。
終了後のLibraryにはOff Vocal分類の曲へのGenius ID割当が0件で、HDD原本の
3コアファイルのSHAも作成元の記録と一致した。結果回収後に一時secretは削除した。

`build_real_library_experiment.py --native-template <verified-after-directory>`で、
原本のSHAに結び付いた照合と分類を維持しながら、Musicが保存した空の
Genius DBとLibrary形式を出力用に使える。読み込み・正常終了のreport、全PID、
解析済みmetadata、保存ファイルSHAを照合する。`--cluster-config`と
`--exclude-kind off_vocal`は共有グラフとDBの作成前に適用する。
この検証も、Geniusメニューによるネイティブ生成やiPodでの受け入れを証明しない。

GitHub ActionsのmacOS上でMusic.appそのものを動かした。Musicが保存した形式を使うと、実ライブラリ2,974曲・214曲分のGenius ID・関連性723本を読み込み、終了後も保持できた。音源47曲を接続し、3曲の再生進行も確認した。未サインイン環境ではGenius Playlistメニューは無効であり、実際のGenius生成は未確認。

入力は実ライブラリの実験コピー2,974曲、214曲のGenius ID、723本の関連性、3種のシードに対応する実音源47曲。元ライブラリは変更しない。選んだシードはSweden、only my railgun、星の声。

Musicでライブラリを明示的に開き、曲数とPersistent IDを確認する。転送した音源だけをMusicのlocationに設定して再生位置の進行を確認し、Genius Playlist操作を試す。サインインやGenius参加状態の変更は行わない。終了後のLibraryとGenius DBを回収し、IDとSQLテーブルの保持をローカルで比較する。元ライブラリのhfmaに記録されたアプリバージョンは1.5.6.11、runnerはMusic1.6.6。鍵導出に使うローカル実行ファイルが1.7でも、元ライブラリの保存バージョンは別である。読み込みが拒否された場合も実行結果として扱う。

## 転送

`scripts/secure_macos_probe.py`はAES-256-GCMで入力ZIPと回収ZIPを暗号化する。公開テスト用ブランチにはコードと暗号文の分割ファイルだけを置き、平文の曲名、音源、DB、スクリーンショット、コマンド出力は公開Artifactにしない。鍵を必要とするのは復号・暗号化を行う親プロセスだけであり、Music検証の子プロセスには鍵の環境変数を渡さない。

一時鍵はActions secretへの登録を想定する。実行中はGitHub runnerとworkflowを変更できるリポジトリ管理者が復号できる。検証終了後にsecretを削除する。初回の登録は自動承認レビューで拒否された。その後ユーザーから明示的な許可を得て、一時secretの登録を実施した。実験用の独立ブランチに暗号化入力とコードだけを公開する。

Artifactは暗号化結果ファイルだけ、保持期間3日。回収した結果はローカルのGit管理対象外data以下で復号する。YTMusic認証情報やAppleアカウントのCUIDを持ち込む手順はない。

## 結果の解釈

MusicがGenius DBを保持すること、実音源が再生できること、Geniusプレイリストが作成されることは別々に記録する。メニューが無効の場合は「生成不可」と記録し、エミュレーション結果をネイティブ生成の代わりにはしない。iPod実機での互換性はこの検証では未確認。

## 実行済みの結果

ユーザーの明示的許可後、暗号化入力を独立テストブランチへ送信し、macOS 26.6.2 / Music 1.6.6 / ARM64で実行した。

| 検証 | Actions run | 結果 |
| --- | --- | --- |
| 初回準備 | 37196806353 | Homebrew管理cryptographyとの競合でMusic起動前に停止。専用venvへ修正。 |
| 実験Libraryを別ディレクトリから明示的に開く | 37196917835 | Library曲一覧のAppleEvent取得が失敗。DBコピーが保持されただけでは読み込みの証拠にならない。 |
| 実験Libraryを標準Musicディレクトリへ配置 | 37197197383 | ネイティブ曲数0。終了後のLibraryは2,974→0曲、Geniusは214行→0行。実験コピーは初期化された。 |
| 実音源を新規取り込み | 37197627162 | Musicは47曲を取り込み、終了後のLibraryでも47曲。addの戻り値処理が失敗し対応表取得・再生は未確認。 |

元HDDのLibrary.musicdbのSHA-256が初期値と一致することも確認した。原本の変更はない。

追加DBの公開ブランチ送信は一度自動承認レビューに拒否された。仕組みを説明した後、ユーザーから「その方法で進めてよい」と明示的な許可を得て、未変更ライブラリの対照とネイティブ47曲への関連性再注入用DB（計約2.4MB）の暗号文を送信した。音源は最初に送った47曲を再利用する。

Appleの通常の利用条件は各MacでのGenius有効化、Apple Account、インターネット接続である。既存LibraryやDBのコピーだけでGeniusが利用できるとはまだ判断できない。
出典: https://support.apple.com/en-ie/guide/music/musbe3694c1b/mac

| 追加検証 | Actions run | 状態 |
| --- | --- | --- |
| 曲ID差分方式 | 37197964542 |47曲取り込み。曲情報のインライン変換が-1700で失敗。|
| 明示的なgetで取り込み後の曲情報を取得 | 37198400336 |47曲の対応表・曲数確認成功。Sweden / Only My Railgun / 星の声の3曲でplaying状態と2秒間の位置進行を確認。正常終了。音声出力の録音・試聴はしていない。|
| 未変更Library対照 | 37198779875 |2,974曲の読み込み、47音源の再リンク、3起点の再生進行、正常終了を確認。Musicの保存で内部形式が1.6.6.4へ更新された。|
| ネイティブ47曲へ関連性61本を注入して再読み込み | 37198788035 |47曲・Genius ID47件・全SQLテーブルが保持された。47音源再リンクと3起点の再生進行を確認。生成メニューは3曲とも無効。|

| 最終検証 | Actions run | 結果 |
| --- | --- | --- |
| Music保存後の全実ライブラリへ関連性を注入 | 37199265990 |2,974曲・214Genius IDs・723有向辺をロード。全PID/GID、Genius全SQLテーブルを終了後に照合し一致。47音源再リンク、3起点の再生進行、正常終了を確認。Genius Playlistメニューは3起点ともdisabled。|

## 旧Library書き換えの制約

旧1.5.6.11（hfma @12=0x320009）を直接書き直したコピーはMusic 1.6.6で空に初期化された。zlibとAESのローカルラウンドトリップは正しく、圧縮・暗号化ウィンドウの誤りは見つからなかった。旧ヘッダーの不透明な状態を保持して書き直すことが原因候補だが、各フィールドの意味や正確な原因は未確定。@92は一般的なCRC32/Adler32と一致せず、チェックサムとは断定しない。

Musicが実際に開いて保存した1.6.6.4（hfma @12=0x1f000c）のコピーを使えば、同じ全2,974曲への214 ID・723辺の注入を読み込めた。この経路を採用する。`encode_library`は既知の失敗した旧プロファイルを通常の書き換えで拒否する。コード側の明示的な`allow_unverified_profile=True`は旧形式のコーデック診断専用であり、Music互換を意味しない。

ネイティブ保存済みの全Libraryに観測済みデータを適用する準備ツール:

```sh
.venv/bin/python scripts/prepare_macos_real_rebase.py --full-library \
  --native data/macos-actions/run-37198779875/private/probe/after \
  --original 'data/real-library-experiment-v02/Music Library.musiclibrary' \
  --manifest data/native-real-input-v01/manifest.json \
  --executable /home/mizoyuzu/Music.app/Contents/MacOS/Music \
  --output data/new-native-full-overlay
```

実行後のネイティブ曲数・終了・ファイルSHAを要求し、Persistent ID集合が一致する場合に限って適用する。参加状態・アカウント設定を変えず、Geniusキーは保存しない。元のMusic保存済みLibraryも書き換えない。試験に使ったLibraryのバイト列と、暗号化前Geniusテーブルをこの保存済みツールで再現できることも確認した。

全ジョブ終了後、GitHub Actionsの一時secret `MACOS_PROBE_KEY`を削除し、secret一覧が空であることを確認した。元HDD Libraryは初期SHAに一致する。新規テストはコード検証であり、Musicの生成成功やiPod実機互換の証拠としては扱わない。
