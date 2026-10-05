# 同じアーティストの軽い優先

ユーザーの指定に合わせて、既存の選曲プロファイルへ独立した `artist_preference=slight` を重ねる。Webの新規生成では slight が既定で、条件の「同じ歌手」を「標準」にすれば neutral に戻せる。過去の保存結果に指定がなければ neutral として読み込む。CLIは再現性を保つため neutral が既定。

Musicの観測済みdistanceフィルタ（type 3、metadata index 1）の初期間隔だけを6から5へ下げる。最低間隔、乱数幅、アルバム、同一曲、skip、jitterの設定を維持する。正の類似度スコアを加えるものではなく、同じartistグループの再選択を避ける間隔を一段緩める操作。artistグループは既存の正規化された完全なクレジット集合を使い、部分一致やブランド全体の同一視は加えない。relations-onlyにはartistフィルタがなく、変更は効かない。

## 保存済み実データの比較

追加の外部通信なし。45起点、286曲、885有向関係、Off Vocal除外、artist-album-minimum-oneを維持し、同一のMusic ARM64実バイナリを限定エミュレーションして比較した。原本ライブラリを変更せず、Music.appのGUIやiPodでの動作検証ではない。

| 指標 | neutral | slight |
| --- | ---: | ---: |
| 生成曲数（起点込み） | 841 | 841 |
| 起点以外で起点と同じartistグループの曲 | 257 | 271 |
| 同じartistグループが隣接する組 | 213 | 213 |

45起点のうち21で選択された曲または順序が変わり、14で起点と同じartistグループの曲が1曲増えた。例えば「青空」/noctchillの同じ歌手の曲は2から3へ増え、「Catch the Breeze」が選ばれた。「星の声」/SHINY COLORSも2から3へ増え、「Migratory Echoes」が選ばれた。すべての起点で増加する保証や、推薦品質の主観評価を示す数字ではない。

個人データを含む比較入力・出力はGit管理外の `data/music-review/artist-adjustment-before-20261005.json`、`data/music-review/artist-adjustment-latest-20261005.json` に保存。新しいAPIの45起点の結果は事前の直接エミュレーション結果と全PID順序が一致し、旧画面の結果も比較元と全PID順序が一致した。

設定は結果全体と各起点、および実際に使ったconfigのSHA-256へ記録する。画面で優先設定を切り替えた場合、保存済みの結果との条件差を表示し、再生成で反映する。

## 検証

- artistの初期間隔だけが変更されること、neutralのバイト一致、最低間隔、relations-only、未知設定の拒否を検証。
- APIの既定slightと明示neutral、設定の保存、未知設定の拒否を検証。
- 実Musicバイナリを含むテスト一式：215件成功。
- ブラウザで45起点とslightの復元を確認。neutralへの変更で再生成表示が出て、slightへ戻すと消える。JavaScript実行エラーなし。

## 二段目の調整

続くユーザーの指定で `moderate` を追加した。初期間隔は元の6から4となり、slight（5）からもう一段だけ緩める。Webの新規生成の既定をmoderateへ変更し、画面に「やや優先」を追加した。neutralとslightの意味・設定値は維持し、既存の保存結果はその指定を復元する。CLIの既定もneutralのまま。最低間隔、アルバム、同一曲、乱数幅などの条件は変更しない。

二段目の入力・結果・集計はGit管理外の `data/music-review/pre-artist-second-step-20261005.json`、`artist-second-step-latest-20261005.json`、`artist-second-step-metrics-20261005.json` に保存する。

45起点の比較では、slightからmoderateへの変更で22起点の曲または順序が変わった。総曲数841は維持し、起点と同じartistグループの起点以外の曲は全体で271→270、同じartistグループの隣接は213→215。先頭20曲に限ると同じartistグループの起点以外の曲は241→253となったが、先頭10曲では137→136だった。全体やどの長さでも増加するわけではない。距離フィルタの調整は経路と順序にも作用するため、単純なスコアの単調増加として扱わない。

二段目でも実Musicバイナリを含む215件のテストが成功した。
