# 既存Genius IDを保持する増分計画

`scripts/plan_genius_update.py` はOpenGeniusのsynthetic rows専用の純粋API。`make_synthetic_state` で専用stateを作り、`plan_genius_update` に変更後の全有向グラフを渡す。ファイルやDBの書き換え、ネットワーク、Musicの操作は行わない。AppleのDBを一般的に取り込むインターフェースも持たない。

stateはproducer、Libraryと対応表のSHA、全曲メタデータと正規化ラベルのfingerprint、PID/GID、metadataと関係BLOBを保持する。自己ハッシュは破損確認用で、署名やApple由来データを識別する証明ではない。sourceやラベルの変化、重複PID/GID、不正ID、genre非0、groupとラベルの不整合、未割当IDへの辺を拒否する。

既存PIDのGIDとmetadataの全バイトを維持し、新PIDにだけ未使用uint32 GIDを割り当てる。既存のartist/album/songラベルは以前のgroup番号を使い、新ラベルは最大値の次を使う。変更後の関係は明示された全グラフだけから作り、省略された旧起点は空の出方向行になる。削除辺と順序変更をdeltaへ記録し、逆辺は発明しない。

## 実データでの計画確認

旧45起点から作ったstateの286 metadata行と関係行が、生成済み実験bundleを復号した実際のSQL行と完全一致することを確認した。追加8観測を含む53起点へ計画すると、旧286 GIDと全metadataを維持し、6曲だけ追加、合計292曲・969有向辺となった。変更行は新規割当を含め14行。計画は `data/ytmusic/incremental-plan-20261005.json` に保存している。

比較用の `data/comparison-only-expanded53-moderate-bundle-20261005/` は従来のゼロID入力から作った別コピーであり、この増分計画を適用したものではない。そのコピーでは既存268 IDが振り直されているため、生きたLibraryへの増分更新として扱わない。両状態1、SQL整合性、全2974曲の解析メタデータ保持、全53起点の標準expanded評価とのPID順一致は独立監査済みだが、新コピーのMusic/iPod受け入れは未確認。

8テストで旧ID/metadata保持、既存ラベル再利用、辺削除・順序差分、再計画の不変、source変更拒否、genre/group不整合、ID折返し・group上限、重複グラフの拒否を確認した。

## 独立bundleへ適用する経路

`scripts/update_genius_bundle.py` を追加した。最初の更新では `--baseline-evaluation` の全関係行からstateを作り、実際のSQL全metadata／関係行および全曲のPID/GID・解析メタデータが一致した場合だけ進む。次回は出力の `state.json` を `--baseline-state` に渡す。自己ハッシュやproducer名だけを信用してDBを変更しない。

```sh
.venv/bin/python scripts/update_genius_bundle.py \
  --bundle /path/to/source.musiclibrary \
  --track-snapshot /path/to/original-zero-id-tracks.json \
  --identity-map /path/to/identity-map.json \
  --baseline-evaluation /path/to/baseline-evaluation.json \
  --evaluation /path/to/expanded-evaluation.json \
  --executable /path/to/Music \
  --output /path/to/new-incremental-output
```

入力はsource hashを保った同じLibraryの観測拡張に限る。Libraryへ新しい曲自体を取り込んでsourceが変わった場合は拒否する。legacy codecの拒否も維持する。補助SQLデータが非空の場合は別のrebaseが必要なので拒否する。設定、空の補助テーブルとschema、鍵ヘッダー、両DBの保存状態、bundle内の他のファイルは保持する。設定のアーティスト評価はsourceの値を使い、このCLIでは変えない。

SQLiteの更新後に全テーブルの許可差分、integrity_check、再暗号化・再復号後の全論理行一致を確認する。入力全ファイルと出力全ファイルのSHAをレポートに記録し、保存直前に入力が変わっていないことも照合する。既存出力や入力内へは書き込まず、原本の置換、Musicの起動、ネットワーク取得を行わない。

4件の書き込みテストでconfig／旧metadata／schema保持、baseline不一致、非空補助データ、triggerによる無許可差分の拒否、元DBの不変、nested support file保持とsymlink拒否を確認した。

実データの `data/incremental-bundle-20261005-v01/` では旧286 GIDと全metadataを保持し、6新規割当・292曲・969辺へ更新できた。別の監査でsource/output SHA、全2974曲の解析メタデータ、config／補助テーブル、両状態1と鍵ヘッダー保持を確認した。保存された実SQLをMusic 1.7選曲コアで実行し、53起点・延べ1115曲が全て観測グラフで到達可能、全53列のPID順が比較用expanded評価と一致した。監査は同directoryの `independent-audit.json`。

出力stateを使って同じグラフを再適用した `data/incremental-bundle-repeat-20261005-v01/` では、追加IDゼロ・変更関係ゼロ、Library bytesとstate bytesは不変。Geniusの暗号化nonceは再生成するので、暗号文の一致を要求しない。

これでオフラインの更新コピー生成まで対応した。実Musicでの再読み込み・保存後の保持と、iPod同期／端末上のGenius生成は未確認。Webの既定データも切り替えていない。
