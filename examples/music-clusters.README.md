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
