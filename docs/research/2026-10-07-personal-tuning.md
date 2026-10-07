# 個人の好みを判断できる評価と、曲の同定を分ける

Music.appとiPodの動作確認について、利用者から別PCで実施済みとの報告を受けた。ここでの優先事項は曲の表記・版・分類と、本人が望む推薦への調整である。別PCのログと同期DBはこの作業環境にはまだ取り込んでおらず、個別オフライン実験の受け入れフラグはその実験自身の確認範囲を表す。

## 表記と版の保全

`music_identity_map.version_signature` はOff Vocal・karaoke・Instrumentalを分けた。従来は同じinstrumentalマーカーとなり、表記aliasのレビューでこれらを入れ替えても通る余地があった。現在の55有効aliasにこの取り違えがあったとは確認していない。55件すべてが新しい検査にも通る。

Game Size・TV Size・short/edit・live・remix・remaster・piano・orchestral・年の認識も保ち、表記aliasが認識済みの版情報を変えることを拒否する。版の詳細が同じとは確定しない。`version_facets` は元タイトルと、認識したマーカー・括弧内の未解釈修飾を保持する。全角括弧もNFKC後に検査する。未解釈の歌唱ユニットや版の表記から同一録音を推測しない。

`audit_library_identity.py` は全Libraryのalias、版情報、重複した正規化タイトル＋artist文字列を報告する。実2,974曲では有効alias55、保留265、重複138群・289レコードだった。Game Size79曲、TV Size10曲、live34曲、remix33曲などを検出した。Off Vocal254、karaoke24、Instrumental68という版マーカーは、BGMかどうかの確定値ではない。重複PIDは自動で統合せず、録音確認の主張は0件。

```sh
.venv/bin/python scripts/audit_library_identity.py \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --output data/new-personal-identity-audit.json
```

## 起点ごとの評価

推薦の曲行に ✓ と × を追加した。意味は「この起点に合う／合わない」であり、曲全体の好き嫌いやartist全体の評価ではない。同じボタンをもう一度押すと解除できる。×を付けたあとだけ理由を選べる。

- 雰囲気・好み
- 作品・範囲
- 繰り返し
- 版が違う
- 曲が違う

Library hash、起点PID、対象PID、判断、理由、推薦条件と入力のcontext hashを保存する。Libraryと対応表、曲のkindをこの操作で変更しない。未評価は肯定にも否定にも数えず、曲・版の指摘は通常の好みと別に集計する。起点自身は評価対象にしない。

評価は `serve_music_review.py --output-directory` 以下へ毎回新しい `taste-*.json` として保存する。解除・訂正の前のsnapshotも残る。保存が失敗するとセッション内の評価も更新しない。APIは画面にある推薦の対象とcontextだけを受け付け、古い画面からの書き込みを拒否する。分類用の「保存」は独立している。

再開時は `--feedback <保存したtaste-*.json>` を指定する。全snapshotを無差別に結合したり、閲覧やスキップから否定を推測したりしない。古い条件の評価は保持するが、画面では現在条件の評価と区別し、集計にはcontext不一致と過去の同じ起点・対象の判断を明記する。

保存済み評価をレビューするだけなら、以下のように起動できる。新しい推薦生成には従来のオフラインengine入力も必要である。

```sh
.venv/bin/python scripts/serve_music_review.py \
  --track-snapshot data/final-library-tracks.json \
  --config data/music-clusters-experiment-v01.json \
  --evaluation data/ytmusic/expansion-live-20261005-v01-expanded-evaluation.json \
  --output-directory data/my-taste-review
```

## 偏りを測る

`audit_personal_recommendations.py` は既存の観測経路監査と明示評価を合わせる。Library内の観測済み起点・参加曲・到達曲・出力曲の割合、起点を除く曲/artistの露出、版マーカー、明示分類、経由曲への集中を報告する。出現回数と異なるPIDの数を分ける。artist欠損は分母にも明記する。

実53起点・292参加曲・969関係の評価は、起点カバー率1.78%、出力250種類、延べ1,115曲だった。起点を除く1,062枠ではClean.Clean upとReflectionがそれぞれ30回出ていた。保存済み経路ではThe Limitの非起点24曲中22曲がHacking to the Gateを最初の経由曲に持つ。これは観測の構造上の集中で、曲の相性の良さや一意の原因を証明するものではない。

既存の分類設定では参加292曲中282曲がkind不明、10曲が明示Minecraft BGMだった。歌唱/BGM比率をこの状態から結論しない。未観測曲を「無関係」、未評価曲を「気に入った」とは扱わない。

```sh
.venv/bin/python scripts/audit_personal_recommendations.py \
  --evaluation data/ytmusic/expansion-live-20261005-v01-expanded-evaluation.json \
  --track-snapshot data/final-library-tracks.json \
  --identity-map data/final-library-identity-map-v04.json \
  --cluster-config data/music-clusters-experiment-v01.json \
  --output data/new-personal-recommendation-audit.json
```

`--feedback` を渡すと本人の評価を集計し、`--comparison` を渡すと共通起点で前後を比較する。条件が違う評価は現在条件での評価と過去の判断を別々に示す。自動のquality winnerや、本人が既に気に入ったという主張は作らない。結果を増やすこと、同artistを増やすこと、作品境界を守ることはそれぞれ別の評価軸である。

## 確認と次の調整

全316テスト確認（環境条件18件スキップ）。実v04の55aliasが維持され、元snapshot・対応表・Libraryへの書き込みは0件。ブラウザではSwedenの18曲で、非起点17曲だけに評価操作があること、保存・再読込・理由変更・解除を確認した。JavaScriptエラーは0件。ブラウザ操作はテスト評価で、最後に解除し、非空のテストsnapshotを `browser-smoke-only/` へ隔離した。本人の判断として集計・学習していない。

次に必要なのは本人の少数の具体例である。Superflyからアイマスへ広がるのをどこまで許すか、ゲームBGMは作品より雰囲気を優先するか、といった希望を確かめ、実際の起点・候補の評価と合わせる。現段階で新しい自動taste rerankerや、好みを反映したと称するネイティブDBは作っていない。これまでの同artist調整を土台に、明示評価を集めて候補・曲順・集中の変化を比べる。
