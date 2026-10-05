# 関係データ拡張の比較でIDとメタデータ群を固定する

`evaluate_ytmusic_batch.py`の通常評価は、入力に現れるPIDをソートして一時Genius IDを割り当てる。同じ集合からartist／album／songのgroup IDも生成する。曲や起点を追加して別々に評価すると、既存曲のIDやgroup IDまで変わり、推薦の変化が新しい関係によるものかを切り分けにくい。

`evaluate_graph_comparison(baseline_graphs, expanded_graphs, ...)`は、両入力のノードの和集合を同じメタデータと一時IDへ投影する。各側の関係BLOBには、その側で観測済みの出方向辺だけを入れる。新入力だけにある起点の出方向辺はbaselineへ入らず、baselineでは空の関係行になる。逆方向辺や推測した辺は追加しない。設定・artist preference・件数上限・比較する起点も揃える。標準MusicCoreは各起点の生成時に同じrandom seedを再設定する既存動作を使う。

比較対象の起点は既定でbaselineの全起点。`root_pids`またはCLIの`--root-pid`で減らせるが、両側で観測された起点だけを指定できる。結果は`baseline`／`expanded`の生成結果、到達曲数と生成順序の差分、共通mappingのハッシュと設定一致の`controls`を持つ。

```sh
.venv/bin/python scripts/evaluate_ytmusic_batch.py \
  --track-snapshot /path/to/tracks.json \
  --identity-map /path/to/identity-map.json \
  --baseline-observations-directory /path/to/before \
  --observations-directory /path/to/after \
  --genius-reference /path/to/genius-reference.sqlite \
  --executable /path/to/Music \
  --profile artist-album-minimum-one --artist-preference moderate \
  --output /path/to/new-comparison.json
```

manifest外の既存観測を明示追加する場合は各側の`--baseline-extra-observation`／`--extra-observation`を使う。cluster条件は両側へ同じものを適用する。既存CLIの単独評価は変更していない。

これは共通ノード集合を条件にした比較であり、以前のbaseline単独評価の再現ではない。baselineにも新ノードのメタデータは存在するため、以前の単独評価からの変化を全て「新辺の効果」と呼ばない。生成曲が各側の観測グラフで到達可能かも従来のpath／unreachable指標で区別する。曲の録音同一性、純正Musicの受け入れ、iPod互換性を証明する試験ではない。ネットワーク取得やMacへのアクセスは行わない。

テストでは、先にソートされる新PIDとartist／album群を追加して単独評価のID・metadataが変わる例を作り、比較時には両側で完全一致することを確認する。新起点と中間起点の出方向辺がbaselineへ混ざらないこと、同じ起点集合・要求順序で比べること、不正なsubsetをMusicCore起動前に拒否することも確認する。

既存観測を使う独立の直接MusicCore driverと、APIの3起点×新旧2条件を照合し、6プレイリストの全PID順序が一致した。共通空間での生成数はBeautifulが4→25、追想が9→25、Swedenが18→18。詳細はGit管理外の`data/ytmusic/expansion-live-20261005-v01-api-crosscheck.json`に保存する。この照合もMac GUIやiPodの試験ではなく、保存済みデータのオフラインエミュレーションである。
