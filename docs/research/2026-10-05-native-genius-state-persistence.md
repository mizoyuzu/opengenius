# 実MacでのGenius有効化状態の保持

## 結果

Music 1.5.6／macOS 15.6.1で、有効化フラグを**Library.musicdbとLibrary Preferences.musicdbの両方**に設定すると、アイドル中・通常終了後・再起動後も状態1を保持した。再起動後、起点PIDを確認して純正Geniusを実行し、Swedenの18曲を取得した。

以前の実験ではPreferencesだけを変更していた。Library.musicdbにも同じplmaの状態フィールドがあることを見落としていた。片方だけの変更を、ネイティブ保存と両DBの整合性を満たす手順として扱ってはいけない。

今回の比較は、この不一致を解消することでリセットが止まることを示す。実際にPreferencesを書き戻したプロセスとその呼び出しスタックは未取得であり、特定の関数や補助プロセスによる上書きまで断定しない。以前のCloud Startup解除説を裏付けたわけでもない。

## 段階別の比較

各DBの展開オフセット284（plma+0x0c）を確認した。以下の「画面」は純正File → Libraryのメニュー。

| 試行・段階 | Library | Preferences | 画面 |
| --- | ---: | ---: | --- |
| Preferencesだけを変更、起動前 | 0 | 1 | — |
| 起動直後 | 0 | 1 | Update Genius／Turn Off Genius |
| 起動約30秒後、終了前 | 0 | 0 | Update Genius／Turn Off Genius |
| 通常終了後 | 0 | 0 | — |
| 補助プロセスを新しく起動、Preferencesだけを変更 | 0 | 1 | Update Genius／Turn Off Genius |
| その起動約33秒後 | 0 | 0 | — |
| 両DBを変更、起動前 | 1 | 1 | — |
| 起動約30秒後 | 1 | 1 | Update Genius／Turn Off Genius |
| 通常終了後 | 1 | 1 | — |
| 再起動後 | 1 | 1 | Update Genius／Turn Off Genius |
| さらに通常終了・再起動 | 1 | 1 | Update Genius／Turn Off Genius |
| 純正生成後の通常終了・再起動 | 1 | 1 | Update Genius／Turn Off Genius |
| 最後の再起動から約162秒後 | 1 | 1 | — |

Preferencesだけの試行は、終了時の解除ではなく動作中の保存で0に戻った。画面の有効状態はその間残っていた。デバッガなしでも、この段階の切り分けはできた。

SSHのLLDB attachは「非対話セッションではデバッグ許可を取得できない」と拒否された。ファイルアクセス追跡用のsudoもパスワードなしでは使えなかった。macOSの保護設定やデバッグ許可は変更していない。

AMPLibraryAgentはLibrary.musicdbを開き、MusicはGenius.itdbとExtras.itdbを開いていた。古い補助プロセスのキャッシュだけが原因かを調べるため、Music終了後、補助プロセスの停止を確認し新規起動したが、Preferencesだけの変更では同じリセットが再現した。最初の停止操作は対象プロセスが既に停止していたため実行せず、所有ユーザー確認を行った後の停止・再起動だけを有効な操作として記録した。

## 変更と監査

Musicとユーザー用AMPLibraryAgentの停止を確認し、変更直前の完全バックアップをMacに保存した。両DBは入力ハッシュを照合してから置き換えた。復号後の差分は**各DBで状態1バイトだけ**。Geniusの鍵ヘッダーは変わらず、Genius.itdbは変更していない。

Libraryの再暗号化には既存codecを使い、このMacが保存した1.5.6.11を同じMusicで試験する場合だけ`allow_unverified_profile=True`を明示した。一般的なlegacy拒否条件は変えていない。

保存後の全解析曲メタデータ・PID・Genius IDを変更前と比較し、2974曲すべて一致した。Genius DBも全SQL行が一致し、286曲・885有向関係・artist距離`[1,1,4,6]`を保持した。

生成確認では、最初の選択PID不一致とMusicが拒否したview setterの試行は成功に数えず、Geniusをクリックしていない。reveal後3秒待ち、selectionのPIDが起点と一致することを確認できた試行だけで純正生成を実行した。

再起動後のGeniusプレイリストは5件。既存4件の全PID順序は前回の検証結果と一致した。新しく生成された1件は18曲で、以前のSwedenの18曲と全PID順序が一致した。古い同名プレイリストだけを読んで生成成功と数えたわけではなく、新しいプレイリストPIDを区別して確認した。

完全バックアップは対象Libraryの親ディレクトリに以下の名前で保持する。

- `OpenGenius-before-state-timing-20261005`：今回の比較開始前。
- `OpenGenius-before-dual-state-20261005`：両DBの変更直前。

個人データ・SSH操作結果・全段階のコピーと監査はGit管理外の`data/native-state-timing-20261005/`に保存する。Appleへの公式有効化・更新操作、実行ファイル改変、実行中メモリの変更は行っていない。通信遮断は前回同様ユーザー申告のLuLu設定を前提とし、独立したパケット監査は行っていない。

## 再利用する準備手順

`scripts/prepare_music_genius_state.py`は、既存の関連性DBに対するローカル試験用に、両DBの状態を揃えた**別ディレクトリのコピー**を作る。公式の有効化手続きや新しいGeniusデータの取得は行わない。出力が既存の場合、状態2などの移行状態、鍵ヘッダー不一致、未対応のヘッダーは拒否する。出力を作る前に暗号化往復と差分を検証し、鍵値を含まないmanifestを保存する。

```sh
.venv/bin/python scripts/prepare_music_genius_state.py \
  /path/to/stopped-library-copy.musiclibrary data/local-genius-state \
  --allow-unverified-profile
```

最後のオプションは今回の1.5.6.11に対する明示指定である。既存codecが通常対応するプロファイルでは不要。実Macで動いているファイルへのインストールはこのツールでは行わない。適用する際はMusicとライブラリ補助プロセスの停止、完全バックアップ、両ファイルの照合を別途行う。

このツールを実データで実行し、検証に投入した両DBと出力バイトが完全一致した。オプションなしでは従来どおりlegacyを拒否し、出力ディレクトリを作らないことも確認した。

今回の保持確認は短時間の起動・終了サイクルと純正生成についてのもの。長期の運用、アカウント切り替え、OS更新、Apple通信の再開時の状態保持、iPodへの引き継ぎは未確認。
