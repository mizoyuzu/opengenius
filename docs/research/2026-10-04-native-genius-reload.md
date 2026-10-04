# 合成Genius DBのMusic再読み込み試験

## ローカルで確認できた範囲

Music 1.6.6がActions上で作った2曲の合成Libraryを入力にする。
本番Library、Apple ID、YTMusic認証情報、Mediaはアップロードしない。
`prepare_macos_genius_fixture.py`は曲名・曲数・3秒の長さ・元Genius IDが0であることを検査し、
元Libraryの展開と再圧縮が完全一致する場合だけ新しい実験コピーを作る。

Music 1.6.6のhfmaヘッダーではoffset128が0だった。1.7サンプルのファイル長の繰り返しと
両方を許可し、元の0を保存するようLibrary writerを修正した。
2曲にGenius ID `0x70000001/2`を割り当て、空のGenius DBにmetadata2行、similarities2行、
config1行を入れる。関係は合成音同士の相互リンクで、音楽推薦の実測値ではない。
SQLiteの変更はcommit後にserializeする。commit前のsnapshotでは挿入行を保持できなかった。

元のSQLスキーマを維持し、SQLite integrity_check、暗号化→復号の全テーブル比較が成功。
提供済みMusic 1.7.0.146の鍵導出を用いて、新規1.6.6のDBを処理できた。
鍵は表示・保存しない。1.7のMusicCoreエミュレーションも2曲のIDを選択した。
これらはMusicアプリの受理やiPod互換を証明するものではない。

## ネイティブ再読み込みと対照実験

`.github/workflows/macos-genius-reload.yml`は手動実行専用。
小さいgzip/base64の合成fixtureを入力し、使い捨てmacOS Actions VM上へ3つのDBを復元する。
既存Libraryがある場合は拒否する。入力サイズ・ファイル名・SHA・ヘッダーを検査し、
実行時間とartifact保存期間を制限する。AppleScriptでPID・曲名・長さを読む。
Music終了を確認できない場合は、回収DBを確定した保存結果として扱わない。

| 実験 | Library/Genius | 結果 |
|---|---|---|
| [37189463643](https://github.com/mizoyuzu/opengenius/actions/runs/37189463643) | IDと相互関係を追加 | COUNT 0、DBが変化、終了未確認 |
| [37189764068](https://github.com/mizoyuzu/opengenius/actions/runs/37189764068) | 無変更の元3DB | COUNT 0、DBが変化、20秒待っても終了未確認 |
| [37189996913](https://github.com/mizoyuzu/opengenius/actions/runs/37189996913) | 無変更の元3DB、bundleを明示してopen | COUNT 2、PID・曲名・長さ一致、終了未確認 |

無変更でも同じ症状だったため、最初の結果をGeniusデータの不正による拒否とは判断しない。
3ファイルだけの復元、ライブラリの選択方法、初回案内、元データの終了確認の不足を
先に切り分ける必要がある。両実験のGeniusメニューはUpdate Genius/Turn On Geniusが無効。
メニューが無効なだけでDB形式の互換性は判断できない。Genius生成操作は未実施。

無変更runの終了待ち画面は「Hear About New Music First」の案内だった。
既存GUI処理はbuttonだけを探してNot Nowを押せていないため、対象を限定して改善する。
元のimport run37186864359もMusic終了確認がなく、3DBのみをコピーしていた。
正常終了を確認した完全な合成bundleを再取得することが次の対照条件になる。

ローカルのartifactは`data/macos-actions/run-<ID>/`、入力は
`data/macos-actions/reload-fixture-v01/`と`reload-control-v01/`。
現時点のローカル全108テストが成功した。Actionsのdiagnosticジョブ成功と
互換性成功は別の判定として扱う。

## ライブラリ指定の切り分け

無変更fixtureに対して`open -a /System/Applications/Music.app <bundle>`を使うと、
ネイティブAppleScriptで元2曲を読み取れた。前のCOUNT0は復元bundleを選んでいなかった
可能性が高い。3DBのみでも読み込みは成功したが、初回案内による終了未確認は残った。
Appleの公式案内は[Option起動とChoose Library](https://support.apple.com/en-gb/guide/music/mus7663a920a/mac)。
今回のopen引数指定は公式案内との同等性を仮定せず、このランナーでの実測結果として扱う。

## 書き換えたDBの読み込み

[run37190148501](https://github.com/mizoyuzu/opengenius/actions/runs/37190148501)で
書き換えfixtureを明示して開き、COUNT2・元PID・曲名・3秒を確認した。
回収Libraryの2曲には`0x70000001/2`が残り、Genius.itdbは入力とバイト一致した。
ただしMusic未終了だったので保存完了後の保持とは扱わない。
このrunではwelcomeと操作許可で最初のGUI探索が終わり、その後promotionが現れた。
ネイティブquery後にも既知案内だけを閉じる探索を追加して再試験する。

新規import [run37190130510](https://github.com/mizoyuzu/opengenius/actions/runs/37190130510)は
2曲取り込み・Music正常終了の両方が成功し、7つのbundleファイルを回収した。
この結果から`reload-fixture-v02`も作成し、SQLite/暗号化/エミュレーションの検査が成功した。
元の無変更Libraryのdecode→encodeも完全一致する。

## プレイリスト生成の入口

手元Music1.7のSDEFにはGeniusを作る専用commandがない。
user playlist.genius、playlist.special kindはread-only。
[Apple公式](https://support.apple.com/en-ca/guide/music/musbe3694c1b/mac)が案内する
Songsの選曲→File→New→Genius Playlistが次のネイティブ生成テスト候補になる。
未ログインの合成VMでこのメニューが有効になるかは未検証。
DB保持と生成操作の有効化を区別し、Turn On Geniusやアカウントログインはこの試験では行わない。

静的文字列にmGeniusEnabledとmGeniusDataPresent、geniusCUID/geniusOptedOutCUID関連の
条件が別々に存在する。DBを追加するだけでUI有効化条件が満たされるとは限らない
という仮説の手掛かりで、必要な値や意味はまだ確定していない。

## 有効化状態の静的対応

手元のMusic1.7.0.146で確認したwriter:

- `0x1000ca0b0..0x1000ca0d4`: preferences object+0x30をBOMA500へ保存。
- `0x1000ca0d8..0x1000ca0fc`: object+0x40をBOMA501へ保存。
- assertion文字列から+0x30=geniusCUID、+0x40=geniusOptedOutCUIDに対応。
- `0x1000ca050`→`0x1000d66a8`、`0x1000d66d8..0x1000d66dc`:
  object+0x1cのbyteをplma+0x0cへ保存。
- Genius関連の状態チェック`0x1007be81c..0x1007be868`がobject+0x1cを1と比較。

提供済みGeniused prefsにはBOMA500あり、501なし、502あり、plma+0x0c=1。
新規合成prefsには500/501なし、502あり、同byte=0。
オフセット対応は静的コードで確認したが、このbyteの「Genius有効」命名は推定。
他のguardも存在するため、このbyteだけで生成できるとは限らない。
本番CUIDの値や鍵は記録せず、転送もしていない。

`reload-fixture-v02-enabled-flag`は、v02のPreferences展開offset284
(plma+0x0c)だけ0→1したコピー。再圧縮・暗号化後の完全な展開比較と
鍵ヘッダーの保持を検査した。Library/Genius DBは同一。

## GUIのクリックに関する対照

[37190570822](https://github.com/mizoyuzu/opengenius/actions/runs/37190570822)の無変更設定と
[37190644031](https://github.com/mizoyuzu/opengenius/actions/runs/37190644031)のbyte変更設定は
両方2曲を読み込めたが、Genius Playlistメニューは無効、Music終了も未確認だった。
OCRは案内とNot Nowを特定し、System Events click-atはそのstatic textを返したが、
終了待ち画面には案内が残った。AXクリック成功と案内の閉鎖は別の判定が必要。
モーダル画面の残存により、この段階ではbyte変更の効果を判断できない。
OCRで特定した座標へ実マウスdown/upを送る方式で同じ2条件を再試験する。

ローカルの監査ツール`audit_macos_genius_reload.py`はartifactのSHAとbefore/afterを
検査し、それぞれのPreferencesから鍵を導出して全テーブルを比較する。
曲PID/GID/曲名/長さの完全比較、正常終了確認、ネイティブ生成の試行を別項目にする。

## 正常終了後の保持とフラグ試験の結果

| 同じ実装・v02 fixture | Native COUNT | 正常終了 | 保存後GID/関係 | Genius Playlist |
|---|---:|---|---|---|
| [37190774520](https://github.com/mizoyuzu/opengenius/actions/runs/37190774520)、設定byte=0 | 2 | 確認 | 全曲情報と全Geniusテーブル保持 | 無効、生成操作なし |
| [37190781633](https://github.com/mizoyuzu/opengenius/actions/runs/37190781633)、設定byte=1 | 0 | 確認 | 曲とGeniusテーブルが空、byteも0へ | 曲の読み込み不成立のためスキップ |

Swift Visionで案内のタイトルとNot Nowを一意に検出し、CoreGraphicsの実マウスイベントで
閉じた。両試験でMusicの正常終了を確認できた。設定byte=0ではTurn On Geniusが有効に
なったが、選曲後のGenius Playlistは無効だった。Turn On Geniusは押していない。
保存後DBは入力SHAと照合し、before/afterをそれぞれのPreferencesの鍵で復号して
SQLite整合性と全テーブル一致を検査した。鍵は記録していない。

設定byte=1は同じfixtureの1byte変更だが、別VMで1回の比較なので原因の確定ではない。
少なくとも単純なbyte変更で有効化できたとは言えず、本番向け処理には採用しない。
CUIDなしの状態との不整合、バージョン差、その他のライブラリ検証条件が候補になる。

到達点は「未ログインMusic1.6.6で、合成ID対応と相互関係DBを読み込み、正常終了後も保持」。
「MusicのGeniusプレイリスト生成」および「iPod上の生成」は未達。
次はGenius有効状態の保存条件、またはiPodへ関係を渡す経路を調べる。
今回のコードに対するローカル全112テストとmacOS関連23テストが成功した。

### 後続試験による補足

ログ付き再試験37192878734では参加状態1でもCOUNT2になり、前回のCOUNT0は再現しなかった。
一方、正常終了後にはGenius IDが0、全Geniusテーブルが空、状態1→0になった。
単純な設定変更によるGeniusデータ消去は再現したが、曲数0をその直接効果とは断定しない。
128曲の参加状態0では正常終了後も全ID・全テーブルが保持された。
詳細は2026-10-04-larger-native-library.md。
