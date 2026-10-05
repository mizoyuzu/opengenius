# 実MacでのLibrary/Genius書き込み

ユーザーが指定したMacと対象ライブラリに、変更前の完全バックアップ、Musicの正常終了、一貫した終了後バックアップ、入力/転送ハッシュ照合、2ファイルの対での書き込みを行った。実行環境はARM64、macOS 15.6.1、Music 1.5.6。個人データ・ホスト情報・操作結果はGit管理外の `data/remote-mba-20261005/` に保存する。

## ネイティブ保存の確認

対象の2974曲の全PIDと解析した全曲メタデータはFinal Libraryと一致し、書き換え前のGenius IDは全て0、Genius DBは全テーブル空だった。Musicが保存した1.5.6.11形式のコピーを、同じMusic版への互換性実験としてGenius IDのスロットだけ編集した。一般的なcodecのlegacy拒否条件は変更せず、実験でのみ `allow_unverified_profile=True` を明示した。

監査済みの45起点、286曲、885有向関係を使い、artist-album-minimum-oneとmoderate（artist距離 `[1,1,4,6]`）をGenius DBへ入れた。Mac固有の既存Preferencesの鍵ヘッダーから導出した鍵で再暗号化し、暗号化往復と全SQL行一致を確認した。Preferencesの有効化状態は書き換えていない。

Musicは変更後2974曲を読み込み、通常終了後のLibraryの全曲メタデータと割り当てた286のGenius IDが期待値と一致した。保存後のGenius全テーブルも期待値と一致し、885関係とmoderate設定を保持していた。この確認は純正Geniusプレイリスト生成の成功を意味しない。

## GUIセッションと有効化

SSHからの直接AppleScriptはタイムアウトした。GUIユーザーのbootstrap domainへ一時LaunchAgentを登録する通常の操作経路でMusicの読み取り・終了ができた。補助アクセスは最初 `-1719` で拒否され、ユーザーがmacOSのアクセシビリティ許可を付与した後にメニューを読み取れた。

現在のFile → Libraryは `Turn On Genius` が有効、New → Genius Playlistは無効だった。AccountメニューはSign Outが存在し、Sign Inは存在しなかった。アカウント識別子は取得・記録していない。書き換え前の最初のバックアップ、正常終了後バックアップ、書き換え後の保存DBともにPreferencesの `plma+0x0c` は0だった。過去の静的解析でこの値を有効化状態と関連付けているが、完全な条件は未確定である。今回の書き込みによって1→0になった証拠はない。

公式のGenius有効化はライブラリ情報のAppleへの送信を伴うため、ユーザーの確認待ちとした。[AppleのGenius規約](https://www.apple.com/legal/internet-services/itunes/us/genius.html) と [Musicユーザーガイド](https://support.apple.com/en-ca/guide/music/musbe3694c1b/mac) を参照。公式の有効化や更新でローカルの関連性DBが変更される可能性もあるため、原本と実験DBのバックアップを区別して保持する。

## 通信遮断下のローカル有効化と純正生成

ユーザーはAppleへの送信を伴う公式有効化を選ばず、その後LuLuでAppleとの通信を遮断済みであると明示してローカル試験を許可した。この通信遮断はユーザー申告を前提とし、こちらでパケット単位の独立検証はしていない。公式のTurn On GeniusやUpdate Geniusは押していない。

最新の完全バックアップとMusicの終了を確認し、Preferencesの展開オフセット284（plma+0x0c）だけ0→1にした。復号後の差分はこの1バイトのみ、鍵ヘッダーも同一。最初に作った別コピーは `open -a Music <copy>` ではMusicの読み込み対象に選ばれず、lsofで元Libraryを開いていると判明したためその試行を有効な比較に数えていない。その後、バックアップのある実際の対象Libraryに設定1バイトだけを変更して試験した。

実際の対象Libraryは2974曲を読み込め、File → LibraryはUpdate Genius/Turn Off Geniusに変化した。Swedenと星の声では純正New → Genius Playlistを実行し、Musicの `every user playlist whose genius is true` で実データを取得できた。青空の初回は別の曲の選択が検出されたため生成成功としなかった。起点PIDが実際のselectionと一致することを確認する操作で再試行し、青空からの生成を確認した。

| 実際に操作した起点 | 純正Genius曲数（起点込み） |
| --- | ---: |
| Sweden / C418 | 18 |
| 星の声 / SHINY COLORS | 50 |
| 青空 / noctchill | 25 |

SwedenはWebのmoderate・25曲上限での18曲と全PID順序が一致した。星の声はMusicで50曲が生成されており、25曲上限のWeb結果との同一性は主張しない。すべての取得曲PIDは投入した286曲のID集合内だった。記録時には操作記録外のWith youのGeniusプレイリスト（25曲）も存在したため、こちらが生成した3起点とは区別する。

原本の完全バックアップは `OpenGenius-backup-consistent-20261005`、フラグ変更直前の状態は `OpenGenius-before-active-flag-20261005`、ネイティブ生成後の完全コピーは `OpenGenius-native-success-20261005` として対象Libraryの親ディレクトリに保持した。

## 有効化状態の保持の限界

Musicの正常終了後も2974曲の全解析メタデータ・286のGenius ID・Genius全SQL行・885関係・moderate設定を保持した。再起動後も4つのGeniusプレイリストの全PIDと曲順は完全一致した。ただし、正常終了後に取得したPreferencesのplma+0x0cは0となっており、再起動後のメニューはTurn On Geniusに戻った。1バイトだけの変更で有効化を定着させたとは言えない。CUID、鍵ヘッダーを含む全BOMA payloadはフラグ変更前と一致しており、別CUIDへの置換やGenius DBの破損ではない。外部からのGUI操作の可能性も排除していないため、保存時に戻る具体的な実行経路の断定は避ける。

実際のMusic 1.5.6 ARM64コードを読み取り解析した。IsGeniusOptedIn相当の0x100c67414がLibraryPrefs object+0x1cを1と比較することを確認した。Cloud Music LibraryのStartup処理0x1006d3a2cにも、仮想メソッドの条件が偽の場合にobject+0x1cを0にして保存要求を行う分岐（0x1006d406c）がある。この分岐が今回実行されたことはまだ確認できていない。opt-out系等の別のclear処理も存在する。実バイナリ改変、実行中メモリの変更、公式の有効化/更新操作は行っていない。

現在の対象Libraryには投入した関連性データと生成済みGeniusプレイリストが残っているが、再起動後の有効化状態は0だった。恒久的なローカル有効化は未解決であり、成功済みとは扱わない。すべての一時GUIジョブは解除済み。
