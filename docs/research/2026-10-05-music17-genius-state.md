# Music 1.7でのGenius状態リセットの静的解析

対象は提供されたMusic 1.7.0.146のARM64e実行ファイル。SHA-256は
`a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a`。
アドレスはASLR適用前。実MacのMusicは1.5.6なので、1.7のアドレスをそのまま実機へ適用できない。

## 結果と限界

有効化状態を0に戻すコードと、その保存経路を確認した。ただし、今回実Macで
どの処理が実行されたかは静的解析では確定していない。特に以前候補にした
Cloud Music Libraryの起動分岐は、今回の保存データをそのまま条件に当てはめると
説明しにくい。これを原因と決めて追加の設定値を書き換える根拠は得られていない。

実Macでは投入したDBから純正Genius生成に成功している。この事実と、以下の
状態管理は別の検証項目である。DBの暗号化失敗や推薦計算の失敗を原因とはしていない。

## 状態と保存先

`0x1007be81c`はLibraryPrefsを`0x1007da210(1, 0)`で取得し、
object+0x1cを**1と比較**する。アプリコンテキスト等のガードもあり、単に非ゼロなら
有効という判定ではない。以下のopt-outには状態2も登場する。

writerの`0x1000ca050..060`はplma+0x0cを渡して`0x1000d66a8`を呼ぶ。
`0x1000d66d8..6dc`がobject+0x1cをその先頭へコピーする。
同じserializerから以下の対応も読み取れる。

| メモリ上の位置 | 保存先 | 用途／根拠 |
| --- | --- | --- |
| +0x1c | plma+0x0c | IsGeniusOptedInの状態 |
| +0x20 | plma+0x10 | 起動分岐で読む32ビット値。フィールド名は未確定 |
| +0x64 | plma+0x36 | Cloud Library状態判定の入力 |
| +0x30 | BOMA 500 | geniusCUID |
| +0x40 | BOMA 501 | geniusOptedOutCUID |
| +0x70 | BOMA 510 | Cloud Library側のCUID。writerは0x1000ca140..164 |

## 起動時の解除経路

CloudLibraryManager生成処理`0x10028a160`の`0x10028a248..264`は、
primary vtableとして`0x101f15708`をobject先頭へ設定する。
Startup `0x10028ac4c`の`0x10028b318..338`はこのvtableの+0x78を呼ぶ。
対応する認証付きrebaseのtargetは`0x100299250`。

**この呼び出しはDoGeniusOptOutであり、適格性チェックのbooleanではない。**
ログ文字列`DoGeniusOptOut`と状態・CUID操作から確認できる。
呼び出しの返り値0は成功を表し、非ゼロは終了ステータスになる。

起動処理の該当部分を簡略化すると次のようになる。ここへ到達する前にも
アプリ／ライブラリの初期化ガードがある。

```text
if CloudLibraryEnabled():
    別のCloud Library処理へ
elif not IsGeniusOptedIn() or prefs.field_20 != 0:
    Startupのログ処理へ
else:
    status = manager.DoGeniusOptOut(0)
    if status == 0:
        if prefs.genius_state != 0:
            prefs.genius_state = 0
            request_preferences_save(1)
        notify_state_changed()
```

最後のclearは`0x10028b358`、保存要求呼び出しは`0x10028b360`。
CloudLibraryEnabled相当の`0x1002a50fc`はガードと+0x64を読み、
`0x1002a77fc`へ渡す。後者は+0x70が空ならfalse、非空なら
状態値0..7についてビット集合`0x7a`（1, 3, 4, 5, 6）を調べる。
7より大きい状態はこの部分ではtrueになる。**状態2はfalse**である。

DoGeniusOptOut `0x100299250`はopt-in状態かつCloud側CUIDが空であることを
要求し、そうでなければ-50を返す。引数0かつGenius CUIDが非空なら
500→501への移動と元CUID／+0x28のclearを行う。
`0x10029934c..370`では状態2、隣の+0x1dは0、+0x20/+0x24は0にして保存を要求する。
この後、Startup側が状態2→0へ進む。

## ほかのclear処理

| 関数 | フラグ書き込み | 周辺の副作用 |
| --- | --- | --- |
| 0x10028edd4 | 0x10028ef20で0 | Cloud Library終了処理。条件付きで500→501移動、500と+0x28を消去 |
| 0x100298ce4 | 0x100298db4で0 | DoGeniusOptInの開始。+0x20/+0x24等も初期化。開始処理だけでは有効状態にならない |
| 0x10029993c | 0x1002999b4で0、0x100299accで1 | opt-in完了系。入力CUIDとtrack-dataを検証してから再設定 |
| 0x100290ce0 | 0x100290ef0で1 | 受け取ったアカウント状態を取り込む。保存済みaccountIDとの相違では別のリセットも行う |

これらは同じLibraryPrefs accessorを使うSTRB+0x1cを走査して確認したもの。
これは全メモリ書き込みの網羅証明ではない。STRHによる状態2の書き込みも
上記opt-outに存在し、STRBだけの走査では拾えない。

## 実Macのスナップショットとの比較

変更前と正常終了後の保存データでは、+0x20相当は0、Cloud状態は2。
BOMA 500は非空、501はなく、**510の文字列も非空**。UTF-16文字列の長さ・境界と
非空判定まで確認した。全BOMA payloadは同一。
キーやCUIDそのものは記録・公開しない。

Cloud状態2はCloudLibraryEnabledをfalseにする一方、510が非空のままなら
上記DoGeniusOptOutは-50を返すのでStartupのclearには進まない。
さらに通常の解除が実行された場合の500→501移動も今回の保存結果にない。
したがって、この起動解除経路が今回の原因だとは言えない。
途中でCUID等が変わり後で戻る可能性や別の実行経路は、保存後の比較だけでは排除できない。

確定には実際の1.5.6でLibraryPrefs+0x1cへの書き込みと保存直前の値を
追跡し、呼び出し元を取得する必要がある。Macでのデバッガattach可否は未検証。
次の比較では起動直後・生成直後・終了直前を分け、ユーザー操作のない時間を
確保する。Appleへの有効化送信や実行ファイル改変は今回行っていない。

## 再現

```sh
.venv/bin/python scripts/trace_music17_genius_state.py /path/to/Music data/music17-state-trace
```

対象SHAの照合、constructorのvtable slot確認、上記11関数の逆アセンブルと
アドレス一覧を出力する。個人Libraryや認証情報は読み込まない。
