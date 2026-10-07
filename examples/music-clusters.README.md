`music-clusters.example.json` の `source_library_sha256` を、自分のtrack snapshotの
`source_sha256` に置き換えてください。例のゼロSHAはそのままでは実Libraryに適用できません。
設定・分類結果には曲情報やPIDが含まれるため、個人用ファイルはgitignoredな `data/` に保存します。

タグは自由に追加でき、一曲に `アイマス` と `シャニマス` の両方を付けられます。
作品タグだけのルールではkindは変わりません。初期kindは `unknown` です。
例のCLANNADルールもタグだけを付けます。歌唱曲を含むアルバム全体をBGMとは扱いません。
`match` の条件はAND、同じリストの値はORです。正規表現は使いません。
`kind` は `vocal` / `bgm` / `off_vocal` / `unknown` から選びます。

BGMか歌唱曲かを自分で確認した曲だけ、`persistent_ids` 条件のルールか `overrides` で
個別にkindを指定します。次のPIDは形式例なので、自分のLibraryに存在するPIDへ置き換えてください。

```json
"overrides": {
  "0123456789ABCDEF": {"tags": ["ノベルゲー", "CLANNAD"], "kind": "bgm"},
  "FEDCBA9876543210": {"kind": "vocal"}
}
```

後のルールがkindを優先します。その後、曲名のOff Vocal / OffVocal / Karaoke / カラオケ表記を
`off_vocal` にします。PID overrideは最後に適用するので、明示的な個別訂正は可能です。
Instrumental表記だけでは `off_vocal` としません。overrideのtagsは置換で、`[]`ならタグを消します。

```sh
python3 scripts/music_clusters.py --track-snapshot data/my-tracks.json --config data/my-clusters.json --output data/my-classifications.json
```

出力先は新しいファイルにしてください。Library、Genius DB、認証情報には書き込みません。

純正形式の実験用Genius DBには、[複数範囲の設定例](native-cluster-policy.example.json)を
`prepare_native_cluster_filter.py --scope-policy` で渡せます。
分類設定とは別に、どのタグ・種類を選曲の境界にするかを指定します。
各scope内のタグはすべて一致する条件です。起点が複数scopeに属する場合、推薦曲もその全部を
満たす必要があります。たとえばシャニマス起点はアイマス＋シャニマスの範囲を保持します。
どのscopeにも属さない起点は、除外種類以外の通常の関係を使います。

kindは自分で指定済みの値を参照し、タグやアルバム名から歌唱／BGMを補いません。
Genius IDのある曲が一つも属さないscopeはエラーになるため、設定例は使う範囲に合わせて編集してください。
`--scope-policy` と単一範囲の `--cluster-tag`／`--kind`／`--exclude-kind` は併用できません。
Webの優先補充はこの形式へ保存していません。
