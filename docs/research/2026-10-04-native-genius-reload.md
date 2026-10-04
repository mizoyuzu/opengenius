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
