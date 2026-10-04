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
