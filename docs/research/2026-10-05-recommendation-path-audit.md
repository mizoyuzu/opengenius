# 推薦曲数と経由曲の集中を分けて確認する

追加観測でプレイリストが長くなっても、少数の経由曲から同じ群へ広がっている場合がある。`audit_recommendation_paths.py` は保存済み評価の各曲の `observed_relation_path` を実際の有向辺と照合し、最初の経由曲への集中、hop分布、アーティスト対応表を使った歌唱者集合の集中を別々に報告する。音楽的な良さやジャンルを採点するものではない。

```sh
.venv/bin/python scripts/audit_recommendation_paths.py \
  --evaluation /path/to/evaluation.json \
  --track-snapshot /path/to/tracks.json \
  --identity-map /path/to/identity-map.json \
  --output /path/to/new-path-audit.json
```

`evaluate_ytmusic_batch.py` の共通ID比較レポートでは、`--side baseline` または `--side expanded` を加える。原本と既存出力は変更せず、HTTPやMusicの起動も行わない。Libraryのハッシュ一致を要求し、入力ファイルと監査ツールのハッシュを保存する。

`largest_gateway_verified_nonroot_share` の分母は、保存経路を照合できた起点以外の曲数。経路がない曲は `missing_path_count` へ入り、経路を推測して補わない。不正な辺、hop数の不一致、重複曲は拒否する。同じ曲に別の到達経路が存在する可能性があるため、経由曲の集計は保存された経路についての説明であり、唯一の原因を証明するものではない。

`largest_known_nonroot_artist_credit_share` はアーティスト名がある起点以外の曲を分母にする。欠損は別に数え、空の名前を一人のアーティストとして扱わない。同一歌唱者集合が多いこと自体は悪い推薦の証拠ではない。

## 実データの確認

2026-10-05の追加8観測を含む53起点の保存評価で、1115件の経路を照合でき、欠落は0件だった。監査は保存済みメタデータに基づき、録音の同一性や実Musicでの新データの受け入れを検証していない。

| 起点 | 曲数 | 起点／直接／間接 | 最大経由曲の割合（起点除外） | 最大同一歌唱者集合の割合（起点除外） |
| --- | ---: | --- | ---: | ---: |
| Beautiful / Superfly | 25 | 1 / 3 / 21 | 星の声 17/24 = 70.8% | 4/24 = 16.7% |
| Sweden / C418 | 18 | 1 / 13 / 4 | 5/17 = 29.4% | 17/17 = 100% |

Beautifulでは歌唱者集合だけを見ると分散していても、星の声を経由する曲が集中する。Swedenは同一アーティスト中心でも、観測経路の最初の分岐は比較的分散している。こうした指標は問題を調べる手掛かりであり、閾値で自動除外する規則にはしていない。既存Webのデータは今回の拡張へ自動切替していない。

個人データはGit管理外の `data/ytmusic/expansion-live-20261005-v01-path-audit-v02.json` に保存した。
