# 大きい合成Libraryでの互換データ検証

## 条件

2曲の検証に続き、128曲の3秒WAVをMusic1.6.6へ一括取り込みする。
Musicが新規作成した合成Libraryだけを回収し、本番Mediaやアカウントは送らない。
取り込みとネイティブ照会には時間上限を設け、Musicの終了を確認してから比較する。
曲名はsynthetic-tone-1..128。各曲の周波数は共通関数で決め、復元時に同じ音源を作る。

Genius用の相互関係は、各曲から次の32曲へつながる循環グラフ。
2曲なら残り1曲へつなぐ。Genius IDはPID順に割り当てる。
これはサイズ・ページ・曲ID対応の試験であり、推薦品質の評価ではない。
全曲のPID/GID/曲名/長さ、SQLite全テーブルを保存後と比較する。
アプリでのプレイリスト生成は別の結果項目にする。

128曲では暗号化DBを含むpayloadが手動実行入力の上限を超える可能性がある。
その場合は新規合成fixtureのみを検証用ブランチのfixtures/macosへ置く。
許可する名前はsynthetic-128.json.gzに限定し、
単一gzipストリーム、展開サイズ、DB SHA、ヘッダー、曲一覧を検査する。
各DB1MiB、展開fixture5MiB、曲数128を上限にする。
元の小さいinline入力は60,000文字・展開350,000bytesの上限を維持する。

## 有効化状態についての追加解析

Music1.7の参加状態チェックはprefs+0x1Cの値1を要求する。
DoGeniusOptOutログを含む関数0x100299250では、この場所へ2を保存し、
CUIDをopted-out CUIDへ移し、prefs+0x28をゼロにする。
したがってplma+0x0Cの値は単純booleanより参加状態を表すと考えられる。

prefs+0x28はserializer0x1000d66d0..0x1000d66d4でplma+0x54へ保存する。
提供済みGeniused Libraryは非ゼロ、人工Libraryはゼロ。
アカウント関連IDの候補だが意味はまだ確定していない。実値は表示・保存していない。
CUIDも架空値を加えず、本番値を転送しない。

prefs+0x1Eはplma+0x0Dへ保存し、Genius DB openの0x1001109A4が検査する。
非ゼロ時は0x1001117DCの「genius corruption detected; deleting db」経路へ進み、
マーカーをクリアしてDBを作り直す。前回の人工参加状態1の試験で前後0→0だった
ことだけでは、途中の破損判定を否定できない。
ネイティブログは既知メッセージの件数だけを収集して原因の切り分けに用いる。

## 128曲の取り込みとオフライン検査

[run37192725306](https://github.com/mizoyuzu/opengenius/actions/runs/37192725306)で
128曲の一括取り込みとMusic正常終了が成功した。元Libraryは41,004bytes。
7つのbundleファイルを回収。ローカルのdecode→encodeは元Libraryと完全一致した。
前のrun37192609191はmacOSの/var→/private/var一時パス差によるテスト失敗で、
Music起動前に停止した。テスト側の期待パスをresolveして再実行した。

Genius IDを128個、metadata128行、similarities128行、config1行を追加した。
関係数4,096、生成Genius DBは81,920bytes（20ページ）。Libraryは41,137bytes。
SQLite整合性、暗号化→復号の全テーブル一致が成功した。
MusicCoreエミュレーションではID1/32/64/128を起点に、それぞれ25曲を取得し、
起点の包含・重複なし・すべて既知IDを検査した。
ネイティブアプリでの生成成功を意味する検査ではない。

fixtureは128,256bytesのgzipで、inline換算171,008文字と上限を超えたため、
合成専用fixtureファイルとして検証ブランチへ保存した。
ローカル生成物はdata/macos-actions/reload-fixture-128-v01/。
再実行用fixtureはfixtures/macos/synthetic-128.json.gz。

## ネイティブ128曲の結果

[run37192872815](https://github.com/mizoyuzu/opengenius/actions/runs/37192872815)で
COUNT128、全曲PID・曲名・3秒の一致、Music正常終了が成功。
回収ファイルのSHAを照合した後、before/afterを各Preferencesで復号して監査した。
128曲のPID/GID/曲名/長さとGenius全テーブルが完全に保持された。
Genius Playlistメニューは無効で、生成操作は行われなかった。

ログ要約はcomplete=true、Genius関連5イベントすべて未分類。
既知のDB破損・鍵/CUID assertion・選曲エラー件数は0。
これはログが得られた範囲の観測であり、その条件が内部で一切発生しなかった証明ではない。

## 参加状態1のログ付き再試験

[run37192878734](https://github.com/mizoyuzu/opengenius/actions/runs/37192878734)は
前回と同じ2曲の参加状態1 fixtureを使用した。
今回はCOUNT2とMusic正常終了が成功したが、保存後Genius IDは両方0、
全Geniusテーブルは0行、参加状態は1→0だった。メニューは無効。
したがって前回のCOUNT0は再現しなかったが、Genius ID/DBの消去は再現した。
曲自体の消失とGeniusデータの消去は別の観測として扱う。

ログ要約complete=true、Genius関連6イベントはすべて未分類。
今回の分類では破損・assertion件数0で、消去原因の特定には至らない。
今後は静的文字列DoGeniusOptIn/DoGeniusOptOutも既知分類に含める。
今回のログからその分類を遡って判定することは、生ログを保存していないためできない。
状態1とCUID/アカウント関連情報の不足によるopt-out/正規化は仮説のまま。
フラグだけの変更は本番処理に取り入れない。

ローカル全122テストが成功。Actions上でも関連テストと128曲のネイティブ検査が成功。
本番2,974曲のMusic/iPod動作や推薦品質を保証する結果ではない。
