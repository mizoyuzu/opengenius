# 出方向の観測を増やすオフライン計画

`--attempt-summary PATH` を繰り返し指定すると、そのLibraryの検索保留履歴を参照し、`search_unresolved` のPIDを追加候補から除く。重複する履歴はPIDでまとめ、他のstatusは保留に使わない。履歴なしは従来どおり。再検索を意図する場合は該当履歴を渡さない。schema 1、Library SHA、results内の既知PIDを要求し、SHAを欠く過去のsummaryを自動で信用しない。collectorは今後のsummaryへLibrary SHAを保存する。入力SHAと除外数は監査に残る。

2026-10-05。`scripts/plan_ytmusic_expansion.py` は評価済みの有向関係から、関連先として一致したが起点として未観測のLibrary曲を選ぶ。既存の `probe_ytmusic_batch.py --seed-plan` が受け付けるPID計画を出力する。HTTP、認証情報の読み出し、Apple DBの変更は行わない。

現行の保存済みmoderate評価を再集計した結果、全2,974曲に対し関係に参加する曲286、出方向を観測した起点45、関連先のみ241、有向関係885だった。全曲に対する割合はそれぞれ9.62%と1.51%。起点45件のうち25曲生成は23件、4曲生成は3件である。これは保存された観測と限定コアの評価範囲であり、未観測曲にサービス上の関係がないとは言えない。

計画は、目標曲数未満の既存起点から有向辺で到達できる候補を優先する。目標は起点を含む25曲が既定で、`--playlist-target 1..100` で明示できる。評価時の件数上限とは別に指定する値なので、低い上限で打ち切られた評価を使う場合は不足と混同しないよう揃える。優先度は到達する各短い起点の「目標−生成曲数」の合計、到達する短い起点数、候補への入次数の順とする。この値は取得順の判断材料であり、増える曲数や推薦の質の予測ではない。候補は正規化されたアーティストクレジット集合ごとの順番待ちに分け、全集合に一巡するまで同じ集合の2曲目を取らない。同じ集合の中では、すでに選んだアルバムと異なる候補を優先する。作品タグは明示設定だけを使い、ジャンルや歌唱/BGMを推測しない。これは観測済みグラフにつながる曲の計画であり、Library全体の代表標本ではない。

`--exclude-kind off_vocal` は指定された分類に従って候補を除く。分類設定がない状態で除外条件を指定すると停止する。unknownは除外しない。タイトル、アーティスト、アルバム、時間を欠く候補は計画に加えない。既存の動画IDを計画へ転記せず、取得時に通常の検索とmetadata候補選択へ渡す。対応表や版違いの判定、既存batchの通常seed選択、HTTP上限・間隔は変更していない。

```sh
.venv/bin/python scripts/plan_ytmusic_expansion.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --evaluation data/music-review/artist-second-step-latest-20261005.json \
  --cluster-config data/music-clusters-experiment-v01.json \
  --exclude-kind off_vocal --seeds 10 \
  --output data/ytmusic/next-expansion-plan.json \
  --audit-output data/ytmusic/next-expansion-audit.json
```

出力監査には入力・plannerのSHA、対象曲数、除外理由、選択アーティスト/アルバム数、短い起点への接続、既存生成曲数分布を保存する。個人の曲名とPIDを含むため計画・監査は非公開のdata配下に置く。出力は上書きしない。候補数が要求数に満たない場合は実際の選択数を保存するので、batchの `--seeds` はその数に合わせる。取得を実行する場合は通常のbatchの認証確認とリクエスト上限が引き続き適用される。

実データでは241候補から10アーティストクレジット集合・10アルバムの10曲計画を生成できた。その後の[8曲の追加観測](2026-10-05-observed-outgoing-expansion.md)では推薦曲数と集中の両方が増え、長さだけで改善と判断できないことが分かった。7件のテストでクレジット別名の統合、短い起点の優先、既観測起点の除外、曲種除外、stale/重複データの拒否、明示目標曲数とbatch互換を確認した。
