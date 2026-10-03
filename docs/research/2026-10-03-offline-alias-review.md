# HDDなしでの別名照合と表記レビュー

## 入力と操作範囲

ユーザーが元データのHDDを取り外したため、保存済みの
`data/final-library-tracks.json`（2,974曲）とYTMusic観測を入力にして進めた。
HDDのバイナリ、音源、認証ファイルにアクセスせず、新規API問い合わせも行っていない。

`music_identity_map.py`に`--track-snapshot`を追加した。
元LibraryのSHA-256、PIDの形式・重複、テキスト情報、長さを検証する。
保存した曲一覧自体のSHAも出力し、元Libraryのハッシュはこの実行で
再検証していないことを`original_library_hash_verified_this_run=false`で明記する。
このモードは元Libraryのバイナリが存在することを要求しない。
元LibraryのSHAが記載されていることは、曲一覧の内容を元バイナリと
再比較して真正性を確認したという意味ではない。

## 確認待ちの別名をプレビュー

`--review-proposed`では、元の有効な対応候補とは別の欄に
`proposed_alias_candidates`を出力する。対応表の無効な曲名候補と、
YTMusic曲名に付加された和英併記候補を検討できる。
このオプションだけでは別名を有効にしない。

候補には元の曲名・アーティスト・アルバム・長さ、照合方法、
長さ差、アルバム一致の有無を記録する。
Instrumental / Off Vocal / Game Size / TV Size / live / remix / piano /
orchestralと年版の一般的な表記差を検出し、
別名照合でこれらが消える候補を拒否する。
これはすべての版の識別を保証するものではなく、原文と未確認の状態も保持する。
`REFRESH_RMX`のようなアンダースコア区切りも検出する。

以前の対応表で「ツバサグラビティ」の保存済み観測を照合したところ、
有効な候補31 video IDに加え、表記確認待ちの7 video IDが見つかった。

## 7曲分の表記を確認して追加

元の曲名と翻訳・ローマ字併記、アーティスト、長さ、版情報を確認し、
以下を明示的な曲名別名として新しい対応表に追加した。
録音が同じという確定はしていない。

| 元の曲名 | YTMusicの追加表記 |
| --- | --- |
| シャイノグラフィ -25 colors- | シャイノグラフィ -25 colors- - Shinography -25 colors- |
| スマイルシンフォニア | スマイルシンフォニア - SMILESINFONIA |
| プリズムフレア | プリズムフレア - Prism Flare |
| 愛なView | 愛なView - Ai na View |
| アルカテイル | アルカテイル - Alka Tale |
| カウントダウンラブ | カウントダウンラブ - COUNTDOWN LOVE |
| バベルシティ・グレイス (2023 Ver.) | バベルシティ・グレイス (2023 Ver.) - Babel City Grace (2023 Version) |

同じ曲名が複数のアルバムに存在するため、7 video IDに対して
11トラック分の表記別名を登録した。アルバムが違う候補を自動で1件に絞っていない。
元の未確認の曲名候補266件は引き続き無効で保持する。

表記判断は`--review-decisions`で適用できる。
判断JSONにはLibrary SHAと入力対応表のSHAが必要で、版の違う対応表への適用を拒否する。
PID・元曲名・別名・理由・根拠を記録し、元対応表を変更せず新しいファイルを作る。
根拠はYTMusicのvideo IDと元観測のSHA、長さ差、アルバム一致など。
ステータスは`reviewed_spelling_alias`で、録音同一性は`unverified`のまま。

出力:

- `data/final-library-title-review-decisions.json`: 11件の表記判断。
- `data/final-library-identity-map-reviewed.json`: 反映後の対応表。
- `data/final-library-matched-offline-reviewed.json`: 再照合結果。
- `data/final-library-title-review.md`: 7曲分の読みやすい比較表。

## 結果と残る課題

- 種曲以外で対応候補があるvideo ID: **31 → 38**。
- 別名照合側だけで新たに候補が出たvideo ID: **7**。
- 従来の厳密照合と別名照合を合わせてローカル候補が1件: **25 video ID**。
- ローカル候補が複数: **13 video ID**。
- 候補PIDの和集合: **55トラック**。
- 録音の同一性を確定した件数: **0**。

候補が1件でも同一録音が確認できたことにはならない。
従来の厳密照合には長さが欠けた観測も含まれ、別名照合は長さ必須という違いがある。
本番Library用のGenius DB生成や実機受け入れを完了した結果ではない。
次は複数収録・版の違い・複数アーティストを整理する。
音源の比較や本番バイナリへの対応ID保存はHDD再接続後の作業になる。

## 検証

全32テスト中28件成功。Musicバイナリを要求する4件は通常設定でスキップ。
追加した検証は、曲一覧JSONの不正情報の拒否、元バイナリにアクセスしないCLI実行、
プレビューの分離、表記レビュー後も未確認であること、元対応表の不変、
版表記を消す判断と異なる対応表版への判断適用の拒否。
サブエージェントによるソースレビューも実施した。
