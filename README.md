# GitHub ActionsでMusic.appの実動作を調べる

## 初回の実験

GitHub-hosted macOS 26 ARM64ランナーで、OS・MusicのバージョンとGUI状態を調べる。
Music.appはランナー標準のものを使い、macOS 27から取得したアプリを持ち込まない。
新しい合成WAVを2曲だけ生成し、AppleScriptによるMusicへの取り込みを試す。
本番Library、Media、Cookie、Apple IDは送らない。

実験は`.github/workflows/macos-music-probe.yml`。手動実行時に
`macos-26`または`macos-26-intel`を選べる。最大10分、artifact保存期間3日。
公式checkoutとupload-artifactだけを固定コミットで使用する。

`probe_macos_music.py`はmacOSかつActions環境のみで動作する。
既存Music Libraryを検出したら起動と取り込みをスキップする。
各操作に上限時間を設け、MusicへのAppleScript、System EventsのUI確認、
スクリーンショット取得の結果を分けて記録する。
取り込み後にMusicを終了し、新規LibraryのDBを回収する。
DBの実際の生成有無と取り込み成否をartifactで判断する。
合成音源はGeniusの既知曲ではないので、この実験は推薦品質の評価ではない。
また、probeは外部APIを呼ばないが、Music自体の通信を遮断・監視した実験ではない。

実行後の読み方:

- report.json: OS/build、操作の成否・エラー・タイムアウト。
- music-screen.png: 初回画面、権限ダイアログ、Musicの表示を確認。
- generated-library/: 新規LibraryのDB。既存Libraryのコピーはしない。
- synthetic-media/: 再現用の小さいWAVのみ。

## GUIと遠隔操作

公式イメージ作成コードにはGUI自動ログインとTCC権限の事前設定がある。
System EventsへのAppleEvents、Accessibility、画面キャプチャの設定は確認できるが、
Musicへの直接AppleEventsが今回のランナーで通るかは未検証。
このため、初回はVNCを導入せず画面と操作結果を回収する。

- [macOS 26 ARM64イメージ定義](https://github.com/actions/runner-images/blob/main/images/macos/templates/macOS-26.arm64.anka.pkr.hcl)
- [自動ログイン設定](https://github.com/actions/runner-images/blob/main/images/macos/scripts/build/configure-autologin.sh)
- [TCC設定](https://github.com/actions/runner-images/blob/main/images/macos/scripts/build/configure-tccdb-macos.sh)

権限確認や初回画面で止まる場合は、スクリーンショットを見てSystem Events操作を追加する。
それでも遠隔操作が必要なら、限定された接続経路でSSHとVNCを組み合わせる。
Tailscale公式Actionは候補だが、tailnet/OAuthまたはOIDCの設定が別途必要。
公開の無認証VNCは作らない。
AppleはmacOS 12.1以降にkickstartでScreen Sharingを有効化できないと説明しているため、
従来のコマンド一発で有効化する方法を前提にしない。

- [Tailscale公式Action](https://github.com/tailscale/github-action)
- [Apple Remote Desktopのkickstart制約](https://support.apple.com/en-bw/guide/remote-desktop/apd8b1c65bd/mac)

## 次に調べること

取り込みとDB生成が動いたら、新規Libraryの形式と鍵ヘッダーを調べる。
そのランナーのMusicビルドに対応する鍵導出が必要であり、既存のmacOS 27用
アドレスやバイナリ固定エミュレータが使えるとは限らない。
その後、合成曲のGenius IDと互換DBを対応付けた実験を作り、
Music.appが読み込むか・更新時に上書きするかを検証する。
既知曲による推薦品質やiPod動作の検証は別段階。

戻った`/mnt/temp-hdd`には本番とGeniused Libraryがある。
初回実験には不要なのでMediaの圧縮・アップロードはしていない。

## 初回実測結果

[Actions run 37186278435](https://github.com/mizoyuzu/opengenius/actions/runs/37186278435)、
検証コードのみの独立`macos-probe`ブランチで実行。ジョブは1分6秒で完了。

- OS: macOS 26.6.2 (25G83)、ARM64。Music 1.6.6。
- GUI console userあり。System EventsのUI有効、Music window 1。
- スクリーンショット成功。
- 音源取り込みは55秒でAppleEvent timeout。画像にはhosted-compute-agentによる
  Music操作の許可ダイアログ（Allow）とMusic初回画面（Start Listening）が表示された。
- 新規LibraryとGenius.itdb、Library Preferences.musicdbを回収。
  Libraryを既存パーサで読むと曲数0で、取り込みの成功は確認できない。
- Genius.itdbは32,768 bytes、8ページ、reserve 12。Music 1.7.0.146の既存鍵導出を
  用いたオフライン復号でSQLite integrity_check成功。5テーブルすべて0行。
  Geniusの5テーブルのSQL定義は提供済み1.7由来DBと一致した。
  鍵導出コードのビルド間互換の一例であり、全ビルド対応を保証する結果ではない。
- ローカルartifact: `data/macos-actions/run-37186278435/`。
  復号コピーは`data/macos-actions/run-37186278435-decrypted.itdb`。鍵は保存・表示していない。

戻った本番LibraryのSHA-256も保存済み曲一覧と一致した。読み取り確認のみ。

## GUI処理の修正と取り込み成功

2回目 [run 37186554307](https://github.com/mizoyuzu/opengenius/actions/runs/37186554307)では
Start Listeningを押せたが、Music version取得は取り込みの操作許可を先に出す方法として
不十分だった。GUI探索は25秒で打ち切られ、実際のadd時に許可ダイアログが出て
取り込みは再び失敗した。背後には「Hear About New Music First」の案内も表示された。

3回目 [run 37186864359](https://github.com/mizoyuzu/opengenius/actions/runs/37186864359)では
実際のimportを非同期で1回だけ開始し、その待機中にGUIを処理する形へ変更した。
ジョブは32秒で完了。操作許可とStart Listeningが成功し、importの返値も
`synthetic import complete, 2`になった。回収Libraryを既存パーサで読むと
2曲、各3,000ms、異なるPID、Genius IDは両方0だった。
パーサが読む曲名はsynthetic-tone-1/2で、設定した追加ラベルの保存は確認していない。

GUI探索は既知7プロセスに限定し、各AppleEventに2秒上限、探索全体を時間制限する。
hosted-compute-agentとMusicの両方を含むダイアログのAllowだけを許可する。
Start ListeningもMusicプロセスに限定。Musicの既知案内のNot Nowを操作する処理も
あるが、3回目の画像ではその案内は残っており、通過できたとは判断しない。
画像取得だけでなく、実際の取り込みとDB生成が成功した点を区別する。

3回目のartifactは`data/macos-actions/run-37186864359/`。
11関連テストがローカルとmacOS Actionsの両方で成功。
artifactアクションを公式v6の固定SHAへ更新し、実操作の成否をジョブ概要へ表示する。

現在の結論: VNCなしでMusicの起動・初回操作許可・2曲取り込み・DB回収が可能。
Genius DBはまだ関係を持たない空DBで、推薦生成や互換DBの受理は未検証。
次はこの新規Libraryに2曲の関係とID対応を与えて再読み込みを調べる。

## 合成Genius DBの再読み込み結果

[run 37190774520](https://github.com/mizoyuzu/opengenius/actions/runs/37190774520)で、
2つの人工音源にGenius IDを割り当て、相互関係を入れたDBをMusic1.6.6へ読み込ませた。
bundleを明示して開くと2曲のPID・曲名・長さが一致し、正常終了後も割り当てIDと
Genius全テーブルが保持された。既存ライブラリやアカウントは持ち込んでいない。

Genius Playlistメニューは無効だったため、アプリでの生成やiPod動作はまだ未検証。
候補の有効化byteだけを変えた別の合成試験では曲とDBが空になった。
この単純な変更は採用せず、保存データとGenius有効化状態を区別して調べる。

初回案内はVision OCRで既知タイトルとNot Nowを特定し、CoreGraphicsの実マウスイベントで
閉じる。新規合成bundleだけを回収し、Music終了確認を別項目で記録する。

## 128曲へ拡大

[run37192872815](https://github.com/mizoyuzu/opengenius/actions/runs/37192872815)で
128曲・4,096関係・20ページの合成Genius DBを検証した。
全曲を読み込め、正常終了後もPID/GID/曲名/長さと全Geniusテーブルを保持した。
`synthetic-128.json.gz`は新規合成音源だけの再実行用fixture。
Genius Playlistは無効のままで、生成や推薦品質の検証は別段階。

参加状態1の2曲再試験では曲を読み込めたが、終了後Genius IDと関係DBが消え、
状態は0へ戻った。前回のCOUNT0は再現せず、曲消失とGeniusデータ消去を区別する。
統合ログは既知メッセージ件数だけを返し、生ログや値は保存しない。
