# 本番Libraryの表記と照合方針

ユーザーの依頼により、最終対象のLibraryを初めて読み取りで確認した。
元のファイル・メディア・タグへの変更はない。API問い合わせも行っていない。

対象:
`/mnt/temp-hdd/Final Target Apple Music Library/Music 1/Music Library.musiclibrary/Library.musicdb`

SHA-256: `cf83c06af944a3792d27373e24ce11c2ba91f94681fb5e601ffef76f4f48763a`

## 観測

- 2,974曲、アーティスト文字列表記356種類。
- 曲名に ` - ` があり、前側に日本語、後側にラテン文字を含む曲は266曲。
  これは和英併記候補の数で、翻訳か版表記かを自動判別した数ではない。
- 例: `瞳の覚醒 - HITOMINO KAKUSEI`、
  `恋クラゲ - Koi Kurage`、
  `羽のゆりかご (Instrument) - Hane no yurikago (Instrument)`。
- 前後の分割が危険な例:
  `スカイクラッドの観測者 -Instrumental- syphonic ver. - Skyclad no kansokusha`。
  後側だけでは前側に含まれるInstrumental等の版情報が消える。
- 日本語のユニット名と英語のブランド表記が混在し、
  複数アーティストの区切りも `、`、`,`、` & `、` / `などがある。
  `ANANT-GARDE EYES`や`VISUAL ARTS / Key`自体も存在するため、
  ハイフンやスラッシュを無条件に区切りとみなせない。

提供済みGeniused Libraryと、本番Library内の曲名・アルバムが正規化後に一致し、
長さの差が1秒以内で本番側候補が1曲の場合に、アーティスト表記差を集計した。
この結果は別名の根拠候補であり、同一録音やアーティスト同一性の確定ではない。

| 提供済みLibrary | 本番Library | 対応レコード数 |
| --- | --- | ---: |
| アンティーカ | L'Antica | 61 |
| イルミネーションスターズ | illumination STARS | 61 |
| アルストロメリア | ALSTROEMERIA | 60 |
| ストレイライト | Straylight | 54 |
| シャイニーカラーズ | SHINY COLORS | 53 |
| ノクチル | noctchill | 46 |
| シーズ | SHHis | 40 |
| コメティック | CoMETIK | 27 |

## 同じ推薦観測の本番Libraryへの照合

認証ありで取得した `data/ytmusic/tsubasa-authenticated-03.json`を使い、
現在のexact_title_artist_and_duration_within_3s_if_availableで再照合した。
種曲以外でローカル候補を持つvideo IDは、提供済みLibraryでは7、
本番Libraryでは31になった。

対象曲数と収録内容も異なるため、増加を英語表記だけの効果とはみなさない。
いずれも録音未確認の候補で、曲の対応が確定した数ではない。
この比較はデータ源としてYTMusicが本番Libraryにも利用できる見込みを示すが、
英語表記を削除すれば改善する、という証拠ではない。

## 採用する設計方針

表示用の曲名・アーティスト名はそのまま保持し、外部サービスとの照合用情報を
独立した対応表に保存する。ローカルの識別子はLibrary SHAとpersistent ID、
外部の識別子はサービス名とvideo ID / artist ID等とする。
異なるLibrary間でpersistent IDを同一曲として流用しない。

和英併記の分割はまず候補生成に使う。原文、どの規則で候補を作ったか、
版情報、長さ、アルバム差、複数候補の有無を残し、自動で録音確認済みにはしない。
Game Size / Instrumental / Off Vocal / remix / ライブ / 年版 / 歌唱者版などの
意味のある情報は保持する。

アーティストの和英別名は明示的な対応表で扱い、可能ならYTMusicのartist IDを
根拠として付ける。機械的なローマ字化だけではL'AnticaやSHHis等を扱えない。
複数クレジットの分解も、名前中の区切りと区別できる規則に限定する。

今後のYTMusicとの照合評価は本番Libraryを基準とする。
元のGeniused LibraryはGenius形式と選曲処理の解析サンプルとして引き続き使う。

詳細な抽出・別名候補はignoredな以下のファイルに保存した:

- `data/final-library-tracks.json`
- `data/final-library-identity-audit.json`
